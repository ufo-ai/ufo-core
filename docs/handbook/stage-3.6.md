# Platform service, object, and surface extension manifests  `stage-3.6`

This stage is mostly startup wiring. It is like the labels and plug shapes on add-on parts: the code for each extension may exist elsewhere, but these files tell the main UFO platform what can be loaded and used. The small __init__.py files for app artifacts, UFO, and web simply mark folders as importable Python packages, so other code can find them.

The manifest files are the real registration cards. The debugger manifest adds a debugging tool and its web view. The memory manifest connects long-term memory to tools, automatic recall, search, indexing, cleanup jobs, object types, and a user surface. The monitors manifest registers monitor objects, actions, and a recurring checker. The objectives manifest adds tools and prompt context so the agent keeps track of ongoing goals. Report digest and scheduled tasks declare their object types, tools, writing or skill files, and background jobs. The UFO manifest exposes the live shell surface. The web manifest mounts the browser portal, its tools, jobs, and feature switches.

## Files in this stage

### Package setup
Initial package marker files make extension code importable before manifests register behavior.

### `extensions/app_artifacts/ufo_ext_app_artifacts/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. That matters because other code can then refer to modules inside `ufo_ext_app_artifacts` using normal Python import paths.

There is no setup code, no exported helper, and no runtime action here. Its value is structural: it makes the extension's package layout visible to Python tooling and to the rest of the application. You can think of it like a label on a drawer. The label does not contain the tools, but it tells the system that the drawer exists and can be opened.

Without this file, depending on the Python version and packaging setup, imports from this extension folder could become less reliable or fail in environments that expect traditional packages.


### Interactive platform services
These manifests register developer tooling, memory, monitoring, and objective context that shape live agent behavior.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This file is the debugger extension’s “label on the box.” When the UFO system loads extensions, it needs a standard description of what each extension offers. This manifest says: the extension is named “debugger,” it is version “0.1.0,” it provides a `report_problem` tool, and it exposes a debug surface made of predefined routes.

A surface is a web-facing area of the product, like a small control panel. This one is only attached with an identity resolver called `resolve_operator_workspace`, which means the system must connect the visitor to an operator workspace before the surface is usable. In plain terms, the debug UI is not meant to be generally available; it is mounted through the core system in a controlled way.

The file does not implement the debugger itself. Instead, it imports the actual route definitions and tool definition from nearby debugger modules, then packages them into a `Manifest` object. Without this file, the host system would not know that the debugger extension exists, what version it is, what tool to register, or how to expose its debug surface.

#### Function details

##### `manifest`  (lines 16–24)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the structured description the host system uses to register the extension. Someone would use this when the application is discovering available extensions and needs to know what this one provides.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the report-problem tool definition, the debug surface name, the route list, and the workspace identity resolver. It puts those into a new `SurfaceSpec`, then places that surface and the tool into a new `Manifest`. The result is a complete manifest object that the host can read and register.

