# Memory, objectives, monitors, and scheduled automation manifests  `stage-3.5`

This stage is shared behind-the-scenes support. It is made of extension manifests, which are like registration forms that tell the host system what extra parts to load and when to run them. Together, they give the assistant long-term context, reminders, automatic checks, scheduled work, and evaluation loops.

The memory manifest wires in tools and background jobs for stored memories. It lets other parts of the system search, update, recall, index, and summarize information the assistant has saved. The objectives manifest keeps active goals visible. At the start of a conversation turn, it reminds the agent of the current plan, unfinished steps, and conditions that still need to be met.

The monitors manifest registers monitor objects and a recurring checker, so the system can revisit watched conditions when they are due. The scheduled-tasks manifest registers task objects, a wait tool, background jobs, and the skill for creating future work. The self-improvement manifest adds a scheduled evaluation job, so the system can periodically review itself and learn from results.

## Files in this stage

### Durable memory services
Registers the memory extension’s searchable stores, tools, hooks, jobs, and services for recall, indexing, updating, and summarization.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, tool calls, prompt hooks, page-change hooks, and scheduled background jobs`

This file is like the front desk and schedule board for the memory system. It tells the larger UFO platform what the memory extension offers and when each piece should run. The extension gives the agent two tools: one to search durable memory and source snippets, and one to write a new durable memory item. It also listens for new user prompts so it can quietly add relevant remembered facts to the model's context before the model answers.

The file also connects memory to background maintenance. When source pages change, one hook indexes those pages so they can be searched, while another hook asks a model to derive durable facts from them. Scheduled jobs later index newly committed memory items and consolidate older facts into broader summaries, so memory does not grow into a pile of tiny repeated notes.

A key behavior is that automatic recall is deliberately best-effort. The prompt hook has a short timeout and catches errors, because failing to recall memory should not block a user's turn. In contrast, indexing and fact derivation fail loudly when required backends are missing, because silently skipping that work would lose important data.

#### Function details

##### `_date_bound`  (lines 145–156)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: This helper turns an optional ISO date or datetime string into a timezone-aware UTC datetime bound for memory searches. It makes an end date written as just a day, such as "2026-01-31", include that whole day by moving the exclusive end to the next midnight.

**Data flow**: It receives a string or nothing, plus a flag saying whether this is an end bound. If there is no string, it returns nothing. If there is a string, it parses it as an ISO date or datetime, adds UTC when no timezone was given, optionally moves a bare end date forward by one day, and returns the resulting datetime.

**Call relations**: The memory search tool calls this before searching so user-supplied start_date and end_date values become real time limits. If parsing fails, the error is allowed to surface as a recoverable tool error instead of being hidden.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 165–243)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the shared search workflow for memory. It searches both stored memory items and indexed source-page snippets for up to three focused queries, then merges the results fairly so one query does not crowd out the others.

**Data flow**: It receives search phrases, a source reader that describes who is allowed to read what, and optional start and end dates. It asks the memory store to run recall searches and source searches in parallel, interleaves the per-query results, removes duplicates, converts each result into a common MemoryMatch shape, and returns those matches. Stored memories become memory references, while source snippets become page references.

**Call relations**: The memory_search tool uses this service when the agent explicitly searches memory. The manifest also exposes this same service as the default memory search provider, so other extensions can depend on the same behavior instead of inventing their own search path.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 245–248)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This reports which classes of memory items can be listed or filtered. It reads the allowed item classes from the shared type definition so the list stays in sync if new classes are added.

**Data flow**: It takes no outside data beyond the declared ItemClass type. It extracts the valid literal values from that type and returns them as a tuple of strings.

**Call relations**: This belongs to the search provider service. Consumers that browse or filter memory can ask it what memory kinds are available instead of keeping a separate hard-coded list.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 250–299)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This returns a paged list of recent live memory items for subjects the caller is allowed to read. It is for browsing memory directly, not similarity search.

**Data flow**: It receives a set of readable subjects, a page size, an optional filter for item classes, and an optional cursor that says where the previous page ended. It builds a database query for non-superseded memory rows in the current workspace, applies the filter and shared paging rules, reads the rows inside a transaction, and converts each row into a MemoryMatch.

**Call relations**: This is the browse side of the memory search provider. Unlike search, it does not include source pages, because listing every synced page would be a document dump rather than a useful memory list.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 302–309)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: This formats one memory search result into a short line of text the agent can read. It includes the kind, snippet, and, when available, a durable object reference and date.

**Data flow**: It receives one MemoryMatch. It starts with the result kind and text, then adds the reference and created date if those fields exist, and returns a single display string.

**Call relations**: The memory_search tool calls this for each returned match when building the text shown to the model. The reference it prints can later be opened with object_get to inspect the full memory item or page.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 312–332)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing memory_search tool. It lets the agent ask focused questions about remembered facts and source documents, optionally within a date range.

**Data flow**: It receives the tool context and validated tool arguments. It checks that extension context is present, parses date bounds, creates a source reader for the caller's allowed audience, runs MemorySearchService.search, and returns a ToolResult containing either "No matching memory." or formatted result lines.

**Call relations**: The manifest registers this as the handler for the memory_search tool. During a tool call, it sits between the model's request and the underlying memory store, translating user-friendly tool arguments into the shared search workflow and then formatting the answer back for the model.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 335–349)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: This is the implementation of the agent-facing memory_update tool. It records a persistent fact, preference, decision, event, or task-like memory for the current audience.

**Data flow**: It receives the tool context and validated memory fields. It checks that extension context is present, chooses the subject from the current effective audience, wraps the body and metadata in a MemoryWrite object, commits it to the memory store, and returns a confirmation message naming the subject.

**Call relations**: The manifest registers this as the handler for the memory_update tool. The agent uses it when it learns something durable about the user or shared conversation, and the store later makes that item available to recall, search, indexing, and consolidation.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 352–394)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook automatically adds relevant memory to a turn before the model responds. It is intentionally safe and best-effort: if recall is slow or broken, the user prompt still goes through.

**Data flow**: It receives a hook context. If the event is not a user prompt submission or there is no active turn, it returns nothing. Otherwise it computes readable memory subjects, builds a source reader, tries to recall relevant memories under a short timeout, logs what happened, filters out topic-only recall items, and returns an InjectContext containing bullet-point memories if any useful ones were found. On errors or timeouts, it logs and returns nothing.

**Call relations**: The manifest registers this for the user_prompt_submit event. It runs before the model's answer, drawing on the memory store and handing back extra context only when it can do so safely within the hook deadline.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 397–406)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job indexes committed memory items that do not yet have embeddings. An embedding is a numeric representation of text that lets similar meanings be found during search.

**Data flow**: It receives the extension context. It verifies that both the search index and embedding backend are available, creates a MemoryIndexer with the index, embedding service, transaction function, text chunker, and page-state tracking, then runs it. The job updates indexing state through that indexer.

**Call relations**: The manifest registers this as the memory_index background job. Candidate workspace selection is supplied by _items_awaiting_index, and when the job runs, this function hands the actual indexing work to MemoryIndexer.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 409–425)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: This page-change hook indexes changed source pages and mirrors them into the memory extension's page table. That makes source documents searchable alongside remembered facts.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it ignores it. Otherwise it checks that index and embedding backends are wired, creates a PageIndexer with the needed services and workspace information, applies the delivered page changes, and returns nothing.

**Call relations**: The manifest registers this as one of two page_change consumers. The core runner owns the page-change cursor and delivers batches; this function processes each batch for search indexing while derive_facts separately processes page changes for fact extraction.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 428–439)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: This page-change hook turns changed source pages into durable fact memories using a model. It keeps memory useful by distilling long or changing documents into smaller remembered facts.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it returns nothing. If no model backend is available, it raises an error so the page-change cursor does not advance past unprocessed pages. Otherwise it creates a FactDeriver from the memory store and model, applies it to the page changes, and returns nothing.

**Call relations**: The manifest registers this as the second page_change consumer, independent from page indexing. It runs when source pages change and hands the batch to FactDeriver, which writes replacement facts and retires older page-derived facts as needed.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 442–450)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job merges clusters of older facts into higher-level semantic summaries. It helps keep memory compact and less repetitive over time.

**Data flow**: It receives the extension context. It verifies that the embedding backend exists, builds a MemoryConsolidator with embedding, database transaction access, workspace id, and the optional model backend, then runs it. The consolidator performs the actual clustering and superseding of original facts.

**Call relations**: The manifest registers this as the memory_consolidate background job. Candidate workspace selection is supplied by _consolidatable_workspaces so the job only runs where there are enough old facts to make consolidation worthwhile.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 453–458)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query used to find workspaces that have memory items waiting to be indexed. It prevents the indexing job from running for workspaces with nothing to do.

**Data flow**: It takes no runtime arguments. It creates a SQL query selecting distinct workspace ids from memory items whose embedding digest is missing, and returns that query for the job scheduler to use.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_index job. The scheduler uses it to decide which workspace owners should receive an indexing run.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 461–477)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query used to find workspaces where consolidation could actually produce a useful summary. It looks for enough old, live facts to form a cluster.

**Data flow**: It computes a cutoff time by subtracting the minimum required age from the current UTC time. It then creates a SQL query for workspaces with at least the minimum number of non-superseded, non-page-derived fact items older than that cutoff, and returns the query.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_consolidate job. This keeps the hourly job from being scheduled for workspaces whose facts are too few, too new, or already superseded.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 480–554)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete declaration of the memory extension. It tells the platform which tools, objects, hooks, jobs, search provider, and UI surface belong to this extension.

**Data flow**: It takes no arguments. It constructs tool definitions for memory_search and memory_update, registers the memory object type, wires prompt and page-change hooks, defines scheduled jobs and their candidate selectors, exposes MemorySearchService as the default memory search provider, registers the memory surface routes, and returns a Manifest object containing all of that.

**Call relations**: The platform calls this during extension loading. Everything else in the file is made reachable through the structure this function returns: tool calls reach the handlers, events reach the hooks, schedules reach the jobs, and dependent extensions can build the memory search provider.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### Active monitoring and objectives
Registers monitor objects and due-check jobs alongside objective reminders that keep active plans visible during agent turns.

### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `startup registration, then recurring scheduled checks`

This file exists so the rest of the application can discover and run the monitors feature without hard-coding it elsewhere. A monitor is a saved watch that periodically runs a shell probe inside a conversation’s sandbox. Think of it like a smoke alarm that checks on a schedule: it only needs attention when its next check is due.

The file names the extension, gives it a version, and defines one scheduled job called `monitor_runner`. The schedule string means the job is offered every minute. When the job fires, it calls a small helper, `_probe`, which creates a `MonitorRunner` and asks it to do the actual checking.

The `manifest` function packages all of this into a `Manifest`, which is the system’s standard way of saying, “Here are the tools, object kinds, and background jobs this extension provides.” It also supplies `due_monitor_workspaces()` as the job’s candidate source. That matters because the runner only needs to consider workspaces where monitor work is actually due. Workspaces with no armed monitors do not waste dispatcher effort.

Without this file, the monitor tool and object kind would not be registered, and the recurring runner would not be scheduled.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job’s entry function. When the clock says monitors should be checked, it creates a `MonitorRunner` and tells it to run the due probes.

**Data flow**: It receives an `ExtensionContext`, which is the system-provided bundle of information and services the extension needs while running. It passes that context into `MonitorRunner`, then awaits the runner’s work. It returns nothing directly; the useful effects happen through the runner, such as checking monitors and updating their stored state.

**Call relations**: The job declared in `manifest` uses `_probe` as its handler. `_probe` does not decide which workspaces are due; that filtering is set up separately through `due_monitor_workspaces()`. Its job is simply to hand the extension context to `MonitorRunner` at the moment the scheduled job runs.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the official declaration for the monitors extension. The host application calls it to learn what this extension contributes: its tool, object kind, and scheduled runner job.

**Data flow**: It starts from constants in this file, such as the extension name, version, job name, and schedule. It includes the imported monitor tool and monitor object definition. It asks `due_monitor_workspaces()` for the list or query that identifies where monitor work is due, wraps the scheduled behavior in a `JobSpec`, and returns a complete `Manifest` object.

**Call relations**: This is the main registration point for the file. During extension loading, the application calls `manifest`, which constructs a `JobSpec` for the recurring monitor runner and places it inside a `Manifest`. The job points to `_probe` for the actual run step and to `due_monitor_workspaces()` so the dispatcher only considers workspaces that have due monitor checks.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `startup registration, then user prompt submission`

This file is the front door for the objectives extension. An objective is durable work that can last across turns, handoffs, or scheduled wake-ups. The main problem it solves is memory loss between turns: an agent may wake up later with fresh context, so it needs the important objective state placed back in front of it.

The file does two things. First, it defines a prompt section that teaches the agent when to create an objective, what counts as a meaningful step, and why completion conditions must be real checks rather than self-made markers. This is like leaving written operating instructions next to a shared workbench.

Second, it defines a hook that runs when a user prompt is submitted. If the current conversation has an objective, the hook reads the saved objective record and injects a compact “frontier” into the agent’s context: the objective name, directive, progress counts, open steps, conditions still needed, and any question already raised with the user. This prevents the agent from repeating questions or forgetting unfinished work.

Finally, the manifest function packages the extension’s tools, hook, and prompt guidance so the host application can load them together.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function adds a summary of the active objective into the agent’s next turn, if the conversation has one. It exists so the agent does not have to rely on short-term memory after a wake-up, delegation, or new prompt.

**Data flow**: It receives hook context from the host system. If there is no current turn, it does nothing. Otherwise, it opens an extension database transaction, uses the current agent’s workspace to look up any objective tied to the conversation, and stops if none exists. If an objective is found, it records a metric, builds a readable text block showing progress and the current unfinished frontier, and returns an InjectContext containing that text. The returned text becomes extra context for the agent; the stored objective itself is only read, not changed.

**Call relations**: This function is registered as the handler for the user_prompt_submit hook by manifest. When that hook fires, it asks agent_current for the workspace, creates an Objectives reader to fetch the conversation’s objective view, uses condition_summary to turn each acceptance condition into plain text, emits a metric showing that the frontier was injected, and finally hands the finished text to InjectContext so the host can place it into the turn.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the objectives extension to the UFO host application. It tells the host the extension’s name and version, which tools it provides, which hook should run, and what guidance should be added to the agent prompt.

**Data flow**: It takes no input from the caller. It gathers the module’s constants, tool definitions, hook definition, and prompt section text, then returns a Manifest object. The result is a packaged description of everything the host needs in order to enable the extension.

**Call relations**: This is the loading point for the file. During extension setup, the host calls manifest to receive a Manifest. Inside that package, HookSpec connects the user_prompt_submit event to _inject_frontier, and PromptSection carries the long instruction text that teaches the agent how to use objectives. The tool constants are included so the agent can plan objectives, run independent steps, record step attempts, and read objective state.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Scheduled automation jobs
Registers scheduled-task infrastructure and recurring self-improvement evaluation work that runs automatically over time.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension load and recurring job dispatch`

