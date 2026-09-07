# Runtime surfaces, memory, sites, and backend registrations  `stage-4.8`

This stage is shared startup support. It is where optional parts of the system introduce themselves to the main application before users start working. Most files are “manifests,” which are like registration cards: they name an extension, state its version, and list the tools, screens, routes, jobs, or backends it wants the host to connect.

The debugger manifest adds a protected debugging page and a reporting tool. The memory package marker is just a doorway for imports, while its manifest registers memory tools, automatic recall, page listeners, cleanup and writing jobs, search support, and a memory screen. The Redis hub manifest tells the system how to create Redis-based hub and terminal backends. The sites package marker enables imports, and its manifest wires in website-building tools, prompts, agents, storage, a web surface, and background work. The UFO and web package markers are import doorways; their manifests mount the shell client and web portal with their routes, permissions, jobs, flags, and browser-facing surfaces.

## Files in this stage

### Debugger surface
Debugger registration exposes the protected debugger web surface and its reporting tool to the host system.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This is the debugger extension's front desk sign. When the larger UFO system discovers extensions, it needs a simple answer to questions like: What is this extension called? What version is it? What tools does it add? What web pages or routes should be mounted? This file provides that answer through a manifest, which is a structured description of the extension.

The extension adds a `report_problem` tool, imported as `REPORT_PROBLEM_TOOL_DEF`, so workspace faults can be sent to operators. It also adds a debugger surface, which is a user-facing area of the system, like a small web section with routes. That surface is protected by an `identify` resolver, `resolve_operator_workspace`, which decides whether the caller is allowed to reach the operator workspace. In plain terms, the surface is not just published for everyone; it is mounted with an identity check at the system boundary.

Without this file, the debugger extension could contain useful code, but the host would not know how to register it. The reporting tool would not be advertised, and the debugger surface would not be wired into the application.

#### Function details

##### `manifest`  (lines 16–24)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension's manifest, which is the package of information the host system uses to register the extension. It declares the extension name, version, available tool, and protected debugger surface.

**Data flow**: It starts with module constants and imported pieces: the extension name and version, the report-problem tool definition, the debugger routes, the debugger surface name, and the operator identity resolver. It wraps the routes and identity resolver into a `SurfaceSpec`, then places that surface and the tool into a `Manifest`. The result is a complete manifest object returned to whoever is loading the extension.

**Call relations**: During extension loading, the host calls `manifest` to ask what this extension contributes. Inside, it creates a `SurfaceSpec` for the debugger web surface and then creates a `Manifest` that includes that surface plus the reporting tool. That returned manifest is what lets the rest of the system mount the debugger surface and expose the problem-reporting tool.

*Call graph*: 2 external calls (__init__, __init__).


### Memory extension
The memory package marker and manifest introduce recall, storage, cleanup, search, hooks, listeners, jobs, and the memory web surface.

### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `package import`

This package is for the system’s memory feature: storing durable facts, recalling relevant facts when a user submits a prompt, deriving memory pages when pages change, and running a job that indexes memories so they can be found later. Think of it like the label on a filing cabinet: the real folders and tools are elsewhere, but this file tells readers what kind of cabinet they are opening. Without this file, Python would not treat this directory as a normal package in the same way, and newcomers would lose a concise summary of the extension’s job. There are no functions, classes, or runtime steps here. Its main value is organization and documentation: it names the extension’s responsibilities and ties together the idea that memory is not just storage, but also recall, page-based updates, and indexing.


### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, request handling, prompt submission, page-change processing, and scheduled background jobs`

The memory extension gives the agent a durable notebook: it can search old facts, write new ones, pull useful memories into a prompt before the model answers, and keep a wiki-like memory page tidy over time. Without this file, the rest of the memory code would exist but the system would not know when to call it or how to expose it.

The file does three main jobs. First, it defines the inputs for tools such as memory search, memory update, corrections, first-run setup, and rebuilding facts from synced pages. These input models act like forms: they say exactly what information the caller must provide. Second, it contains the small handlers that turn tool or hook calls into real work, such as saving a memory row, searching stored memories and source pages, or injecting relevant remembered facts into the next model prompt. Third, the manifest at the bottom registers everything with the host system: tools, object types, hooks, timed jobs, search provider, and the memory surface.