**Call relations**: During extension loading, the host calls this function to ask the debugger extension to describe itself. The function creates a `SurfaceSpec` for the debug web surface, then creates a `Manifest` that includes that surface and the reporting tool. It hands this manifest back to the host so the host can wire the debugger extension into the larger system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, prompt handling, page-change handling, scheduled jobs`

This file is the memory extension’s control panel. Without it, the system might still have memory storage code elsewhere, but nothing would expose it as agent tools, run it on page changes, refresh it in the background, or inject useful memories into a model prompt.

It defines the inputs for memory tools, such as searching memory, recording a new memory, correcting an old one, and rebuilding facts derived from synced pages. It also defines the tool handlers that turn those requests into reads and writes against the memory store.

A key part is automatic recall. When a user submits a prompt, the recall hook looks for relevant stored memories and adds a short “Relevant memory” section before the model runs. This is best effort: if recall is slow or broken, the conversation continues rather than failing.

The file also wires page-change hooks. One hook indexes changed pages so they can be searched. Another uses a model to turn page text into durable fact rows. Scheduled jobs then keep memory healthy: indexing new items, merging old facts into summaries, retiring duplicates, writing wiki-like section and overview paragraphs, updating member profiles, and curating whole memory pages.

The final `manifest` function packages all of this into the extension declaration the host system loads.

#### Function details

##### `_date_bound`  (lines 254–265)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: This helper turns an optional date string from a memory search into a real time boundary. It lets users search memory within a date window without needing to provide a full timestamp.

**Data flow**: It receives a string such as `2026-01-31` or a full ISO date-time, plus a flag saying whether this is the end of the range. If the value is missing, it returns nothing. If it is a bare end date, it moves the boundary to the next midnight so that the named day is included. The result is a timezone-aware `datetime` in UTC.

**Call relations**: The memory search tool calls this before searching. It prepares the start and end times that are passed into the shared memory search workflow.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 274–352)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the shared search workflow for memory. It searches both stored memory items and synced source-page snippets, then merges the results into one list the agent can use.

**Data flow**: It receives one to three focused query strings, a source reader that says whose memory and sources may be read, and optional date bounds. It asks the memory store to recall matching memory items and to search source documents, running those lookups in parallel. It then removes duplicates, interleaves results fairly across queries, wraps them as `MemoryMatch` objects, and returns the combined matches.

**Call relations**: The `memory_search_handler` uses this when the agent calls the memory search tool. The manifest also registers this class as the extension’s memory search provider, so other parts of the system can use the same search behavior.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 354–357)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This reports which classes of memory items can be listed. It keeps the listing filter in sync with the memory item type definition.

**Data flow**: It reads the allowed item classes from the `ItemClass` type and returns them as a tuple of strings. It does not touch storage or change anything.

**Call relations**: This belongs to the search provider interface. Consumers that browse memory can ask it what kinds of items are valid instead of maintaining their own separate list.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 359–409)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This returns a page of recent memory items for selected subjects, newest first. It is the browse view of memory: no query, just the latest live rows the viewer is allowed to read.

**Data flow**: It receives subjects, a limit, optional item-kind filters, and an optional paging cursor. It builds a database query for non-retired, non-superseded memory items in the current workspace, applies the cursor and limit, reads rows in a transaction, and turns each row into a `MemoryMatch`. It returns a `ListingPage`, which includes the results and paging information.

**Call relations**: This supports memory listing through the provider interface. It uses the shared listing helpers so memory pages behave like other paged lists in the system.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 412–419)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: This formats one memory search hit as a short text line for the agent. It includes the snippet, its kind, and, when available, a reference that can be opened later.

**Data flow**: It receives a `MemoryMatch`. It builds a line like a bullet point, adds the object reference and date if present, and returns the final string.

**Call relations**: The memory search tool handler calls this for each match before returning the search results to the agent.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 422–442)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: This is the implementation of the `memory_search` tool. It lets the agent look up relevant facts, notes, and source-document snippets before answering.

**Data flow**: It receives the tool context and validated search arguments. It parses optional date bounds, builds a source reader from the tool context, runs `MemorySearchService.search`, and formats the matches. It returns a tool result saying either that nothing matched or listing the matching memory and page references.

**Call relations**: The manifest registers this as the handler for the `memory_search` tool. It relies on `_date_bound`, `MemorySearchService.search`, and `match_line` to do the actual work.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 445–459)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: This is the implementation of the `memory_update` tool. It records a durable memory item so later conversations can recall it.

**Data flow**: It receives the tool context and the memory item fields, including text, class, kind, confidence, and optional source reference. It chooses the current effective audience as the subject, writes a `MemoryWrite` to the memory store, and returns a confirmation message.

**Call relations**: The manifest registers this as a side-effecting tool because it changes stored memory. Agents call it when they learn something lasting that should be remembered.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_correction_handler`  (lines 462–480)

```
async def record_correction_handler(ctx: ToolContext, args: RecordCorrectionInput) -> ToolResult
```

**Purpose**: This records a corrected version of an existing memory item. It does not edit the old row directly; it writes a new fact that points back to the item being corrected.

**Data flow**: It receives the current tool context and a correction containing the old memory item ID and replacement text. It writes a new fact under the speaker’s current audience, using a source reference that says which memory it corrects. It returns a confirmation message.