This file exists so the scheduled-tasks extension can introduce itself to the larger UFO system in one clear place. Without it, the system would not know that this extension provides a tool for pausing a conversation until later, an object type for scheduled tasks, or two clock-based background jobs that wake up due work.

The important idea is that there are two different kinds of waiting. One is a scheduled task that should run at a planned time. The other is a paused conversation that should resume after a delay. This file keeps them separate by declaring two recurring jobs. Both jobs run on the same frequent schedule, but each looks only for workspaces that actually have due rows in its own table. That is like a delivery driver checking only the houses with packages waiting, instead of driving down every street.

When a scheduled-task job fires, it creates a ScheduledTaskRunner and lets it do the real work. When a pause-resume job fires, it creates a PauseRunner instead. The file also declares the skill files the agent should load before scheduling tasks, and it says this extension depends on memory search. It does not implement the scheduling rules itself; it wires the extension’s pieces into the platform.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job callback used when the scheduled-task background job fires. It creates the runner that knows how to find and invoke scheduled tasks that are due.

**Data flow**: It receives an ExtensionContext, which is the platform’s bundle of services and current extension environment. It gives that context to a ScheduledTaskRunner, then asks the runner to run. Nothing meaningful is returned; the effect is that due scheduled-task work is processed.

**Call relations**: The manifest registers this function as the handler for the scheduled-task runner job. When the platform’s clock says the job should run, the platform calls this function, and this function hands control to ScheduledTaskRunner to do the actual scheduled-task work.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job callback used when the pause-resume background job fires. It creates the runner that knows how to resume conversations whose pause time has arrived.