A useful analogy is a building directory. The deeper modules are the offices where indexing, deduping, summarizing, and fact derivation happen. This file is the directory and reception desk: it tells visitors which office to go to, checks a few permissions, and passes along the right paperwork.

#### Function details

##### `_date_bound`  (lines 254–265)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date string into a timezone-aware UTC datetime used to narrow memory searches. It treats an end date written as just a date as covering that whole day, which makes date-window searches feel natural.

**Data flow**: It receives either no value or a date/date-time string, plus whether this is the end of a range. If the value is present, it parses it, adds UTC if no timezone was written, and for plain end dates moves the bound to the next midnight. It returns a datetime or None, and bad date text is allowed to raise an error for the tool layer to report.

**Call relations**: The memory search tool calls this before searching. Its output becomes the start and end filter passed into the shared memory search workflow.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 274–352)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Searches both saved memory items and synced source-page snippets for one to three focused queries. It gives each query a fair chance by merging results round-robin instead of letting one query dominate.

**Data flow**: It receives search text, the caller's readable subjects from a source reader, and optional date bounds. It asks the memory store to recall stored items and search source pages in parallel, removes duplicates, wraps each hit as a MemoryMatch with a reference that can be opened later, and returns a combined tuple of matches.

**Call relations**: The memory_search tool uses this as its main search engine, and the manifest also exposes this class as the extension's memory search provider for other parts of the system. It hands off the heavy work to the store, then formats the results into the SDK's shared search-result shape.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 354–357)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which classes of memory items can be listed by consumers. It reads this from the central ItemClass type so the list stays in step with the store.

**Data flow**: It takes no external input beyond the service instance. It reads the allowed item-class values from the type definition and returns them as a tuple of strings.

**Call relations**: This supports consumers that browse memory rather than search it. It avoids maintaining a second hand-written list that could fall out of date.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 359–409)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Returns a paged list of recent live memory items for subjects the caller may read. This is the browse view: newest first, without similarity search and without source-page dumps.

**Data flow**: It receives subjects, a limit, optional item-class filters, and an optional cursor for pagination. It builds a database query for non-retired, non-superseded memory rows in the current workspace, applies the paging helper, and converts database rows into MemoryMatch objects. It returns a ListingPage containing the items and paging position.

**Call relations**: Other listing-aware parts of the system call this when they need recent memory entries. It relies on the shared listing helpers so memory browsing behaves like other paged lists in the product.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 412–419)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one search result into a line of text an agent can read. It includes the memory or page reference when available so the agent can open the underlying object later.

**Data flow**: It receives a MemoryMatch. It starts with the kind and snippet text, then, if there is an object reference, adds that reference and the creation date if known. It returns one human-readable string.

**Call relations**: The memory_search tool calls this after search results come back. It is the final presentation step before the tool response is sent to the model.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 422–442)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the memory_search tool that an agent can call during a conversation. It searches durable memory and source snippets, then returns readable hits or a clear 'no match' message.

**Data flow**: It receives a tool context and validated search arguments. It checks that extension context is present, parses optional date bounds, builds a source reader for the caller, runs MemorySearchService.search, formats each match with match_line, and returns a ToolResult containing text.

**Call relations**: The manifest registers this as the handler for the memory_search tool. It sits between the tool call from the agent and the reusable MemorySearchService.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 445–459)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the memory_update tool that records a new durable memory item. Agents use it when they learn something worth remembering across future turns.

**Data flow**: It receives the current tool context and the memory text plus class, kind, confidence, and optional source reference. It chooses the current effective audience as the subject, writes a MemoryWrite to the store, and returns a short confirmation naming the subject.

**Call relations**: The manifest registers this as a side-effecting tool because it changes stored memory. It hands the actual database write to the memory store.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_correction_handler`  (lines 462–480)

```
async def record_correction_handler(ctx: ToolContext, args: RecordCorrectionInput) -> ToolResult
```

**Purpose**: Records a corrected version of an existing memory item from the memory view. It does not edit the old item directly; it writes a new statement that points back to the old one.

**Data flow**: It receives the item being corrected and the replacement text. It writes a new fact under the speaker's current audience, using a source reference that says which memory it corrects, then returns a confirmation.

**Call relations**: The manifest exposes this as a portal action bound to the memory collection. Later deduplication can retire the old near-duplicate toward this newer corrected row.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_first_run_handler`  (lines 483–499)