**Call relations**: The manifest registers this for the memory view’s “Record edit” action. Later deduplication can retire the older near-duplicate in favor of this newer statement.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_first_run_handler`  (lines 483–499)

```
async def record_first_run_handler(ctx: ToolContext, args: RecordFirstRunInput) -> ToolResult
```

**Purpose**: This records the first-run setup memory, such as what tools the team uses. It gives future turns a stable fact to recall from the workspace’s onboarding choices.

**Data flow**: It receives the tool context and one short statement. It writes that statement as a fact under the current effective audience with a source reference marking it as first-run information. It returns a confirmation message.

**Call relations**: The manifest registers this as a side-effecting first-run action. It uses the same memory write path as normal memory recording, but with fixed metadata.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 502–584)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: This automatically adds relevant memory to a user’s prompt before the model responds. It is like slipping a short reminder note onto the model’s desk, but only when the note can be found quickly and safely.

**Data flow**: It receives a hook context and checks that the event is a submitted user prompt with a live turn. It skips certain internal machine-only root turns. Otherwise it computes readable subjects, searches memory with a short timeout, filters out topic-only recalls, truncates long items, keeps the total injected text under a fixed budget, logs what happened, and returns an `InjectContext` containing the memory text. If recall fails, it logs the problem and returns nothing.

**Call relations**: The manifest registers this for the `user_prompt_submit` event as best effort. The hook chain calls it before the model runs; it calls into the memory store and hands back optional context injection.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 587–596)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job indexes newly committed memory items so they can be searched by meaning. Indexing here means turning text into searchable chunks and embeddings, which are numeric fingerprints of meaning.

**Data flow**: It receives an extension context with index and embedding backends. If those backends are missing, it fails loudly. Otherwise it builds a `MemoryIndexer` with the index, embedder, transaction function, chunker, and page state storage, then runs it.

**Call relations**: The manifest schedules this as the `memory_index` job. The job runner calls it for workspaces that have memory items still waiting for embeddings.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 599–615)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: This page-change hook indexes synced source pages when they change. That makes page text available to memory search alongside explicitly recorded memory.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. Otherwise it checks that index and embedding services exist, builds a `PageIndexer`, and applies the changed pages from the batch. It returns no hook output.

**Call relations**: The manifest registers this as one consumer of `page_change` events. The core runner delivers batches of changed pages, and this hook turns them into searchable index chunks and mirror records.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 618–629)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: This page-change hook reads changed source pages and derives durable fact memory from them. It lets synced documents become concise memory rows that can be recalled later.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it returns nothing. If no model is wired, it raises an error so the cursor does not skip pages. Otherwise it builds a `FactDeriver` with the memory store and model, then applies it to the changed pages.

**Call relations**: The manifest registers this as a second `page_change` consumer, separate from page indexing. It advances on its own cursor so fact derivation and indexing can replay page changes independently.

*Call graph*: 2 external calls (__init__, store_for).


##### `rebuild_page_facts_handler`  (lines 641–657)

```
async def rebuild_page_facts_handler(ctx: ToolContext, args: RebuildPageFactsInput) -> ToolResult
```

**Purpose**: This implements the admin tool that queues all synced pages for fact derivation again. It is used when page-derived memory reads badly and needs to be rebuilt from the original pages.

**Data flow**: It receives the tool context and empty input. It verifies that an extension context exists and that the speaker is a workspace admin. Then it deletes the stored cursor for the fact-derivation hook. That does not rewrite facts immediately; it makes the next derivation pass start over. It returns a message saying the rebuild is queued.

**Call relations**: The manifest registers this as the `rebuild_page_facts` tool. It works by resetting the cursor that `derive_facts` uses, so the existing page-change machinery performs the rebuild safely.

*Call graph*: calls 1 internal fn (speaker_is_admin); 2 external calls (__init__, __init__).


##### `consolidate_memory`  (lines 660–668)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job groups older related facts into higher-level summaries. It helps memory stay useful instead of becoming a long pile of repeated small notes.

**Data flow**: It receives an extension context and requires an embedding backend. It builds a `MemoryConsolidator` with the embedder, transaction function, workspace ID, and model, then runs it. The consolidator is responsible for writing summaries and superseding originals.

**Call relations**: The manifest schedules this as the hourly consolidation job. Its candidate query only selects workspaces with enough old live facts to make consolidation worthwhile.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 671–679)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job retires duplicate memory rows. It keeps the newest useful copy and reduces clutter from repeated facts.

**Data flow**: It receives an extension context and requires an embedding backend. It builds a `MemoryDeduper` with the embedder, transaction function, workspace ID, and store, then runs it. The detailed duplicate detection and retirement happen inside the deduper.

**Call relations**: The manifest schedules this as the deduplication job. Its candidate query selects workspaces that appear to have enough old repeated rows for a sweep to matter.

*Call graph*: 1 external calls (__init__).


##### `write_memory_sections`  (lines 682–687)

```
async def write_memory_sections(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job writes or rewrites section paragraphs for memory pages. These paragraphs summarize the facts under a subject-and-kind band, like a short heading introduction in a wiki.

**Data flow**: It receives an extension context, builds a `SectionWriter` with the transaction function, workspace ID, and model, then runs it. The writer reads the relevant facts and updates section-level memory text.

**Call relations**: The manifest schedules this as the nightly section-writing job. Its candidate query selects workspaces where a section has enough facts to summarize or has an old paragraph that may need removal.

*Call graph*: 1 external calls (__init__).


##### `write_memory_overview`  (lines 690–695)

```
async def write_memory_overview(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job writes the opening overview paragraph for a workspace’s shared memory page. It gives readers a short top-level summary before they inspect individual facts.

**Data flow**: It receives an extension context, builds an `OverviewWriter` with the transaction function, workspace ID, and model, then runs it. The writer decides what overview paragraph should stand based on current shared facts.

**Call relations**: The manifest schedules this shortly after the section-writing job. Its candidate query looks for workspaces with enough shared facts for an overview, or an existing overview that may need to be cleared.

*Call graph*: 1 external calls (__init__).


##### `write_member_profiles`  (lines 698–703)

```
async def write_member_profiles(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job writes profile information for members, such as their role and current focus, based on shared memory facts. It helps the memory surface show who people are and what they are working on.

**Data flow**: It receives an extension context, builds a `ProfileWriter` with the transaction function, workspace ID, and model, then runs it. The writer reads roster and fact information and updates profile records.

**Call relations**: The manifest schedules this in the nightly memory-writing window after overview and section writing. Its candidate query selects workspaces that have at least one shared fact.

*Call graph*: 1 external calls (__init__).


##### `curate_memory_pages`  (lines 706–711)

```
async def curate_memory_pages(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job performs a whole-page curation pass over memory pages. It reads a subject’s page as a whole and retires rows that repeat one another.

**Data flow**: It receives an extension context, builds a `PagePass` with the transaction function, workspace ID, and model, then runs it. The pass performs the detailed page-level review and retirement work.

**Call relations**: The manifest schedules this before the nightly paragraph-writing jobs and marks it as needing the deployment model. Its candidate query selects workspaces with enough facts under one subject to justify a whole-page read.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 714–719)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with memory items still missing embeddings. Those workspaces need the indexing job.

**Data flow**: It creates a SQL query selecting distinct workspace IDs from memory rows where the embedding digest is missing. It returns the query rather than executing it.

**Call relations**: The manifest passes this query builder to `owner_candidates` for the `memory_index` job, so the scheduler can choose only workspaces with indexing work to do.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 722–739)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with enough old facts to consolidate. It prevents the consolidation job from running where it cannot usefully create a summary.

**Data flow**: It computes an age cutoff using the current time minus the minimum fact age. It builds a SQL query for live, unsuperseded, tool-written facts older than that cutoff, groups them by workspace, and keeps only workspaces with at least the required count. It returns the query.

**Call relations**: The manifest uses this as the candidate source for the `memory_consolidate` job. The scheduler calls it when deciding which workspaces should receive that job tick.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 742–763)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with likely duplicate memory rows. It avoids spending a deduplication pass on workspaces that have no old repeated groups.

**Data flow**: It computes an age cutoff using the current time minus the deduplication minimum age. It builds a SQL query for live, unsuperseded, non-section, tool-written rows older than the cutoff, groups them by workspace, subject, and class, and keeps groups with enough copies. It returns distinct workspace IDs.

**Call relations**: The manifest uses this as the candidate source for the `memory_dedup` job. It narrows the job runner’s attention to workspaces where a dedup sweep is likely to do something.

*Call graph*: 2 external calls (now, select).


##### `_class_count`  (lines 766–770)

```
def _class_count(item_class: ItemClass) -> sa.Function[int]
```

**Purpose**: This helper builds a SQL count for rows of one memory item class inside a grouped query. It lets candidate queries ask questions like “how many facts?” and “is there already a section paragraph?” in the same grouped scan.

**Data flow**: It receives an item class. It returns a SQL expression that counts only rows whose `item_class` matches that value. It does not execute the query itself.

**Call relations**: The section and overview candidate query builders call this when they need separate counts for facts and paragraph rows.

*Call graph*: called by 2 (_overviewable_workspaces, _summarizable_workspaces); 1 external calls (case).


##### `_summarizable_workspaces`  (lines 773–796)

```
def _summarizable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces where section paragraphs may need writing or removal. It looks for subject-and-kind bands with enough facts, or bands that still have a section paragraph standing.

**Data flow**: It builds a SQL query over live fact and section rows, groups by workspace, subject, and memory kind, and keeps groups where the fact count meets the section-writing threshold or the section count is greater than zero. It returns distinct workspace IDs.

**Call relations**: The manifest uses this as the candidate source for the nightly `memory_section` job. It relies on `_class_count` to count facts and section paragraphs separately.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_overviewable_workspaces`  (lines 799–821)

```
def _overviewable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces where the shared memory overview may need writing or removal. It focuses only on the shared subject, because the overview is for the whole workspace page.

**Data flow**: It builds a SQL query over live shared facts and overview rows, groups by workspace, and keeps workspaces with enough facts for an overview or with an existing overview paragraph. It returns the query.

**Call relations**: The manifest uses this as the candidate source for the nightly `memory_overview` job. It calls `_class_count` to distinguish fact rows from overview rows.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_peopled_workspaces`  (lines 824–838)

```
def _peopled_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces where member profiles might be writable. A workspace with no shared facts gives the profile writer nothing useful to draw from.

**Data flow**: It builds a SQL query selecting distinct workspace IDs from live, unsuperseded shared facts. It returns that query without running it.

**Call relations**: The manifest uses this as the candidate source for the `memory_people` job. The scheduler uses it to avoid running profile writing where no shared memory exists.

*Call graph*: 1 external calls (select).


##### `_curatable_workspaces`  (lines 841–857)

```
def _curatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with enough facts under one subject to justify whole-page curation. It avoids calling the model for pages that are too small to review meaningfully.

**Data flow**: It builds a SQL query over live, unsuperseded facts, groups them by workspace and subject, and keeps groups with at least the page-pass minimum row count. It returns distinct workspace IDs.

**Call relations**: The manifest uses this as the candidate source for the `memory_page_pass` job. The job runner uses it before calling `curate_memory_pages`.

*Call graph*: 1 external calls (select).


##### `manifest`  (lines 860–1019)

```
def manifest() -> Manifest
```

**Purpose**: This constructs the full extension declaration that the host system loads. It tells the system what the memory extension is, what tools it exposes, what hooks and jobs it runs, what objects and pages it provides, and how memory search is offered.

**Data flow**: It creates and returns a `Manifest` object. Inside it are tool definitions with input models and handlers, object definitions, hook specifications, job schedules and candidate queries, the memory search provider, and the memory web surface route registration.

**Call relations**: The host extension loader calls this at startup. Everything else in this file becomes active through the objects this function places in the manifest.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `extension startup and recurring scheduled job registration`

This is the extension’s sign-up sheet. When the larger UFO system loads extensions, it needs a compact declaration of what each extension provides. This file declares that the monitors extension is named "monitors", has a version, offers one tool, defines one durable object kind, and needs a clock-driven background job.

A monitor here means a repeated check, like setting an alarm to test something every so often. The monitor runner does the actual probing work elsewhere; this file only makes sure the system knows when and where to call it. The job is scheduled with a cron-like expression, which is a compact time pattern. Here it is set to run once per minute.

The important detail is that the recurring job does not blindly scan everything. Its candidate workspaces come from `due_monitor_workspaces()`, which means the dispatcher is told only about workspaces that currently have monitor work ready to do. That keeps idle workspaces cheap, like only sending a repair truck to streets where someone has reported a problem.

In short, this file connects the monitors feature to the host system’s extension, tool, object, and job machinery.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job handler that runs when the scheduled monitor job fires. It creates a `MonitorRunner`, which is the component that actually checks due monitors, and tells it to run using the current extension context.

**Data flow**: It receives an `ExtensionContext`, which is the host system’s bundle of services and information for this extension. It passes that context into a new monitor runner, then the runner performs the monitor checks. Nothing is returned; the useful result is the side effect of the runner doing its scheduled work.

**Call relations**: The job declared by `manifest` points at `_probe` as its handler. When the platform’s scheduler reaches the monitor job’s time, it calls `_probe`, and `_probe` hands control to `MonitorRunner` so the real probing logic stays in the runner rather than in the manifest file.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This builds and returns the formal extension declaration. The host uses it to discover the monitors extension’s name, version, tool, object type, and scheduled background job.

**Data flow**: It starts from constants in this file and imported pieces from the monitors extension: the monitor tool, monitor object, runner handler, and the query for due workspaces. It packages them into a `Manifest` object. The returned manifest is what the host system reads to wire the extension into the rest of the application.

**Call relations**: This function is called when the extension is being loaded. While building the manifest, it creates a `JobSpec` for the recurring monitor runner and asks `due_monitor_workspaces()` to define which workspaces should be considered for that job. The finished manifest then hands all of this registration information back to the host.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `startup registration, then each user prompt submission`

This file is the front door for the objectives extension. An objective is durable work that lasts beyond one chat turn, such as a multi-step task that may be resumed after a heartbeat, a handoff, or another interruption. Without this file, the agent might have tools for tracking objectives, but it would not automatically be reminded that an objective exists or what still needs doing.

The file does two main jobs. First, it defines a prompt section that teaches the agent when to create an objective, what counts as a meaningful step, and how to record progress honestly. It stresses that acceptance conditions should be real checks, not artificial markers created just to pass.

Second, it installs a hook, which is code that runs at a specific moment in the system. Here the hook runs when a user prompt is submitted. If the current conversation has an objective, the hook reads its “frontier”: the still-relevant open or attempted steps, their required conditions, and any outstanding question already raised with the user. It then injects that summary into the agent’s context. This is like putting the project checklist back on the desk at the start of every work session.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function reminds the agent about the current objective at the start of a turn. It builds a compact status note showing the objective directive, progress counts, open steps, unmet conditions, and any question already asked of the user.

**Data flow**: It receives a hook context from the system. If there is no active turn, it returns nothing. Otherwise, it opens an extension database transaction, finds the objective tied to the current conversation and workspace, and stops if none exists. When it finds one, it records a metric, turns the objective’s active steps into readable lines of text, and returns an InjectContext containing that text so it can be added to the agent’s prompt.

**Call relations**: The system calls this function through the hook registered by manifest when a user prompt is submitted. Inside, it asks agent_current for the current workspace, uses Objectives to read the durable objective record, uses condition_summary to explain each acceptance condition, emits a metric about the injection, and finally hands the finished text to InjectContext so the broader prompt-building flow can include it.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the UFO system what the objectives extension contains. It names the extension, lists its tools, registers the prompt guidance, and connects the prompt-submission hook that injects objective status.

**Data flow**: It takes no input. It gathers the constants and tool objects defined or imported in the file, wraps the hook handler in a HookSpec, wraps the instructional text in a PromptSection, and returns a Manifest object describing the whole extension.

**Call relations**: The extension loader calls this function when the extension is being registered. The returned Manifest hands the system four objective-related tools, the _inject_frontier hook to run on user_prompt_submit, and the prompt section that teaches the agent how to use objectives responsibly.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Recurring task services
These manifests expose scheduled report and task capabilities, including their object types, tools, jobs, and dependencies.

### `extensions/report_digest/ufo_ext_report_digest/manifest.py`

`config` · `extension startup, then scheduled background runs and admin tool use`

This file is the registration sheet for the report-digest extension. Without it, the system would not know that this extension has a digest-writing job, a skill prompt that defines the writing standard, or an admin tool for rebuilding bad digest entries.

The extension has two main paths. First, a scheduled job runs every ten minutes and looks for reports whose digest entries still need to be written. It uses the same writing standard stored in the skill directory, so both humans asking an agent and the background job follow one shared rulebook. Second, a workspace admin can use a tool called “rebuild_report_digest” when recent report entries read badly. That tool does not rewrite anything immediately. Instead, it marks reports from the last seven days as due again, like putting them back on a to-do pile. The regular digest job later works through that pile in batches.

The file also protects important assumptions. The background job refuses to run if it was not given a model to write with or blob access to read published reports. The rebuild tool refuses non-admin users. The manifest ties all of this together so the host application can load the extension safely and present the right action in the report collection UI.

#### Function details

##### `write_digests`  (lines 38–43)

```
async def write_digests(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job entry point for writing missing report digest entries. It makes sure the extension has the two things it needs: a background language model to write with, and blob access so it can read the published report text.

**Data flow**: It receives an ExtensionContext from the host system. It checks that the context contains a model and member-context blob storage; if either is missing, it stops with a clear error. If both are present, it creates a DigestWriter using that context, model, and blob access, then runs it so pending digest entries can be written.

**Call relations**: The manifest registers this function as the handler for the recurring report_digest job. When the scheduler decides a workspace has undigested reports, it calls this function, which then hands the actual reading and writing work to DigestWriter.

*Call graph*: 1 external calls (__init__).


##### `rebuild_report_digest_handler`  (lines 50–73)

```
async def rebuild_report_digest_handler(ctx: ToolContext, args: RebuildReportDigestInput) -> ToolResult
```

**Purpose**: This is the admin tool action that asks the system to rewrite recent report digest entries. It does not rewrite them on the spot; it marks eligible reports as needing work so the scheduled job can process them later.

**Data flow**: It receives a ToolContext, which describes the current tool call and speaker, plus an empty validated input object. It first checks that the tool was given its extension context. Then it asks whether the speaker is a workspace admin. If not, it raises an error. If the speaker is allowed, it runs DigestRebuild, which marks recent reports as due again. It returns a ToolResult containing either a “nothing to rebuild” message or a count of how many reports were put back in the digest-writing queue.

**Call relations**: The manifest exposes this function as the handler behind the “rebuild_report_digest” tool. A user action in the report collection can call it, but only admins get past the permission check. After it marks reports due, it relies on the normal scheduled digest job to do the actual writing later.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 76–114)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension declaration that the UFO host reads when loading the report-digest extension. It says what the extension is called, what skill it provides, what tool it exposes, what job should be scheduled, and what object type it contributes.

**Data flow**: It uses constants and imported building blocks to assemble a Manifest object. Into that manifest it puts the skill directory, the admin rebuild tool definition, the scheduled digest-writing job, the report object definition, and a flag saying the extension needs permission to read member context. The finished Manifest is returned to the host system.

**Call relations**: This is the file’s central wiring point. During extension loading, the host calls this function to discover everything the extension offers. The returned manifest connects the rebuild tool to rebuild_report_digest_handler, connects the scheduled job to write_digests, and uses owner_candidates with undigested_workspaces so the scheduler knows which workspaces need the job.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension load and scheduled background runs`

This file is the extension’s front desk. When UFO loads extensions, it needs a clear list of what each extension offers and what background work it wants the system to run. Without this file, the scheduled-tasks feature would not be registered: agents would not get the pause-and-wait tool, scheduled task objects would not be known, and due tasks or paused conversations would not be resumed on the clock.

The file declares two recurring jobs that both run once per minute. One looks for scheduled tasks that are due. The other looks for paused conversations that are ready to resume. They are separate on purpose: if one kind of work gets stuck, it should not stop the other kind from running. Each job also supplies a “candidate” finder, which means the dispatcher can ask, “Which workspaces actually have due work?” before spending effort there. This is like checking which mailboxes have mail before sending a delivery person to every house.

The manifest also points to a task-scheduling skill folder, registers a conversation slot used for automation-related state, and says this extension depends on memory search. The small helper functions `_run` and `_resume` are the actual job entry points; they create the right runner and tell it to do its work.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job callback for scheduled tasks that are due to run. It creates a `ScheduledTaskRunner`, which is the worker that knows how to find and invoke due scheduled tasks for the current extension context.

**Data flow**: It receives an `ExtensionContext`, which is the system-provided bundle of runtime services and workspace information. It passes that context into `ScheduledTaskRunner`, then waits for the runner’s `run` method to finish. It returns nothing; the useful result is the background work the runner performs.

**Call relations**: The job declared in `manifest` uses `_run` as its handler. When the scheduler fires the scheduled-task job, this function is called and immediately hands the real work to `ScheduledTaskRunner`.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job callback for paused conversations that are ready to continue. It creates a `PauseRunner`, which is the worker responsible for resuming conversations whose waiting time has expired.

**Data flow**: It receives an `ExtensionContext` from the job system. It builds a `PauseRunner` with that context, then waits for the runner’s `run` method to complete. It does not return data; it causes ready pauses to be processed.

**Call relations**: The job declared in `manifest` uses `_resume` as its handler. When the pause-resume job fires, `_resume` passes control to `PauseRunner` so that pause-specific logic stays outside the manifest file.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This builds and returns the extension manifest: the official description of everything this extension contributes to UFO. The platform uses it to register tools, object types, background jobs, skills, dependencies, and conversation storage slots.

**Data flow**: It reads constants from this file, such as the extension name, version, job names, schedule, and skill folder. It also asks `due_task_workspaces` and `due_pause_workspaces` for workspace selectors, builds two `JobSpec` objects, builds `SkillSpec` entries for the listed skill names, and wraps everything in a `Manifest`. The output is that complete `Manifest` object.

**Call relations**: UFO calls `manifest` when loading the extension. Inside it, the two job specifications connect the scheduler to `_run` and `_resume`, while the candidate functions tell the scheduler where due scheduled tasks or due pauses exist. The manifest is the bridge between this extension’s code and the wider UFO runtime.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### Client surface extensions
The shell and web package markers and manifests register the live client surfaces and portal-specific jobs.

### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package, like putting a label on a drawer so the rest of the program knows where to find related tools. Because this file has no code inside it, importing `ufo_ext_ufo` does not run setup logic, create objects, or expose helper functions directly. Its value is structural: without it, some Python tooling or older import behavior might not recognize this directory as a package, and imports that expect `ufo_ext_ufo` to exist could fail. In short, this file is a quiet signpost for the extension’s Python module layout.


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

This is a small registration file, like a sign on a shop door that tells the main application what this extension offers and where to send visitors. The `ufo` extension exposes one “surface,” meaning one public-facing way for other parts of the system to interact with it. In this case, that surface is the terminal stream used by the `ufo` shell client.

The file does not contain the streaming logic itself. Instead, it imports the route definitions and workspace-identification function from the surface module, then packages them into a `Manifest`. A manifest is a compact description the host system can read during startup or extension discovery. It says: this extension is named `ufo`, it is version `0.1.0`, and it provides a surface with these routes and this way to identify the workspace.

A notable detail is that this extension does not declare extra credential slots or configuration knobs here. The comment explains that access is checked using a bearer token against the `UFO_TOKEN_SECRET` environment variable, rather than a stored workspace credential. So installing the extension is enough to make its route available, much like mounting a web endpoint.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the core system reads to know what the `ufo` extension provides. Someone would use this when loading extensions so the main application can mount the extension’s surface and routes.

**Data flow**: It starts with fixed local constants for the extension name and version, plus imported route and workspace-identification details. It wraps those into a `SurfaceSpec`, which describes one public interaction surface, then wraps that into a `Manifest`. The result is a ready-to-read manifest object; it does not change files, network state, or stored settings.

**Call relations**: During extension loading, the host calls `manifest` to ask this module what it offers. The function creates a `SurfaceSpec` for the UFO surface, then passes that into `Manifest.__init__` so the extension can be registered with the core system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the program find the drawer by name. Because the file has no code, it does not start anything, configure anything, or expose any functions directly. Its value is structural: without it, some Python environments or packaging tools might not recognize `extensions/web/ufo_ext_web` as a normal package, which could make imports fail or behave differently.


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup and scheduled background jobs`

Think of this file as the web extension’s registration card. When the larger UFO system starts up or discovers extensions, this manifest explains what the web extension is called, what version it is, what routes it exposes, and what background work it wants the core to schedule.

The file declares feature flags for portal sections such as Code, Issues, Meetings, Metrics, Radar, Wiki, Memory, and Skills. A feature flag is a named on/off switch, usually controlled by deployment configuration, that lets operators decide which parts of the portal appear in a given environment.

The manifest also says that the web extension connects to member accounts. In plain terms, the portal acts on behalf of the signed-in user rather than using one shared bot secret. It exposes one main browser surface, marks it as the home surface, and gives the core a function for figuring out which workspace a request belongs to.

Finally, it registers two scheduled jobs. One job gives untitled conversations readable titles based on their opening exchange. The other seeds homepages for agent workspaces that do not have one yet. The important detail is that this extension does not just draw pages; it also asks the core to run small housekeeping tasks that keep the portal experience tidy.

#### Function details

##### `manifest`  (lines 66–92)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the object the core system reads to understand what this extension provides. Someone would use it when loading extensions so the web portal can be mounted, configured, and given its scheduled jobs.

**Data flow**: It starts with constants imported from the web extension, such as route definitions, feature flag names, job names, and helper functions. It wraps those into surface, job, flag, tool, and conversation-slot declarations, then returns one complete Manifest object. Nothing is written to disk or sent over the network here; the output is a structured description for the core system to consume.

**Call relations**: When the extension is discovered, the core calls this function to ask, “What are you?” Inside, it creates a SurfaceSpec for the browser portal, two JobSpec entries for title summarizing and homepage seeding, and uses SDK helpers to choose which workspaces are candidates for those jobs. It then hands the finished Manifest back to the core, which can mount the web routes, expose the declared tools, honor the listed flags, and schedule the requested background work.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).