**Data flow**: It receives an ExtensionContext from the platform. It passes that context into a PauseRunner, then asks the runner to run. It does not return a value; the visible result is that due paused conversations may be resumed.

**Call relations**: The manifest registers this function as the handler for the pause runner job. When the recurring pause job fires, the platform calls this function, and it delegates the real resume work to PauseRunner.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This builds the Manifest, which is the extension’s official registration form for the host system. It declares the extension name, version, tools, object kinds, background jobs, skills, dependencies, and conversation storage slot.

**Data flow**: It starts from constants in this file and imported extension pieces such as the pause tool, scheduled-task object, due-workspace selectors, and skill folder paths. It packages them into JobSpec, SkillSpec, and Manifest objects. The returned Manifest is what the platform reads to know how to install and run this extension.

**Call relations**: The host system calls this during extension loading. Inside it, the file asks the schedule and pause modules for candidate workspace selectors, builds two job definitions around _run and _resume, creates skill entries from the skill names, and returns the completed Manifest to the platform.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup registration, then scheduled job execution`

This file is the extension’s sign-up sheet. Without it, the wider system would not know the self-improvement extension’s name, version, or scheduled job, so its periodic evaluation loop would never be registered or run.

The file declares a single cron-style job, meaning a job that runs on a clock schedule rather than in response to a user action or a data change. Here, the schedule is set to run once an hour at minute zero. When the clock triggers the job, the system calls `_tick`.

`_tick` is the small bridge between the scheduler and the actual self-improvement work. First it checks that model access is available, because this extension needs a language model to propose prompt changes, replay behavior, and judge results. If no model is wired in, it stops with a clear error instead of silently doing nothing. Then it wraps the model in `ModelAccessLeg`, which gives the proposer and evaluator a shared, controlled way to use it. Finally it builds an `ImproveCron` object with two main parts: a `PromptProposer`, which suggests improvements, and a `CandidateEvaluation`, which tests and judges those suggestions. Think of this file like a calendar entry plus the phone number to call when the reminder goes off.

#### Function details

##### `_tick`  (lines 21–29)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the function the scheduler runs when the self-improvement job fires. It prepares model-backed proposing and evaluation, then starts the improvement cycle.

**Data flow**: It receives an `ExtensionContext`, which is the runtime bundle of services available to the extension. It reads `ctx.model`; if that model access is missing, it raises an error. If the model is present, it wraps it in `ModelAccessLeg`, gives that wrapper to `PromptProposer` and `CandidateEvaluation`, creates an `ImproveCron`, and awaits its `run` method. The main output is the side effect of running the improvement job; the function itself returns nothing.

**Call relations**: The scheduled job declared by `manifest` points to `_tick` as its handler. When the scheduler calls `_tick`, it creates the objects that do the real work: `ModelAccessLeg` for controlled model use, `PromptProposer` for generating candidates, `CandidateEvaluation` for replaying and judging them, and `ImproveCron` to coordinate the whole pass.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 32–44)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It provides the extension name, version, and the scheduled job that should be registered.

**Data flow**: It takes no input. It builds a `JobSpec` with the job name, the hourly cron schedule, `_tick` as the function to run, and the set of trajectory workspaces that can be candidates for the job. It then places that job inside a `Manifest` and returns it to the host system.

**Call relations**: The host system calls `manifest` when loading the extension. Inside, it asks `trajectory_workspaces` for the workspace candidates the scheduled job should apply to, creates a `JobSpec`, and wraps that in a `Manifest`. Later, the scheduler uses that job specification to call `_tick` on the configured schedule.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).