```
async def record_first_run_handler(ctx: ToolContext, args: RecordFirstRunInput) -> ToolResult
```

**Purpose**: Records the first-run setup fact about what tools or systems the team uses. This makes the onboarding choice available to later conversations.

**Data flow**: It receives one short statement from the first-run flow. It writes it as a normal fact for the current audience with a fixed source reference marking it as first-run data, then returns a confirmation.

**Call relations**: The manifest registers this as a side-effecting first-run action. It uses the same memory store path as ordinary memory writes, but with fixed provenance.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 502–584)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically finds relevant memory before the model responds to a submitted user prompt. It is best-effort: if recall is slow or fails, the conversation continues without memory instead of blocking the turn.

**Data flow**: It receives a hook context. If the payload is a user prompt and the turn is suitable for recall, it computes readable subjects, searches memory with a short timeout, drops topic-only items, truncates long lines, respects an overall size limit, logs what happened, and returns injected prompt text when there is anything useful. On recall failure it logs the problem and returns no injection.

**Call relations**: The manifest registers this for user_prompt_submit. It runs before the model sees the turn, using the memory store to fetch context and InjectContext to add that context to the prompt.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 587–596)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that indexes stored memory items so they can be searched by meaning. It requires both an index backend and an embedding backend, where embeddings are numeric representations of text meaning.

**Data flow**: It receives the extension context, checks that indexing and embedding services are wired, creates a MemoryIndexer with a text chunker and database transaction function, and runs it. The result is changed index state rather than a returned value.

**Call relations**: The manifest registers this as the memory_index job. The job runner calls it for workspaces that have memory rows still waiting for indexing.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 599–615)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to synced page changes by indexing page text and mirroring page information for memory search. This keeps document snippets searchable alongside hand-written memory.

**Data flow**: It receives a hook context, ignores it unless the payload is a page-change batch, checks for index and embedding services, builds a PageIndexer, and applies the delivered page changes. It returns no hook output.

**Call relations**: The manifest registers this as one of the page_change hooks. The core runner owns the cursor and delivers batches; this function processes each batch for search indexing.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 618–629)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to synced page changes by extracting durable fact memories from changed pages. This turns source documents into rows the memory wiki can recall and display.

**Data flow**: It receives a hook context, ignores non-page-change payloads, checks that a model is available, builds a FactDeriver from the memory store and model, and applies it to the changed pages. It returns no hook output, while the deriver writes new facts and retires replaced page-derived facts.

**Call relations**: The manifest registers this as a second page_change hook with its own cursor. It deliberately fails loudly if no model is wired, so the cursor does not advance past pages whose facts were not derived.

*Call graph*: 2 external calls (__init__, store_for).


##### `rebuild_page_facts_handler`  (lines 641–657)

```
async def rebuild_page_facts_handler(ctx: ToolContext, args: RebuildPageFactsInput) -> ToolResult
```

**Purpose**: Implements the admin-only tool that asks the system to derive facts from every synced page again. It queues the rebuild by resetting the page-derivation cursor rather than doing the full work immediately.

**Data flow**: It receives a tool context and empty arguments. It verifies an extension context exists, checks that the speaker is a workspace admin, deletes the cursor key used by derive_facts, and returns a message explaining that facts will be rewritten as the derivation pass reaches each page.

**Call relations**: The manifest exposes this as the rebuild_page_facts tool. It does not call the fact deriver directly; instead it causes the normal page-change derivation flow to replay from the beginning.

*Call graph*: calls 1 internal fn (require_speaking_admin); 2 external calls (__init__, __init__).


##### `consolidate_memory`  (lines 660–668)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled consolidation pass that groups older related facts into higher-level summaries. This helps memory stay useful instead of growing as many tiny overlapping rows forever.

**Data flow**: It receives the extension context, checks for an embedding backend, creates a MemoryConsolidator with database access, workspace identity, embeddings, and the model, and runs it. It changes memory rows through the consolidator and returns nothing.

**Call relations**: The manifest registers this as the memory_consolidate job. The job runner calls it only for candidate workspaces that appear to have enough aged facts to consolidate.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 671–679)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled duplicate cleanup pass. It finds repeated tool-written memory rows and retires older copies toward the newest useful version.

**Data flow**: It receives the extension context, checks for embeddings, builds a MemoryDeduper with database access, workspace identity, store access, and embeddings, then runs it. Its effect is to mark duplicates as superseded or retired as decided by the deduper.

**Call relations**: The manifest registers this as the memory_dedup job. Candidate selection tries to call it only for workspaces with likely duplicate backlogs.

*Call graph*: 1 external calls (__init__).


##### `write_memory_sections`  (lines 682–687)

```
async def write_memory_sections(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled pass that writes or rewrites section paragraphs for the memory wiki. Each paragraph summarizes the facts under one subject-and-kind band.

**Data flow**: It receives the extension context, creates a SectionWriter using the workspace, transaction function, and model, then runs it. The output is stored paragraph memory rows, not a direct return value.

**Call relations**: The manifest registers this as the memory_section job. It is scheduled after curation so the paragraph reflects the current facts printed beneath it.

*Call graph*: 1 external calls (__init__).


##### `write_memory_overview`  (lines 690–695)

```
async def write_memory_overview(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled pass that writes the opening overview paragraph for the shared memory page. It summarizes the page at a higher level than the section paragraphs.

**Data flow**: It receives the extension context, creates an OverviewWriter with database access, workspace identity, and the model, then runs it. It writes or retires overview rows in storage and returns nothing.

**Call relations**: The manifest registers this as the memory_overview job. It is scheduled shortly after section writing so the page-level summary and section summaries describe the same nightly state.

*Call graph*: 1 external calls (__init__).


##### `write_member_profiles`  (lines 698–703)

```
async def write_member_profiles(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled pass that writes member profile summaries, such as each person's role and current focus. This turns shared facts into a people-oriented view.

**Data flow**: It receives the extension context, creates a ProfileWriter with workspace, database, and model access, and runs it. The resulting profile information is written to memory profile storage.

**Call relations**: The manifest registers this as the memory_people job. It runs in the nightly writing window after the broader page-writing passes.

*Call graph*: 1 external calls (__init__).


##### `curate_memory_pages`  (lines 706–711)

```
async def curate_memory_pages(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled whole-page curation pass. It reads a subject's full memory page with the deploy model and retires rows that repeat one another.

**Data flow**: It receives the extension context, creates a PagePass with database access, workspace identity, and the model, then runs it. It updates stored memory state by retiring redundant rows and returns nothing.

**Call relations**: The manifest registers this as the memory_page_pass job and marks it as needing the deploy model. It runs before the nightly paragraph-writing jobs so summaries are written from already-curated rows.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 714–719)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces with memory items that still need embeddings. This keeps the indexing job focused on places where there is actual work.

**Data flow**: It takes no arguments. It creates a SQL query selecting distinct workspace IDs from memory rows whose embedding digest is missing. It returns that query for the job candidate system to use.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_index job. The job scheduler uses it before deciding which workspaces to run.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 722–739)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces with enough old live facts to consolidate. It prevents the hourly consolidation job from running where it cannot produce a useful cluster.

**Data flow**: It computes a cutoff time based on the minimum age for consolidation, then builds a SQL query for workspaces with at least the required number of eligible facts. It excludes page-derived, superseded, and retired rows. It returns the query.

**Call relations**: The manifest uses this as the candidate source for the memory_consolidate job. The consolidator itself does the real summarizing, but this query decides where running it is worth trying.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 742–763)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces that likely have duplicate memory rows. It avoids spending a deduplication tick on workspaces with only fresh or page-derived rows.

**Data flow**: It computes a minimum-age cutoff, then builds a grouped SQL query looking for enough live tool-written rows sharing the same subject and item class. It returns distinct workspace IDs that meet the duplicate-copy threshold.

**Call relations**: The manifest uses this query builder for the memory_dedup job's candidates. The later dedup job receives only likely workspaces, then decides which rows to retire.

*Call graph*: 2 external calls (now, select).


##### `_class_count`  (lines 766–770)

```
def _class_count(item_class: ItemClass) -> sa.Function[int]
```

**Purpose**: Creates a SQL count for rows of one memory item class inside a grouped query. It is a small helper for deciding whether paragraph-writing jobs have enough facts or existing paragraphs to act on.

**Data flow**: It receives an item class such as fact, section, or overview. It builds a database expression that counts only rows of that class within each group. It returns the expression, not a number immediately.

**Call relations**: _summarizable_workspaces and _overviewable_workspaces call this while building their candidate queries. It lets each query compare fact counts and paragraph counts separately in one grouped scan.

*Call graph*: called by 2 (_overviewable_workspaces, _summarizable_workspaces); 1 external calls (case).


##### `_summarizable_workspaces`  (lines 773–796)

```
def _summarizable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces where section paragraphs may need writing or removal. A workspace qualifies if a band has enough facts to summarize or still has an old section paragraph standing.

**Data flow**: It creates a SQL query over live fact and section rows, groups them by workspace, subject, and memory kind, and uses class-specific counts to detect useful work. It returns distinct workspace IDs.

**Call relations**: The manifest uses this for candidates of the memory_section job. It ensures the section writer runs both when a paragraph should be written and when an existing paragraph may need to disappear because facts fell below the floor.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_overviewable_workspaces`  (lines 799–821)

```
def _overviewable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces where the shared page overview may need writing or removal. It focuses only on shared-subject facts and overview rows.

**Data flow**: It creates a SQL query for live shared rows, groups by workspace, and checks whether there are enough facts for an overview or any existing overview paragraph. It returns workspace IDs that need the overview pass.

**Call relations**: The manifest uses this as the candidate source for the memory_overview job. It keeps the overview writer from running for workspaces that have no shared page worth summarizing.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_peopled_workspaces`  (lines 824–838)

```
def _peopled_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces where member profile writing may have material to use. A workspace qualifies if it has any live shared fact.

**Data flow**: It creates a SQL query selecting distinct workspace IDs from live shared facts. It excludes superseded and retired rows, then returns the query.

**Call relations**: The manifest uses this for candidates of the memory_people job. The ProfileWriter does the actual roster-aware writing after the scheduler chooses candidate workspaces.

*Call graph*: 1 external calls (select).


##### `_curatable_workspaces`  (lines 841–857)

```
def _curatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces with memory pages large enough for whole-page curation. It avoids running the expensive page pass on tiny pages.

**Data flow**: It creates a SQL query over live fact rows, groups by workspace and subject, and keeps groups with at least the required number of rows. It returns distinct workspace IDs.

**Call relations**: The manifest uses this for candidates of the memory_page_pass job. The page pass later reads each selected workspace's pages with the deploy model and retires repeated facts.

*Call graph*: 1 external calls (select).


##### `manifest`  (lines 860–1019)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest: the system's registration record for everything the memory extension offers. This is how tools, hooks, jobs, object kinds, search, and the memory surface become visible to the host application.

**Data flow**: It takes no inputs. It constructs ToolDef objects for memory tools, HookSpec objects for prompt and page-change events, JobSpec objects for scheduled background work, object and surface declarations, and the memory search provider. It returns one Manifest object containing all of that registration data.

**Call relations**: The host system calls this during extension loading. The returned manifest wires the handlers in this file to the wider runtime so later tool calls, prompt submissions, page changes, scheduler ticks, and surface requests reach the right memory code.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### Redis backend hub
The Redis hub manifest registers Redis-backed hub and terminal backend implementations with the main system.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup`

The core system normally has an in-process hub and terminal path, which means live messages and terminal connections are tied to one running server process. This file offers a Redis-based alternative. Redis is an external data service often used as a fast shared message pipe. By using Redis Streams, several server instances can share live frames instead of being trapped inside one process. The same Redis URL is also used for terminal routing, so a user’s terminal can still be reached even if the current work lands on a different pod or server than the one holding the actual connection.

The file is deliberately small because it is a manifest: a structured declaration that the larger application can discover. It defines the extension name and version, then registers two backend choices, both named "redis": one for the hub and one for terminal transport.

The two builder functions are gatekeepers. They refuse to create Redis-backed pieces unless a Redis URL is provided. This is important because it makes configuration mistakes fail early and clearly, rather than causing a confusing failure later when the system first tries to send a frame or reach a terminal. If the URL exists, the builders create the Redis-specific hub or terminal transport objects.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed live-frame hub. It is used when the main system has been configured to use the Redis hub backend instead of the default in-process one.

**Data flow**: It receives a Redis URL, or no URL. If no URL was provided, it stops immediately with a clear error message explaining that the Redis hub needs `hub.url`. If a URL is present, it passes that URL into `RedisStreamHub` and returns the new hub object, ready to publish and receive live frames through Redis.

**Call relations**: The manifest gives this function to `HubSpec` as the way to build the Redis hub. Later, when the application selects the `redis` hub backend, the core calls this builder; the builder then hands off to `RedisStreamHub.__init__` to create the actual Redis-based hub.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This function creates the Redis-backed terminal transport. It lets terminal traffic reach the right connected user even when work is admitted on a different server instance.

**Data flow**: It receives a Redis URL and a blob store. The blob store is shared storage for larger pieces of terminal-related data that should not live only inside one process. If the Redis URL is missing, the function raises a clear error. If the URL is present, it combines the URL and blob store to create and return a `RedisTerminals` transport.

**Call relations**: The manifest gives this function to `TerminalTransportSpec` as the builder for the `redis` terminal backend. When the application chooses that backend, the core calls this function, and it delegates the real setup to `RedisTerminals.__init__`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension’s formal registration information. The main system uses it to discover that this package provides a Redis hub backend and a Redis terminal transport backend.

**Data flow**: It takes no input. It gathers the fixed extension name, version, backend names, and builder functions, wraps them in `HubSpec` and `TerminalTransportSpec` records, and returns a `Manifest` object that the core system can read.

**Call relations**: This is the entry point the extension exposes to the host application. It creates the hub and terminal transport specifications, then packages them into a `Manifest` so the wider system knows which backend names are available and which builder function to call when one is selected.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Hosted sites
The sites package marker and manifest register the hosted-site tools, prompts, skills, subagents, surfaces, storage, object type, and background job.

### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project may want to refer to code inside `extensions/sites/ufo_ext_sites` using normal Python import paths. Without this file, some Python versions or tooling may not recognize the folder as a package, which could make imports fail or make the extension harder to discover. Think of it like a label on a drawer: the drawer may contain useful files, but the label tells the rest of the system that the drawer belongs to the organized cabinet. Since the file is empty, it does not set up configuration, expose helper functions, or run any startup code. Its job is structural rather than behavioral.


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup / extension load`

Think of this file as the packing list for the sites extension. When the system loads extensions, it needs a clear answer to: “What new abilities does this extension bring, and how should they be plugged in?” This file answers that in one place.

It names the extension, reads a prompt section from disk, points to bundled skill folders, and gathers together the pieces that make website building work. Those pieces include tools an agent can call, delegation tools for asking a child agent to build a site, profiles for those child agents, a “surface” that shows a hosted site to viewers, and a “site” object kind that chat can recognize and re-open.

It also registers two skills: one for general website building and one for application homepages. A skill is a bundle of instructions and files an agent can load before doing a task. Finally, it schedules a background cleanup-style job that releases a homepage page once ownership should move from a main agent to the chat application.

Without this file, the code for building and serving sites could exist, but the platform would not know to offer it. The extension would be like a box of parts with no label or assembly instructions.

#### Function details

##### `manifest`  (lines 48–67)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the sites extension. The manifest is the system-readable declaration of everything this extension contributes, such as website tools, site display support, agent profiles, skills, prompts, conversation slots, and a scheduled job.

**Data flow**: It starts with constants and imported extension pieces: names, version, prompt text read from the prompts folder, skill folder paths, tool lists, object definitions, subagent profiles, the site surface, and homepage-release job settings. It packages those into a Manifest object. While doing that, it creates small wrapper objects for the prompt section, each skill path, and the scheduled job. The result is one complete Manifest that the host system can load.

**Call relations**: The extension loader calls this function when it wants to discover what the sites extension provides. Inside, it constructs PromptSection, SkillSpec, JobSpec, and Manifest objects, and it asks unreleased_main_homepage_workspaces for the set of workspaces that may need the homepage-release job. The returned manifest is then used by the wider platform to make the sites tools, skills, subagents, surface, object kind, and scheduled job available at the right times.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, unreleased_main_homepage_workspaces).


### Shell and web clients
The UFO shell and web portal package markers and manifests register browser-facing routes, surfaces, permissions, jobs, and feature flags.

### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like putting a label on a folder so the rest of the project can say, “load something from this folder.” Without this file, some Python tooling or older import rules might not recognize `extensions/ufo/ufo_ext_ufo` as a package, which could make imports fail even though the actual useful code exists in nearby files. Because the file is empty, it does not run setup code, expose shortcuts, or change any state. Its value is structural: it helps the extension fit into Python’s module system.


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

This is the extension’s sign on the front desk. When the larger system looks for installed extensions, it needs a small, standard answer to questions like: “What is this extension called?”, “What version is it?”, and “What user-facing routes should I expose?” This file provides that answer.

The extension is named `ufo` and versioned as `0.1.0`. Its main job is to create a `Manifest`, which is a description the core system can read. Inside that manifest, it declares one `SurfaceSpec`. A “surface” is the public area where the extension meets users or clients, like a service counter. Here, that surface uses `SURFACE_UFO`, exposes the routes listed in `ROUTES`, and uses `resolve_workspace` to identify which workspace a request belongs to.

Without this file, the UFO extension might exist on disk but the host would not know how to attach it to the application. Its routes would not be registered, and the UFO shell client would have no declared surface to connect to.

#### Function details

##### `manifest`  (lines 16–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the UFO extension. The host system uses this to learn the extension’s name, version, surface, routes, and workspace-identification function.

**Data flow**: It starts with constants and imported pieces: the extension name, version, route list, surface name, and workspace resolver. It wraps the surface details into a `SurfaceSpec`, then places that inside a `Manifest`. The result is a complete description object returned to whoever is loading the extension.

**Call relations**: During extension loading, the host calls this function to ask, “What do you provide?” The function creates the surface description first, then hands that to the manifest object so the core system can mount the UFO routes and know how to identify the workspace for incoming requests.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. That means code elsewhere can refer to this directory using package-style imports, such as importing modules from `ufo_ext_web`.

There is no code inside this file, so it does not start anything, configure anything, or change program behavior directly. Its value is structural: it gives the surrounding web extension folder a clear identity in Python’s module system. Without it, some tooling or older Python import setups might not recognize the folder as a package, which could make imports fail or behave inconsistently.

An everyday analogy is a label on a drawer. The label does not contain the tools, but it tells people and systems that this drawer is a named place where tools can be found.


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup`

The web portal is one way people interact with UFO, alongside things like Slack or the command line. This file tells the shared extension system exactly what the web portal offers and what the core system should turn on when the extension is installed. Without it, the core system would not know that the web portal has pages to serve, tools it may use, conversation slots it can show, or scheduled work it needs done.

Most of the file is a clear declaration rather than active logic. It names the extension, sets its version, and lists feature flags. A feature flag is a switch that lets operators turn parts of the portal on or off, such as the Code app, Wiki app, Memory tab, Skills tabs, or an alternate “lanes” shell. Declaring the flags here matters because deployment checks can make sure the environment only defines flags the portal actually understands.

The `manifest` function then builds the full manifest object. It says the web extension connects to member accounts, uses web access tools, exposes a browser home surface with its routes, and reads member context. It also registers two background jobs: one that creates short titles for untitled conversations, and one that seeds homepage content for workspaces that do not have it yet. In everyday terms, this file is the portal’s “front desk sign-up sheet” for the rest of the platform.

#### Function details

##### `manifest`  (lines 72–98)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the structured description the core platform reads to install and run the portal. Someone would use it when the extension system asks, “What does this extension provide?”

**Data flow**: It starts with constants and imported pieces: the extension name and version, web routes, access tools, feature flags, conversation slots, surface setup, and job handlers. It packages those into a `Manifest` object. The result tells the platform that the web extension has a home page surface, can connect member accounts, can read member context, has declared tools and flags, and needs two scheduled jobs. While building the jobs, it asks helper functions for the sets of workspaces that still need conversation titles or homepage seeding.

**Call relations**: When the extension framework loads this module, it calls `manifest` to get the web portal’s registration details. Inside, `manifest` creates a `SurfaceSpec` to describe the browser-facing web surface, creates two `JobSpec` entries for scheduled background work, calls `untitled_conversation_workspaces` to find conversations that need titles, calls `unseeded_agent_workspaces` to find workspaces that need homepage setup, and finally hands everything to `Manifest` so the core system can mount routes, expose tools, honor flags, and schedule the jobs.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).
