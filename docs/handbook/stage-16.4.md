# Memory Store, Recall, and Condensation  `stage-16.4`

This stage is the memory system’s shared “library desk.” It is behind-the-scenes support used while the agent is working: before a reply it can recall useful context, during work it can save new facts, and later background jobs clean and summarize what was saved.

The manifest is the front sign and schedule board. It tells the larger system which memory tools agents may call, which automatic hooks should run before replies, and which cleanup or summary jobs should run in the background. The shared memory contract in core defines a common way to search and browse saved memories, so other parts of the system do not need to know the exact storage details. The events file keeps the names and size limits for recall events in one place, so every component reports recall the same way.

The store is the main filing cabinet and search desk. It saves memories, indexes source pages, filters unsafe or stale results, and avoids noisy duplicates. The condenser is the editor. It turns rough saved fragments into clearer facts, summaries, profiles, and readable wiki-style pages.

## Files in this stage

### Extension Registration
Manifest and shared recall definitions expose memory tools, hooks, jobs, contracts, and event metadata to the rest of the system.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, then active during tool calls, prompt hooks, page-change handling, and scheduled jobs`

This file is the front desk and timetable for the memory feature. It tells the host application: “Here are the memory tools, here is when to run them, here are the kinds of objects this extension owns, and here are the background jobs that keep everything useful.”

The memory system has two main user-facing tools. One searches remembered facts and synced source pages. The other writes a new durable memory item, like adding a row to a long-lived wiki. A third admin tool asks the system to rebuild facts derived from synced pages.

It also adds an automatic recall hook. Before the model answers a user message, the hook searches for relevant stored memory and injects a short “Relevant memory” note into the model’s context. This is deliberately best-effort: if recall is slow or broken, the conversation continues instead of failing.

The file also registers page-change hooks. When synced pages change, one hook indexes them for search, while another asks a model to turn page content into durable fact rows. Finally, it defines scheduled jobs that index new memories, merge old similar facts, remove duplicates, write page summaries, update people profiles, and curate whole memory pages.

#### Function details

##### `_date_bound`  (lines 213–224)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date or date-time string into a precise UTC time boundary for memory search. It lets a user search only memories created within a requested time window.

**Data flow**: It receives a string such as "2026-01-31" or a full ISO date-time, plus a flag saying whether this is the end of the range. If the value is missing, it returns nothing. If it is a bare end date, it moves the boundary to the next midnight so that the whole named day is included.

**Call relations**: memory_search_handler uses this before searching, so the date filters sent by the tool caller become real start and end times. The actual parsing is delegated to the standard date-time parser.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 233–311)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Searches memory across several focused queries at once, then combines remembered items and matching source-page snippets into one result list. This is the shared search workflow used by the agent tool and by other extensions that need memory search.

**Data flow**: It receives search phrases, a source reader that describes who is allowed to read what, and optional start and end dates. It asks the memory store to search both stored memory rows and indexed source pages, runs those searches in parallel, removes duplicates, and returns MemoryMatch objects that include text, kind, subject, date, and a reference to open the full item or page later.

**Call relations**: memory_search_handler creates this service when the memory_search tool is called. The service talks to the memory store through store_for, uses parallel gathering so all query paths run together, and wraps each hit in standard objects that the rest of the platform understands.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 313–316)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which classes of memory items can be listed or filtered by consumers. This keeps the public list of memory classes tied to the store’s actual type definition.

**Data flow**: It reads the allowed ItemClass type choices and returns them as a tuple of strings. Nothing else is changed.

**Call relations**: Other parts of the system can ask the search provider what kinds are listable instead of hard-coding a second copy of that list. It uses Python’s type helper to read the literal choices.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.body_max_chars`  (lines 318–322)

```
def body_max_chars(self) -> int
```

**Purpose**: Reports the maximum allowed length for a memory item body. This lets user interfaces or other callers enforce the same length rule as the write tool.

**Data flow**: It reads the shared memory body length constant and returns that number. It does not touch storage or perform validation itself.

**Call relations**: This supports consumers that need to prepare valid memory writes before calling the store. The actual write path uses the same limit through the input model.


##### `MemorySearchService.list_recent`  (lines 324–374)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Returns a paged list of recent live memory items for selected subjects, newest first. It is for browsing memory without doing a text search.

**Data flow**: It receives subjects, a page size, optional item kinds, and an optional cursor saying where the previous page stopped. It builds a database query for non-retired, non-superseded memory rows in the current workspace, applies paging, and turns each row into a MemoryMatch.

**Call relations**: This is the browsing side of the memory search provider. It relies on the shared listing helpers page_query and page_of so memory lists page through results the same way other platform lists do.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 377–384)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one memory search hit into a simple line of text for the agent to read. It includes the snippet, its kind, and, when available, a reference and date.

**Data flow**: It receives a MemoryMatch. It builds a line like a bullet point, adds the object reference and creation date if they exist, and returns the final string.

**Call relations**: memory_search_handler calls this for each returned match before sending the tool result back. It is the last formatting step between structured search results and plain tool output.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 387–407)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the memory_search tool that an agent can call during a conversation. It searches stored memories and source documents, then returns readable matches.

**Data flow**: It receives the tool context and validated search arguments. It parses optional date filters, builds a MemorySearchService from the extension context, searches using the caller’s source-reading permissions, and returns either “No matching memory” or a text block of formatted matches.

**Call relations**: manifest registers this as the handler for the memory_search tool. It calls _date_bound for time filters, MemorySearchService.search for the real lookup, and match_line to turn matches into tool output.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 410–424)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the memory_update tool that records a new durable memory item. Agents use it when something should be remembered beyond the current conversation.

**Data flow**: It receives the tool context and a validated memory write request. It decides the subject from the current audience, packages the body, class, kind, confidence, and optional source reference into a MemoryWrite, commits it to the memory store, and returns a short confirmation.

**Call relations**: manifest registers this as the side-effecting memory_update tool. It hands the actual database write to the memory store obtained through store_for.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 427–509)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically looks up relevant memory just before the model responds to a user prompt. Its job is to quietly give the model useful background without making the conversation depend on recall succeeding.

**Data flow**: It receives a hook context. If the event is not a user prompt, or if it is an internal machine-only root turn, it does nothing. Otherwise it searches memory for the prompt text, filters out items that should only be suggested as topics, truncates long lines and the total injection, logs what happened, and returns an InjectContext containing the memory text when there is something to inject.

**Call relations**: manifest registers this on the user_prompt_submit hook as best-effort work. It calls recall_subjects to decide which memory subjects are visible, uses SourceReader to describe read permissions, asks the store to recall matches, and hands an InjectContext back to the hook chain when recall succeeds.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 512–521)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that turns newly committed memory items into searchable index chunks. This is what lets later searches find recently written memories by meaning, not just by exact text.

**Data flow**: It receives an extension context with indexing and embedding backends. If those backends are missing, it fails clearly. Otherwise it creates a MemoryIndexer with the index, embedding service, transaction function, text chunker, and page state tracker, then runs it.

**Call relations**: manifest registers this as the memory_index scheduled job. It delegates the actual indexing work to MemoryIndexer.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 524–540)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to batches of synced page changes by indexing page text and updating the memory extension’s mirror of those pages. This makes changed source pages searchable.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it ignores it. Otherwise it checks that indexing and embedding backends are available, builds a PageIndexer, applies the delivered page changes, and returns no hook output.

**Call relations**: manifest registers this as one of the page_change hooks. The core runner supplies the page-change batches; this function hands them to PageIndexer for embedding and indexing.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 543–554)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to synced page changes by deriving durable fact memories from the changed pages. This turns raw documents into concise memory rows the agent can recall later.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. If no model is wired, it raises an error so the page-change cursor does not move past unprocessed pages. Otherwise it builds a FactDeriver and applies it to the changed pages.

**Call relations**: manifest registers this as a second page_change hook, separate from page indexing. It uses store_for to write memory rows and FactDeriver to perform the model-based extraction.

*Call graph*: 2 external calls (__init__, store_for).


##### `rebuild_page_facts_handler`  (lines 566–582)

```
async def rebuild_page_facts_handler(ctx: ToolContext, args: RebuildPageFactsInput) -> ToolResult
```

**Purpose**: Implements the admin-only tool that queues all synced pages for fact derivation again. It is used when page-derived memory rows need to be rebuilt across the workspace.

**Data flow**: It receives the tool context and empty validated arguments. It checks that the caller is a workspace admin, deletes the stored cursor used by derive_facts, and returns a message saying the rebuild has been queued. It does not rewrite facts immediately.

**Call relations**: manifest registers this as the rebuild_page_facts tool. By deleting the derive_facts cursor, it causes the normal page-change derivation flow to replay pages from the beginning on later ticks.

*Call graph*: calls 1 internal fn (speaker_is_admin); 2 external calls (__init__, __init__).


##### `consolidate_memory`  (lines 585–593)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled consolidation job that groups older related facts into higher-level summaries. This keeps memory from becoming a long pile of overlapping small facts.

**Data flow**: It receives an extension context. It requires an embedding backend, then creates a MemoryConsolidator with embedding, database transaction access, workspace ID, and model access, and runs it.

**Call relations**: manifest registers this as the memory_consolidate job. The job candidate query decides which workspaces are worth running; this function performs the run for one chosen workspace.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 596–604)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled duplicate cleanup job for memory rows. It collapses repeated tool-written items so the newest useful copy remains instead of many near-identical ones.

**Data flow**: It receives an extension context. It requires an embedding backend, builds a MemoryDeduper with embedding, transaction access, workspace ID, and the raw store, then runs it.

**Call relations**: manifest registers this as the memory_dedup job. Candidate selection finds workspaces with likely duplicate backlogs, and MemoryDeduper does the actual comparison and cleanup.

*Call graph*: 1 external calls (__init__).


##### `write_memory_sections`  (lines 607–612)

```
async def write_memory_sections(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that writes or refreshes section paragraphs for memory pages. These paragraphs summarize the facts under a subject-and-kind band, like a short introduction above a group of wiki rows.

**Data flow**: It receives an extension context, builds a SectionWriter with database transaction access, workspace ID, and model access, and runs it. The writer reads the live facts and writes the section text.

**Call relations**: manifest registers this as the memory_section scheduled job. The candidate query chooses workspaces where there are enough facts, or stale section paragraphs, to justify the pass.

*Call graph*: 1 external calls (__init__).


##### `write_memory_overview`  (lines 615–620)

```
async def write_memory_overview(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that writes the top overview paragraph for a memory page. This gives the page a concise opening summary instead of starting only with individual rows.

**Data flow**: It receives an extension context, creates an OverviewWriter using transaction access, workspace ID, and model access, and runs it. The writer reads relevant facts and updates the overview paragraph.

**Call relations**: manifest registers this as the memory_overview scheduled job. It runs after section writing in the nightly schedule so page-level prose is refreshed in the same maintenance window.

*Call graph*: 1 external calls (__init__).


##### `write_member_profiles`  (lines 623–628)

```
async def write_member_profiles(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that writes member profile summaries, such as a person’s role and current focus. This turns shared facts into readable people entries.

**Data flow**: It receives an extension context, creates a ProfileWriter with transaction access, workspace ID, and model access, and runs it. The writer reads workspace facts and roster information, then updates profile data.

**Call relations**: manifest registers this as the memory_people scheduled job. The candidate query looks for workspaces with shared facts, because without facts there is nothing useful to write into profiles.

*Call graph*: 1 external calls (__init__).


##### `curate_memory_pages`  (lines 631–636)

```
async def curate_memory_pages(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the whole-page curation pass that reads a subject’s memory page and retires rows that repeat one another. This is a higher-level cleanup than simple duplicate detection.

**Data flow**: It receives an extension context, creates a PagePass with transaction access, workspace ID, and model access, and runs it. The pass reads enough facts for a page and marks redundant rows retired where appropriate.

**Call relations**: manifest registers this as the memory_page_pass job. It is marked as needing the deploy model because its purpose is to read a whole page in one model pass.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 639–644)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with memory items not yet embedded for search. It prevents the indexing job from running where there is nothing to index.

**Data flow**: It creates a SQL query selecting distinct workspace IDs from memory rows whose embedding digest is missing. The result is not executed here; it is handed to the job system as a candidate finder.

**Call relations**: manifest passes this function into owner_candidates for the memory_index job. The job scheduler uses the resulting query to decide which workspace owners should run the indexer.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 647–664)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces with enough old live facts to make consolidation worthwhile. This avoids spending model and embedding work on workspaces that cannot produce a useful cluster.

**Data flow**: It computes an age cutoff from the current time, then creates a SQL query for workspaces with at least the configured number of old, live, non-page-derived fact rows. It returns the query for the scheduler to use later.

**Call relations**: manifest uses this as the candidate finder for the memory_consolidate job through owner_candidates. The actual consolidation is done later by consolidate_memory.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 667–688)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces with likely duplicate memory rows old enough for scheduled cleanup. Fresh writes are left to the immediate commit path instead of this background sweep.

**Data flow**: It computes an age cutoff, then creates a SQL query grouping live tool-written memory rows by workspace, subject, and class. Groups with enough old copies become candidates, and the query returns distinct workspace IDs.

**Call relations**: manifest uses this as the candidate finder for the memory_dedup job. dedup_memory later performs the actual duplicate sweep for selected workspaces.

*Call graph*: 2 external calls (now, select).


##### `_class_count`  (lines 691–695)

```
def _class_count(item_class: ItemClass) -> sa.Function[int]
```

**Purpose**: Creates a SQL count expression for rows of one memory item class inside a grouped query. It is a small helper for deciding whether a workspace has enough facts or existing paragraphs to process.

**Data flow**: It receives an item class, builds a conditional database count for rows matching that class, and returns the count expression. It does not execute a query.

**Call relations**: _summarizable_workspaces and _overviewable_workspaces use this helper while building their candidate queries. It keeps their grouped counts clear and consistent.

*Call graph*: called by 2 (_overviewable_workspaces, _summarizable_workspaces); 1 external calls (case).


##### `_summarizable_workspaces`  (lines 698–721)

```
def _summarizable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces where section paragraphs need attention. A workspace qualifies if a section has enough facts to summarize or if an existing section paragraph may need removal or rewriting.

**Data flow**: It creates a SQL query over live fact and section rows, groups them by workspace, subject, and memory kind, counts facts and section paragraphs, and returns distinct workspace IDs that meet the work conditions.

**Call relations**: manifest uses this as the candidate finder for the memory_section job. It calls _class_count to measure facts and existing section paragraphs within each group.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_overviewable_workspaces`  (lines 724–746)

```
def _overviewable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces where the shared page overview needs attention. A workspace qualifies if it has enough shared facts for an overview, or if an overview exists but the facts have fallen below the writing threshold.

**Data flow**: It creates a SQL query over live shared-subject fact and overview rows, groups by workspace, counts the relevant classes, and returns workspace IDs that need the overview pass.

**Call relations**: manifest uses this as the candidate finder for the memory_overview job. It calls _class_count to check both the fact count and whether an overview paragraph already exists.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_peopled_workspaces`  (lines 749–763)

```
def _peopled_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces where member profiles might be written. A workspace only qualifies if it has at least one live shared fact.

**Data flow**: It creates a SQL query selecting distinct workspace IDs from live shared fact rows. It returns the query without running it.

**Call relations**: manifest uses this as the candidate finder for the memory_people job. write_member_profiles later does the actual profile-writing work.

*Call graph*: 1 external calls (select).


##### `_curatable_workspaces`  (lines 766–782)

```
def _curatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces with enough fact rows on a subject to justify a whole-page curation pass. This keeps the expensive page-reading job away from tiny pages.

**Data flow**: It creates a SQL query over live fact rows, groups by workspace and subject, keeps groups with at least the configured minimum row count, and returns distinct workspace IDs.

**Call relations**: manifest uses this as the candidate finder for the memory_page_pass job. curate_memory_pages later runs the PagePass for selected workspaces.

*Call graph*: 1 external calls (select).


##### `manifest`  (lines 785–911)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the contract between this memory extension and the host system. It names the tools, hooks, jobs, object kinds, search provider, and web surface routes the extension provides.

**Data flow**: It creates ToolDef entries for memory search, memory update, and page-fact rebuild; HookSpec entries for recall and page-change processing; JobSpec entries for indexing, consolidation, deduplication, summaries, profiles, and curation; plus object, memory-search, and surface registrations. It returns one Manifest object containing all of those declarations.

**Call relations**: The host calls this during extension loading. The returned Manifest tells the platform which handlers to call during tool use, prompt submission, page changes, scheduled job ticks, memory-provider lookup, and memory surface routing.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `core/src/ufo/memory.py`

`data_model` · `cross-cutting`

This file is a small but important boundary between memory consumers and memory providers. A memory provider is any extension that can store or find past notes, events, or other remembered items. Without this boundary, every caller would need to know the private details of each memory system, and swapping or adding a provider would be much harder.

The central result shape is `MemoryMatch`, a plain data record for one search hit. It carries the kind of memory, the text snippet to show, and, when available, a durable object reference that can be opened later. It may also include when the item was created and what subject it belongs to.

`MemorySearchProvider` is a protocol, meaning “anything with these methods counts.” It says a provider must support two ways of finding memories: searching by query text, and listing recent items by readable subject set. Listing uses a cursor, which is like a bookmark in a changing list, so new items do not cause readers to skip or repeat rows.

`MemorySearch` is a thin wrapper around one selected provider. It does not search itself; it forwards each request to the provider. Think of it as a standard plug socket: consumers plug into this shape, and different memory backends can fit behind it.

#### Function details

##### `MemorySearchProvider.search`  (lines 39–45)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This defines how a memory provider should search stored memories using one or more text queries. A caller uses it when they want relevant remembered material, optionally limited to a time range.

**Data flow**: It receives query strings, a `SourceReader` that represents the readable source context, and optional start and end times. A concrete provider is expected to look through the memories it can read, filter or rank them according to the queries and time limits, and return a tuple of `MemoryMatch` results.

**Call relations**: This is the provider-side promise behind `MemorySearch.search`. Consumers call the wrapper, and the wrapper hands the request to whatever provider implements this method.


##### `MemorySearchProvider.list_recent`  (lines 47–53)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This defines how a provider should return recent memory items that a given set of subjects is allowed to read. It is for browsing recent memory, not searching by text.

**Data flow**: It receives the readable subjects, a maximum number of results, an optional set of memory kinds to include, and an optional cursor bookmark. A concrete provider is expected to return a `ListingPage` of `MemoryMatch` items plus any paging information needed to continue from the same point later.

**Call relations**: This is the provider-side promise behind `MemorySearch.list_recent`. The wrapper passes browsing requests here so callers do not need to know the provider’s storage layout.


##### `MemorySearchProvider.listable_kinds`  (lines 55–55)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This defines how a provider reports which kinds of memory items can be shown in a browse/filter interface. It lets user-facing code offer only valid choices.

**Data flow**: It takes no input. A concrete provider returns a tuple of kind names that it knows how to list.

**Call relations**: This method is exposed through `MemorySearch.listable_kinds`. A consumer can ask the selected provider what filters to present before calling `list_recent`.


##### `MemorySearchProvider.body_max_chars`  (lines 57–57)

```
def body_max_chars(self) -> int
```

**Purpose**: This defines how a provider reports the largest memory body it will accept or record. It helps forms and callers stop users at the same length the provider enforces.

**Data flow**: It takes no input. A concrete provider returns a number of characters as its maximum body size.

**Call relations**: This method is exposed through `MemorySearch.body_max_chars`. Code that collects or prepares memory text can ask the provider for its limit before submitting content elsewhere.


##### `MemorySearch.search`  (lines 66–73)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This asks the selected memory provider to search for memories matching the given queries. It exists so callers use one stable object instead of talking directly to provider implementations.

**Data flow**: It receives a reader, query strings, and optional start and end times. It passes those values unchanged to the provider’s `search` method, waits for the provider’s answer, and returns the resulting tuple of `MemoryMatch` items.

**Call relations**: This is the consumer-facing entry to memory search in this file. Its only handoff is to `MemorySearchProvider.search`, where the real provider-specific search work happens.


##### `MemorySearch.list_recent`  (lines 75–82)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This asks the selected memory provider for a page of recent memory items. It is used when the caller wants a recency-ordered list rather than query-based search.

**Data flow**: It receives readable subjects, a result limit, optional kind filters, and an optional cursor bookmark. It forwards all of that to the provider’s `list_recent` method and returns the `ListingPage` it gets back.

**Call relations**: This is the consumer-facing browse path. It delegates directly to `MemorySearchProvider.list_recent`, keeping pagination and storage details inside the provider.


##### `MemorySearch.listable_kinds`  (lines 84–85)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This asks the selected provider which memory kinds can be listed. It is useful for building filters or menus that match the provider’s actual abilities.

**Data flow**: It takes no input from the caller. It asks the provider for its listable kind names and returns them unchanged.

**Call relations**: This wrapper method simply relays the question to `MemorySearchProvider.listable_kinds`. Callers can use the answer before making a `list_recent` request.


##### `MemorySearch.body_max_chars`  (lines 87–88)

```
def body_max_chars(self) -> int
```

**Purpose**: This asks the selected provider for the maximum allowed size of a memory body. It helps callers avoid preparing or accepting text that the provider would later reject.

**Data flow**: It takes no input. It calls the provider’s `body_max_chars` method and returns the numeric character limit unchanged.

**Call relations**: This wrapper method relays the limit request to `MemorySearchProvider.body_max_chars`. It keeps provider-specific limits available through the same standard memory-search object.


### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension appears to report a structured event when it looks up past memories before producing a response. This file defines the small set of constants that make that reporting predictable. The event name, `memory.pre_response_recall`, is like a label on a form: other code can use it to recognize that this event means “memory recall happened before the response.” The two size limits protect the event data from becoming too large or messy. One caps how many recalled memory identifiers should be included, and the other caps how long an error class name should be if recall fails. Without this file, the event name and limits might be copied by hand in several places, which can lead to spelling mismatches, oversized event payloads, or inconsistent reporting. There are no functions here; it is a small shared vocabulary for the rest of the memory extension.


### Durable Memory Store
The store persists memories, maintains searchable indexes, filters stale or unsafe results, and supports recall operations.

### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file is the working heart of the memory system. It owns the database tables for stored memories, links between memories and source pages, and a small mirror of indexed pages. A memory can be written directly or derived from a synced page. Writes are intentionally simple: they save the row, but they do not immediately split text into searchable chunks or make embeddings, which are numeric representations of text meaning. That heavier work is done later by indexer jobs, so saving a memory stays fast.

When a user asks to recall memories, the file searches in two ways: word matching and vector matching, then blends the results. Think of it like asking both a librarian who matches exact words and another who understands meaning, then combining their ranked suggestions. It also checks permissions, drops superseded or retired facts, applies time decay for old facts, removes near-duplicates, and keeps one memory type from crowding out all others.

The file also indexes source pages themselves, so users can search original snippets. It is careful about safety: page-derived memories and page chunks are only published while the source page still has the same subject and revision. If a page changes or disappears, related chunks are removed or marked for re-checking.

#### Function details

##### `recall_subjects`  (lines 190–191)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience description into the set of subject labels that memory recall should search. A subject is the visibility bucket a memory belongs to.

**Data flow**: It receives an audience object → asks the shared audience helper to expand it into subject strings → returns those subjects as a frozen set.

**Call relations**: This is a small adapter around the shared audience logic. Other memory code can use its result as the subject filter passed into recall or source search.

*Call graph*: 1 external calls (audience_subjects).


##### `clip_to_word`  (lines 194–203)

```
def clip_to_word(text: str, limit: int) -> str
```

**Purpose**: Shortens text to a character limit without cutting through the middle of a word. It adds an ellipsis inside the limit so the reader can tell text was shortened.

**Data flow**: It receives text and a maximum length → if the text already fits, it returns it unchanged; otherwise it keeps as much as possible, backs up to the last space, trims dangling punctuation, and adds an ellipsis → returns the readable shortened text.

**Call relations**: Writers can use this before creating a MemoryWrite when they choose to trim oversized text instead of failing validation.


##### `_granted_link`  (lines 206–214)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds the database permission test for page-derived memories. A memory learned from source pages is readable if the reader has access to at least one linked source.

**Data flow**: It receives the source IDs the reader may access → builds a database EXISTS condition tied to the current memory row → returns that condition for larger queries to use.

**Call relations**: MemoryStore._untail_leg and MemoryStore._enrich include this condition when reading or scanning page-derived memories, so search results respect source permissions.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 254–323)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded operator-facing listing of recent stored memories in a workspace. It shows not just the text, but also indexing state and the same decay signals recall would use.

**Data flow**: It receives a transaction opener and workspace ID → reads the newest memory rows and their source links from the database → computes age, half-life, and decay factor using a single current time → returns MemoryInventoryItem objects.

**Call relations**: It uses _aware, half_life_days, and decay_multiplier so the inventory view reports the same timing math as recall. It is separate from recall because it is an inspection view, not a query-ranked search.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 326–327)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a datetime has timezone information. If a timestamp is missing a timezone, it treats it as UTC.

**Data flow**: It receives a datetime → checks whether it already has timezone info → returns it unchanged or with UTC attached.

**Call relations**: Inventory, recall enrichment, source search, and decay calculation use this helper so age comparisons are consistent and do not mix timezone-aware and timezone-less values.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.body_is_within_its_class_budget`  (lines 355–361)

```
def body_is_within_its_class_budget(self) -> Self
```

**Purpose**: Validates that a memory body fits the length budget for its class. Short list-style facts and longer overview paragraphs have different allowed sizes.

**Data flow**: It reads the MemoryWrite body and item_class → looks up the maximum length for that class → either returns the same object or raises a validation error.

**Call relations**: This runs automatically when a MemoryWrite is created, before MemoryStore.commit can save it. It keeps oversized text out of the durable memory table.


##### `MemoryWrite.page_origin_is_complete`  (lines 364–372)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Validates that page-derived memories identify their source page completely. A partial page origin would make later permission and revision checks unreliable.

**Data flow**: It checks whether page ID, page revision, and source ID are all present or all absent → returns the object if consistent → raises an error if only some are present.

**Call relations**: This protects MemoryStore.commit and later index/read logic from rows that claim to come from a page but cannot be safely verified.


##### `_fuse`  (lines 402–425)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines search hits from several search methods into one best score per owning row. It uses reciprocal-rank fusion, a ranking method that rewards items appearing high in multiple result lists.

**Data flow**: It receives several ordered hit lists and the vector-hit list → calculates rank-based scores per chunk, keeps the best chunk for each owner, and records the best vector similarity → returns a mapping from owner ID to fused rank, cosine score, and matched text.

**Call relations**: fuse_hits and fuse_recall both call this as their shared ranking core. It does not know whether it is ranking memories or source pages; callers decide how to interpret the result.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 428–441)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by blending lexical and vector hits into one list. It also blocks meaningless vector-only matches unless they clear a similarity floor.

**Data flow**: It receives word-search hits, vector-search hits, and a limit → fuses them with _fuse → filters weak vector-only results when no words matched → returns the top Fused results.

**Call relations**: MemoryStore.search_sources calls this after asking the index for page hits. It prepares the ordered page IDs that source search then verifies against the database and current page state.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 444–472)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending rank-based relevance with raw semantic closeness. It also includes a temporary word-search leg for newly written memories that have not been indexed yet.

**Data flow**: It receives lexical hits, vector hits, unindexed-tail hits, and a limit → fuses all legs → normalizes rank scores, mixes in cosine similarity, applies a floor only when no words matched anywhere → returns top Fused memory candidates.

**Call relations**: MemoryStore.recall calls this before reading rows back from the database. Its output is later filtered for permissions, page freshness, supersession, and time windows.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 490–496)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Chooses how quickly a fact should fade in recall ranking. Only fact-class memories decay; summaries and other classes stay relevance-only.

**Data flow**: It receives an item class and memory kind → if the item is not a fact, returns None → otherwise returns the configured half-life for that kind, falling back to the fact default.

**Call relations**: decay_multiplier and inventory call this so ranking and inspection use the same recency rules.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 499–511)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Computes the multiplier that lowers a fact’s recall score as it gets older, scaled by confidence. This is how a stale low-confidence fact naturally falls behind newer or stronger ones.

**Data flow**: It receives item class, memory kind, confidence, an effective date, and current time → finds the half-life → for non-decaying items returns 1.0; for facts, computes confidence times age decay → returns the multiplier.

**Call relations**: decay_factor delegates to this for recalled rows, and inventory uses it to display the same multiplier an item would receive during recall.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 514–517)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the shared decay calculation to a Recalled memory object. It picks the memory’s as-of date, or created date if as-of is missing.

**Data flow**: It receives a recalled item and current time → extracts class, kind, confidence, and effective date → calls decay_multiplier → returns the factor.

**Call relations**: MemoryStore._shortlist calls this while turning enriched candidates into final ranked recall results.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 525–527)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns a memory body into small three-word fingerprints used for near-duplicate detection. This gives the code a cheap way to compare whether two bodies say almost the same thing.

**Data flow**: It receives body text → lowercases it, splits it into words, groups nearby words into three-word phrases → returns a frozen set of those phrases.

**Call relations**: drop_near_duplicates calls this for each candidate it considers. The shingles are compared with Jaccard overlap, which means overlap divided by total unique shingles.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 530–556)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes recall candidates whose text is almost the same as an earlier, higher-ranked candidate. This prevents repeated copies of one fact from wasting the limited memory slots shown to a user.

**Data flow**: It receives ranked recalled items and a number to keep → walks the list in order, builds word-shingles for each body prefix, skips items too similar to already kept ones → returns the distinct kept items.

**Call relations**: MemoryStore._shortlist calls this after decay ranking and before type diversity. It relies on _body_shingles for the text fingerprints.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 559–577)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one class of memory from filling nearly all recall results. It first caps how many of each class can enter, then backfills if the result would otherwise be too short.

**Data flow**: It receives recalled rows and a limit → admits rows in rank order while each class is under its cap → stores overflow rows → fills remaining slots from overflow if needed → returns at most the requested limit.

**Call relations**: MemoryStore._shortlist calls this as the final shaping step after duplicate removal.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 580–590)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Converts episodic memories into browseable topic pointers instead of injecting their full body as context. Episodic memory acts like a breadcrumb, not a quoted fact.

**Data flow**: It receives a recalled item and its result index → if the item is not episodic, returns it unchanged; if it is episodic, returns a copy with a pointer-style body and recall_mode set to topic.

**Call relations**: MemoryStore.recall applies this to final shortlist entries before returning them to the caller.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 617–743)

```
async def commit(self, write: MemoryWrite) -> UUID
```

**Purpose**: Saves or updates one memory row without doing indexing work inline. It uses a content-based ID so the same exact memory text for the same subject and class lands on the same row.

**Data flow**: It receives a MemoryWrite → computes a stable UUID from workspace, subject, class, and body → upserts the memory row, resets indexing only when the page binding changed, clears supersession on revival, and records source-page links when present → returns the memory ID.

**Call relations**: Fact derivation and tool writes use this as the durable write path. MemoryIndexer later sees rows with no embedding digest and turns them into searchable chunks.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 745–866)

```
async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None
```

**Purpose**: Retires page-derived facts that a page no longer stands behind. If the same fact is still supported by another linked page, it keeps the memory row and only removes the stale page link.

**Data flow**: It receives a page ID and optionally the set of memory IDs still kept for that page → deletes stale memory_source links → either deletes orphaned memory rows and their index chunks, or repoints surviving rows to another source link and marks them due for re-index checks → returns nothing.

**Call relations**: The page fact deriver calls this after committing the current facts for a page. It hands deleted memory IDs to the index backend so their searchable chunks are removed.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 868–907)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Answers a memory recall query for a reader. It combines indexed search, newly written unindexed rows, permission checks, freshness checks, decay, duplicate removal, diversity, and episodic pointer conversion.

**Data flow**: It receives query text, allowed subjects, a result limit, optional time bounds, and a source reader → gets readable source IDs, gathers lexical/vector/tail hits, fuses them, enriches them from the database, shortlists them in a worker thread, rewrites episodic items as pointers → returns Recalled items.

**Call relations**: This is the main read path for memory recall. It coordinates _source_ids, _legs, _untail_leg, fuse_recall, _enrich, _shortlist, and as_topic_pointer.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 909–924)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Turns an enriched candidate pool into the final set of recall results. It applies recency decay, removes near-duplicates, and enforces type diversity.

**Data flow**: It receives enriched recalled items, a limit, and current time → multiplies each score by its decay factor, sorts by score, drops near-duplicates, applies class caps → returns the final shortlist.

**Call relations**: MemoryStore.recall runs this through asyncio.to_thread, meaning in a worker thread, so CPU-heavy comparison work does not block the async request loop.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 926–992)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches the original synced source pages and returns matching snippets. It uses the same word-and-vector search style as memory recall, then verifies that each page is still current and readable.

**Data flow**: It receives query, subjects, limit, optional time window, and a source reader → searches page chunks through _legs, fuses hits, reads matching mem_page rows, checks current readable page states, and returns SourceMatch objects in rank order.

**Call relations**: This is the source-page counterpart to MemoryStore.recall. It calls fuse_hits for ranking and _readable_states to enforce source access and page freshness.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 994–1000)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Gets the set of source IDs the current reader is allowed to read. Source-derived memory cannot be safely shown without this permission authority.

**Data flow**: It receives a SourceReader → if no readable-source callback is wired, raises an error → otherwise calls it and returns the readable source IDs.

**Call relations**: MemoryStore.recall calls this before tail scanning and row enrichment, because both need to know which page-derived memories the reader may see.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 1002–1014)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main index searches for a query: lexical search for matching words and vector search for matching meaning. It embeds the query first because vector search needs numbers, not raw text.

**Data flow**: It receives query, subjects, owner kind, and limit → asks _embed_query for an embedding → asks the index for lexical hits → asks the index for vector hits when an embedding exists → returns both hit lists.

**Call relations**: MemoryStore.recall uses this for memory-item hits, and MemoryStore.search_sources uses it for page hits. It hides the shared search setup from both callers.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 1016–1072)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Searches the newest unindexed memory rows by simple word counting. This makes a just-saved fact recallable before the background indexer has embedded it.

**Data flow**: It receives query terms, subject filters, a limit, and readable source IDs → scans a bounded number of newest due rows that are live and authorized → counts term occurrences in each body → returns sorted Hit objects for rows with matches.

**Call relations**: MemoryStore.recall passes this tail leg into fuse_recall beside indexed lexical and vector hits. It uses _granted_link for source-derived permission checks.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 1074–1082)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Converts a non-empty query into an embedding vector for semantic search. If embedding fails, it logs the failure and lets recall continue with word search only.

**Data flow**: It receives query text → returns an empty tuple for blank text → otherwise calls the embedding backend → returns the first vector, or an empty tuple on failure or no result.

**Call relations**: MemoryStore._legs calls this before vector search. Its graceful failure keeps recall usable even when the embedding service has a problem.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 1084–1164)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused hit IDs into full recalled memory objects, while applying database-side safety filters. It removes unreadable, superseded, retired, out-of-window, or stale page-derived rows.

**Data flow**: It receives fused hits, allowed subjects, readable source IDs, and optional time bounds → reads matching memory rows under workspace, subject, lifecycle, and permission filters → checks page-derived rows against current page state → returns Recalled objects in fused order.

**Call relations**: MemoryStore.recall calls this after fuse_recall. It uses _granted_link for source grants and _aware for timestamp consistency.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1166–1173)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads current page states, but only through the source-aware permission path. It is used when source search must prove a page is still readable and current.

**Data flow**: It receives page IDs and a source reader → returns an empty dict for no IDs → raises an error if no readable-page callback is wired → otherwise returns the readable current states.

**Call relations**: MemoryStore.search_sources calls this after finding candidate page IDs, before returning snippets to the caller.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1176–1189)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a MemoryStore from the extension context. It fails early if required index or embedding backends are not available.

**Data flow**: It receives an ExtensionContext → checks that index and embed are wired → copies workspace, transaction, page-state, and source-permission callbacks into a MemoryStore → returns that store.

**Call relations**: Higher-level extension code uses this factory to get the correctly scoped memory workflow instead of constructing MemoryStore by hand.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1213–1215)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing pass for due memory items. It claims a batch, then indexes or settles each item one by one.

**Data flow**: It starts with no direct input → calls _claim_due to get due MemoryItem records → passes each item to _index_item → returns when the batch is processed.

**Call relations**: This is the background job entry for memory-item indexing. It delegates claiming and per-item decisions to the private methods.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1217–1253)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically claims memory rows that still need indexing. The claim prevents overlapping indexer runs from embedding the same row at the same time.

**Data flow**: It computes a lease cutoff time → selects rows with no embedding digest and no active claim, up to a batch limit → marks them claimed in the database → returns them as MemoryItem objects.

**Call relations**: MemoryIndexer.run calls this first. MemoryIndexer._index_item later clears the claim through _settle when the item reaches a terminal decision.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1255–1292)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Decides whether one claimed memory item should be published to the search index, withheld, or removed from the index. It protects against indexing stale page-derived content.

**Data flow**: It receives a claimed MemoryItem → checks whether it is retired or publishable → deletes chunks and settles if not publishable; otherwise creates chunks and embeddings when missing → rechecks the row binding and page state → settles only if nothing changed underneath it.

**Call relations**: MemoryIndexer.run calls this for each claimed item. It uses _publishable for safety checks, chunk_embed_upsert for chunking and embedding, and _settle to mark completed work.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1294–1303)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Checks whether a memory body may be placed in the index. Direct memories are publishable; page-derived memories must still match the page’s current subject and revision.

**Data flow**: It receives subject, page ID, and revision → returns true immediately for non-page memories → otherwise reads current page state and compares subject and revision → returns true or false.

**Call relations**: MemoryIndexer._index_item calls this before and after indexing work so stale page-derived facts do not occupy search candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1305–1329)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory item as decided by writing a digest of its body and clearing the claim. This removes it from the due-for-indexing queue.

**Data flow**: It receives the MemoryItem originally claimed → computes a sha256 digest of its body → updates the row only if key fields and the claim still match → clears embedding_claimed_at and stores the digest.

**Call relations**: MemoryIndexer._index_item calls this after publishing or deliberately withholding an item. The guarded update avoids settling a row that changed during indexing.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1352–1354)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the page search index. It processes each change independently.

**Data flow**: It receives a tuple of PageChange objects → loops over them → calls _apply for each → returns after all changes are processed.

**Call relations**: The page-change runner calls this with batches. PageIndexer._apply contains the actual per-page indexing and cleanup decisions.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1356–1414)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Updates the indexed chunks and mirror row for one source page change. It deletes stale page chunks, indexes current page bodies, and records the page revision that was indexed.

**Data flow**: It receives one PageChange → reads current page state, marks left-behind facts due for re-check, handles tombstones or stale changes by deleting index/mirror data, otherwise chunks and embeds the page body, rechecks that the page did not change, then upserts mem_page → returns nothing.

**Call relations**: PageIndexer.apply calls this for every change. It calls _unsettle_left_behind_facts first so memory-item chunks tied to old page revisions are re-evaluated by MemoryIndexer.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1416–1444)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory facts as needing indexing review when their source page has moved on or disappeared. This lets the memory indexer withdraw chunks for facts no longer supported by the live page revision.

**Data flow**: It receives a page ID and optional current page state → builds a condition for memory rows from that page that no longer match the live subject/revision, or all rows if the page is gone → clears their embedding digest and claim fields → returns nothing.

**Call relations**: PageIndexer._apply calls this before handling each page change. The actual removal from the memory-item index is then performed later by MemoryIndexer._index_item.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### Memory Condensation
Condensation turns noisy source-derived fragments into cleaner facts, summaries, profiles, sections, and curated memory pages.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background memory cleanup`

This file is the memory system’s “editorial desk.” Other parts of the system collect raw pages and memory rows; this file decides how to turn those into durable facts, summaries, and readable page sections. It uses language models for tasks that need judgment, such as extracting facts from source pages or writing a short overview. It uses embeddings, which are number lists that represent meaning, for cheaper similarity checks such as finding near-duplicates.

The main flow starts with FactDeriver, which reads changed source pages and asks a model to extract concrete facts. Later background jobs clean and reshape those facts. MemoryConsolidator groups older related facts and writes one summary. MemoryDeduper finds repeated tool-written rows and points older copies at the newest one. SectionWriter and OverviewWriter rewrite the prose paragraphs that sit above facts, so the visible page reads like a current summary instead of an old log. ProfileWriter writes the People band: each member’s role and current focus. PagePass reads an entire subject page and retires rows that make the page worse, usually because another row already says the same thing.

A key safety idea runs through the file: model calls happen before database write transactions, and writes check that the rows have not changed meanwhile. This keeps long model calls from holding locks and avoids overwriting newer information.

#### Function details

##### `section_headings`  (lines 178–184)

```
def section_headings(subject: str) -> dict[MemoryKind, str]
```

**Purpose**: Chooses the human-readable section titles for a memory page. Shared workspace pages use team-oriented wording, while personal pages use wording addressed to one person.

**Data flow**: It receives a subject string, checks whether that subject means the shared workspace, and returns the matching dictionary of memory kinds to section headings.

**Call relations**: SectionWriter uses this when deciding which bands can be summarized and when telling the model what heading a paragraph will appear under. PagePass uses it when building the full page that the model will curate.

*Call graph*: called by 3 (_page, _sections, _summarize); 1 external calls (subject_shared).


##### `live_page_link`  (lines 246–259)

```
def live_page_link() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a fact still matches the current version of the source page it came from. This prevents summaries from relying on facts whose source page has since changed.

**Data flow**: It reads no rows itself. It returns a SQL condition connecting memory rows to page rows by page id, workspace, subject, and revision.

**Call relations**: member_servable uses it to decide whether a page-derived row can still be shown. PagePass uses it directly because it only curates rows still backed by the exact current page revision.

*Call graph*: called by 2 (_page, member_servable); 1 external calls (and_).


##### `member_servable`  (lines 262–274)

```
def member_servable() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition for rows that a member could actually see. Agent-written rows always count; page-derived rows count only if their source page is still current.

**Data flow**: It produces a SQL filter. The filter accepts rows with no source page, or rows whose source page still matches through live_page_link.

**Call relations**: SectionWriter, OverviewWriter, and ProfileWriter use this filter before summarizing facts, so their prose is based only on rows that readers can also see.

*Call graph*: calls 1 internal fn (live_page_link); called by 4 (_facts, _facts, _facts, _sections); 2 external calls (or_, select).


##### `ExtractedFact.within_row_budget`  (lines 314–315)

```
def within_row_budget(cls, body: str) -> str
```

**Purpose**: Keeps a model-extracted fact short enough to fit the memory row limit. It trims at a word boundary so the saved text does not end halfway through a word.

**Data flow**: It receives a fact body string, clips it to the configured maximum length, and returns the clipped body.

**Call relations**: Pydantic calls this automatically when validating ExtractedFact objects created from the model’s tool output during FactDeriver._extract.

*Call graph*: 1 external calls (clip_to_word).


##### `FactDeriver.apply`  (lines 351–366)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Processes a batch of changed source pages and turns eligible pages into memory facts. It also retires facts from deleted pages or machine-status pages that should no longer contribute memory.

**Data flow**: It receives page changes, asks the store which pages are still live, filters out tombstones, tiny pages, and machine-status streams, then sends eligible pages in bounded batches for derivation. For pages that produced replacement facts, it supersedes the old page-derived facts.

**Call relations**: The page-change runner calls this after source pages are delivered. It hands batches to FactDeriver._derive, then tells the store which old facts are replaced.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 368–407)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> dict[UUID, frozenset[UUID]]
```

**Purpose**: Extracts and commits facts for one bounded group of pages, but only if each page is still at the same revision that triggered the work. This avoids writing facts from stale page content.

**Data flow**: It receives page changes, re-checks their current stored page states, asks _extract for facts, commits each valid fact as a MemoryWrite, and returns the ids of the newly landed facts grouped by page id.

**Call relations**: FactDeriver.apply calls this for each page batch. It depends on FactDeriver._extract for the model judgment and then hands the resulting writes to the MemoryStore.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 409–486)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: Asks the model to record concrete facts from a group of source pages. It validates the model’s structured tool output and removes repeated facts within the same reply.

**Data flow**: It turns pages into a compact JSON payload, sends a forced tool-call request to the model, reads the returned facts list, validates each entry, drops invalid entries, and keeps the fuller version when two entries restate the same claim.

**Call relations**: FactDeriver._derive calls this before committing anything. It uses _restates to collapse duplicate-looking facts inside the model response.

*Call graph*: calls 1 internal fn (_restates); called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `_content_words`  (lines 489–490)

```
def _content_words(body: str) -> frozenset[str]
```

**Purpose**: Pulls out the meaningful words from a sentence-like string. Small filler words such as “the” and “and” are removed so similarity checks focus on substance.

**Data flow**: It receives text, splits it into lowercase word-like pieces, removes filler words, and returns the remaining set of words.

**Call relations**: _restates calls this for two fact bodies before comparing how much meaning they share.

*Call graph*: called by 1 (_restates); 1 external calls (split).


##### `_restates`  (lines 493–505)

```
def _restates(kept: str, candidate: str) -> bool
```

**Purpose**: Decides whether two extracted facts are probably saying the same claim. It is used to avoid saving two versions of the same fact from one model response.

**Data flow**: It receives two text bodies, reduces each to content words, checks whether the smaller one has enough words, and returns true if most of its content appears in the other.

**Call relations**: FactDeriver._extract calls this while walking validated model facts, replacing an earlier duplicate with a longer version when useful.

*Call graph*: calls 1 internal fn (_content_words); called by 1 (_extract).


##### `cosine`  (lines 508–516)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how close two embedding vectors are in meaning. A result near 1 means the texts are very similar; 0 is returned if either vector is empty in effect.

**Data flow**: It receives two tuples of numbers, computes their dot product divided by their lengths, and returns a floating-point similarity score.

**Call relations**: MemoryConsolidator._clusters and MemoryDeduper._clusters use this as the basic test for grouping related or duplicated rows.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 546–555)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic job that turns clusters of older, related tool-written facts into one semantic summary. It does nothing if no model is configured.

**Data flow**: It reads aged candidate facts, groups them by subject, embeds each group, clusters the vectors in a worker thread, and consolidates each large enough cluster.

**Call relations**: The scheduler calls this periodically. It coordinates _aged_facts, _buckets, _embed, _clusters, and _consolidate as one cleanup pass.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 557–588)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: Finds old live facts that are eligible for consolidation. It deliberately ignores page-derived facts, already superseded facts, and retired facts.

**Data flow**: It opens a transaction, queries the memory table for old live tool-written fact rows in this workspace, and returns them as _AgedFact records.

**Call relations**: MemoryConsolidator.run calls this first to get the pool of facts that may be clustered.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 590–599)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: Groups consolidation candidates by subject, because facts about different subjects should not be summarized together.

**Data flow**: It receives _AgedFact objects, builds subject-based groups, sorts each group newest first, applies a per-subject size cap, and returns the grouped facts.

**Call relations**: MemoryConsolidator.run calls this after loading aged facts, before asking for embeddings.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 601–605)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Turns fact bodies into embedding vectors so related facts can be found by meaning rather than exact wording.

**Data flow**: It receives facts, sends clipped fact text to the embedding client, and returns a mapping from fact id to vector.

**Call relations**: MemoryConsolidator.run calls this before clustering. The resulting vectors are passed to _clusters.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 607–628)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: Groups facts whose embeddings are close enough to be summarized together. It uses a greedy newest-first approach, like placing each card into the first matching pile.

**Data flow**: It receives facts and their vectors, walks facts newest first, compares each to existing cluster heads with cosine, and returns clusters of facts.

**Call relations**: MemoryConsolidator.run runs this in a worker thread so CPU-heavy similarity math does not block the main async loop.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 630–694)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: Writes one summary for a cluster and marks the original facts as superseded by that summary. It checks the rows again under a lock so it does not summarize facts that changed meanwhile.

**Data flow**: It receives a cluster, asks _summarize for a summary, creates a new semantic memory row, verifies the donor rows still match, inserts the summary, and updates donors to point at it.

**Call relations**: MemoryConsolidator.run calls this for each acceptable cluster. It hands the language work to _summarize and performs the database replacement itself.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 696–706)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: Asks the model to write a short summary of a cluster of related facts. The output is trimmed to the same paragraph budget used elsewhere.

**Data flow**: It receives a model and facts, sends clipped fact bodies as JSON, gets a text completion, strips it, budgets it with _to_overview_budget, and returns the final summary.

**Call relations**: MemoryConsolidator._consolidate calls this before opening the write transaction.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_to_overview_budget`  (lines 709–721)

```
def _to_overview_budget(summary: str) -> str
```

**Purpose**: Cuts a generated paragraph down to the allowed size while preserving whole sentences when possible. This keeps summaries readable and bounded.

**Data flow**: It receives a summary string, keeps sentences until word or character limits would be exceeded, and falls back to word clipping if even the first sentence is too long.

**Call relations**: MemoryConsolidator, SectionWriter, and OverviewWriter use this after model text generation so all summary-like rows obey the same size rule.

*Call graph*: called by 3 (_summarize, _write, _summarize); 1 external calls (clip_to_word).


##### `_recency`  (lines 724–725)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: Provides a stable sorting key for facts by creation time and id. This makes newest-first ordering deterministic even when timestamps match.

**Data flow**: It receives an _AgedFact and returns a pair of its creation time and id.

**Call relations**: MemoryConsolidator uses this helper when ordering facts before bucketing and clustering.


##### `_Group.key`  (lines 745–746)

```
def key(self) -> tuple[str, str]
```

**Purpose**: Returns the deduplication group’s ordering identity. It lets the deduper walk groups predictably from one tick to the next.

**Data flow**: It reads the group’s subject and item class and returns them as a tuple.

**Call relations**: MemoryDeduper.run compares this key with the stored cursor to choose the next group to sweep.


##### `_Group.fingerprint`  (lines 749–750)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: Summarizes whether a deduplication group has changed since the last completed sweep. It uses the number of live copies and the latest update time.

**Data flow**: It reads the group’s copy count and latest timestamp, converts the timestamp to text, and returns a small JSON-friendly list.

**Call relations**: MemoryDeduper.run stores and compares this value in the scoped key-value store to skip groups that have not changed.


##### `MemoryDeduper.run`  (lines 794–806)

```
async def run(self) -> None
```

**Purpose**: Runs one step of the periodic duplicate cleanup job. It sweeps one subject-and-class group per tick, so work is spread out rather than done all at once.

**Data flow**: It loads candidate groups, reads the last cursor, chooses the next group, stores the new cursor, skips unchanged groups by fingerprint, otherwise deduplicates the group and records its new fingerprint.

**Call relations**: The scheduler calls this periodically. It coordinates _groups, _cursor, and _dedup_group, using the scoped store to remember progress.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 808–820)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: Reads and validates the stored cursor for the deduplication walk. It treats malformed cursor data as an error instead of silently starting over.

**Data flow**: It receives a JSON value from storage, returns an empty tuple for no cursor, returns a subject/item-class tuple for valid data, or raises if the shape is wrong.

**Call relations**: MemoryDeduper.run calls this before choosing which group to process next.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 822–848)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: Finds groups of live tool-written rows that have enough copies to be worth checking for duplicates.

**Data flow**: It queries the memory table by subject and item class, ignores young rows, page-derived rows, sections, superseded rows, and retired rows, and returns _Group records with counts and latest update times.

**Call relations**: MemoryDeduper.run calls this to know what groups exist and whether each group may need a sweep.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 850–855)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: Deduplicates one group of memory rows. It embeds the row text, clusters near-duplicates, and collapses clusters with enough copies.

**Data flow**: It receives a _Group, reads its live copies, embeds their text, clusters them in a worker thread, and calls _collapse for each duplicate cluster.

**Call relations**: MemoryDeduper.run calls this for the selected group after cursor and fingerprint checks pass.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 857–872)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: Reads the live rows in one deduplication group, newest first. The newest row becomes the winner if a duplicate cluster is found.

**Data flow**: It receives a group, builds the live-row filter with _live_group, queries ids and bodies, caps the result size, and returns _LiveCopy records.

**Call relations**: MemoryDeduper._dedup_group calls this before embedding and clustering the group.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 874–881)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Creates embedding vectors for the rows in a duplicate-check group. It batches requests so very large groups do not become one huge embedding call.

**Data flow**: It receives live copies, sends clipped bodies to the embedding client in batches, and returns a mapping from row id to vector.

**Call relations**: MemoryDeduper._dedup_group calls this before _clusters compares row meanings.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 883–912)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: Groups near-duplicate rows by embedding similarity. Because copies arrive newest first, the first row in each cluster is the one that survives.

**Data flow**: It receives live copies and vectors, compares each copy with existing cluster heads using cosine, joins a close cluster or starts a new one, and returns all clusters.

**Call relations**: MemoryDeduper._dedup_group runs this in a worker thread, then asks _collapse to stamp duplicate clusters.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 914–942)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: Marks every duplicate in a cluster as superseded by the newest copy. It locks and rechecks rows first so it does not point rows at a winner that changed or disappeared.

**Data flow**: It receives a group and a duplicate cluster, treats the first copy as the head, locks all expected rows, compares their current bodies with what was embedded, and updates donor rows to point to the head.

**Call relations**: MemoryDeduper._dedup_group calls this after clustering. It uses _live_group to make sure it only touches rows that are still live and eligible.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 944–953)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: Builds the database filter for live, old-enough, tool-written rows in one deduplication group.

**Data flow**: It receives a _Group and returns SQL conditions for workspace, subject, item class, no source page, no superseder, not retired, and older than the safety age.

**Call relations**: MemoryDeduper._live_copies and _collapse use this so reading and updating use the same eligibility rules.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


##### `_rewrite_in_place`  (lines 974–1024)

```
async def _rewrite_in_place(connection: AsyncConnection, workspace_id: UUID, standing: _Standing, paragraph: str, confidence: int) -> None
```

**Purpose**: Writes a new standing paragraph and supersedes the previous live paragraph in that exact spot. This keeps each section or overview to one current paragraph.

**Data flow**: It receives a database connection, workspace id, standing identity, paragraph text, and confidence. It locks existing live paragraphs for that spot, inserts the new paragraph, and updates the old ones to point at it.

**Call relations**: SectionWriter.run and OverviewWriter.run call this after a model has produced new prose.

*Call graph*: called by 2 (run, run); 5 external calls (execute, insert, select, update, uuid4).


##### `_retire_standing`  (lines 1027–1055)

```
async def _retire_standing(connection: AsyncConnection, workspace_id: UUID, standing: _Standing) -> None
```

**Purpose**: Removes a standing paragraph when its section or overview no longer has enough facts to justify it. This prevents old summaries from floating above rows that no longer support them.

**Data flow**: It receives a connection, workspace id, and standing identity, then marks matching live paragraphs retired and clears embedding-related fields so they leave the search index.

**Call relations**: SectionWriter.run calls this for bands that no longer qualify. OverviewWriter.run calls it when the shared page has too few facts for an overview.

*Call graph*: called by 2 (run, run); 2 external calls (execute, update).


##### `SectionWriter.run`  (lines 1081–1104)

```
async def run(self) -> None
```

**Purpose**: Refreshes the short paragraph that opens each memory section. It also retires paragraphs for sections that no longer have enough live facts.

**Data flow**: It exits if there is no model, finds sections worth writing, finds existing standing paragraphs, retires stale ones, reads facts for each current section, asks the model for a paragraph, and rewrites that paragraph in place.

**Call relations**: The scheduler calls this periodically. It coordinates _sections, _standing, _facts, _summarize, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 6 internal fn (_facts, _sections, _standing, _summarize, _retire_standing, _rewrite_in_place).


##### `SectionWriter._sections`  (lines 1106–1139)

```
async def _sections(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds the subject-and-kind bands that currently have enough visible facts to deserve a section paragraph.

**Data flow**: It queries live servable facts, groups them by subject and memory kind, keeps groups above the minimum count, verifies that each kind has a heading, and returns _Standing identities.

**Call relations**: SectionWriter.run calls this to decide what should be written now. It uses member_servable and section_headings to match what readers can actually see.

*Call graph*: calls 2 internal fn (member_servable, section_headings); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._standing`  (lines 1141–1159)

```
async def _standing(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds section paragraphs that are currently live, even if their facts no longer qualify. This lets the writer retire stale paragraphs.

**Data flow**: It queries live section rows grouped by subject and memory kind and returns their _Standing identities.

**Call relations**: SectionWriter.run compares this result with _sections; anything standing but no longer eligible is passed to _retire_standing.

*Call graph*: called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._facts`  (lines 1161–1182)

```
async def _facts(self, section: _Standing) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the live facts that belong under one section heading. These are the facts the model will summarize into that section’s paragraph.

**Data flow**: It receives a _Standing section, queries matching servable live facts newest first, caps the number read, and returns body-plus-confidence records.

**Call relations**: SectionWriter.run calls this for each eligible section before asking _summarize to write prose.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._summarize`  (lines 1184–1203)

```
async def _summarize(self, model: ModelAccess, section: _Standing, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write one concise paragraph for a section. It includes the section heading so the model knows what question the paragraph must answer.

**Data flow**: It receives a model, section identity, and facts, sends the heading and clipped facts as JSON, gets model text, and trims it through _to_overview_budget.

**Call relations**: SectionWriter.run calls this before opening the write transaction that replaces the old paragraph.

*Call graph*: calls 3 internal fn (complete, _to_overview_budget, section_headings); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `OverviewWriter.run`  (lines 1227–1248)

```
async def run(self) -> None
```

**Purpose**: Refreshes the shared workspace overview paragraph. If there are too few live facts, it retires the overview instead of leaving stale prose.

**Data flow**: It exits without a model, gathers shared facts, retires the overview if the count is below the threshold, otherwise reads the workspace domain, asks the model to write the overview, and rewrites it in place.

**Call relations**: The scheduler calls this periodically. It uses _facts, _write, _retire_standing, and _rewrite_in_place to keep exactly one live shared overview.

*Call graph*: calls 4 internal fn (_facts, _write, _retire_standing, _rewrite_in_place); 2 external calls (__init__, workspace_domain).


##### `OverviewWriter._facts`  (lines 1250–1271)

```
async def _facts(self) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the newest visible facts for the shared workspace page. These facts are the raw material for the company-level overview.

**Data flow**: It queries shared-subject live fact rows that are servable, not superseded, and not retired, orders them newest first, applies a cap, and returns body-plus-confidence records.

**Call relations**: OverviewWriter.run calls this before deciding whether to retire or rewrite the overview.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `OverviewWriter._write`  (lines 1273–1293)

```
async def _write(self, model: ModelAccess, domain: str | None, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write the workspace’s opening overview paragraph. The workspace domain is included when available so the prose can name the company context.

**Data flow**: It receives a model, optional domain, and facts, sends a compact JSON payload, gets a completion, strips it, trims it with _to_overview_budget, and returns the paragraph.

**Call relations**: OverviewWriter.run calls this after it has enough facts and before it opens the write transaction.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `WrittenProfile.within_role_budget`  (lines 1330–1331)

```
def within_role_budget(cls, role: str) -> str
```

**Purpose**: Keeps a generated member role short enough to stay phrase-like. It trims at a word boundary.

**Data flow**: It receives a role string, clips it to the configured role length, and returns the clipped value.

**Call relations**: Pydantic calls this while validating WrittenProfile entries returned by ProfileWriter._write.

*Call graph*: 1 external calls (clip_to_word).


##### `WrittenProfile.within_row_budget`  (lines 1335–1336)

```
def within_row_budget(cls, focus: str) -> str
```

**Purpose**: Keeps a generated member focus sentence within the normal memory row length. This prevents oversized profile text from being stored.

**Data flow**: It receives a focus string, clips it to the configured memory-body length, and returns the clipped value.

**Call relations**: Pydantic calls this while validating profile entries produced by the model in ProfileWriter._write.

*Call graph*: 1 external calls (clip_to_word).


##### `ProfileWriter.run`  (lines 1374–1389)

```
async def run(self) -> None
```

**Purpose**: Writes or refreshes each roster member’s role and current focus for the People band. It does nothing if no model is configured or no roster exists.

**Data flow**: It loads the roster, loads shared workspace facts, asks the model to write profiles, matches returned names back to roster members, and stores the matched entries.

**Call relations**: The scheduler calls this periodically. It coordinates _roster, _facts, _write, and _store.

*Call graph*: calls 4 internal fn (_facts, _roster, _store, _write).


##### `ProfileWriter._roster`  (lines 1391–1410)

```
async def _roster(self) -> tuple[_Rostered, ...]
```

**Purpose**: Reads the workspace roster in the same terms the seat system uses. It records each member’s email-like name and whether they are admin/member and seated/unseated.

**Data flow**: It opens a transaction, asks Seats for a snapshot, clips the roster to its limit, and returns _Rostered records.

**Call relations**: ProfileWriter.run calls this before asking the model to write people entries.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `ProfileWriter._facts`  (lines 1412–1429)

```
async def _facts(self) -> tuple[str, ...]
```

**Purpose**: Reads shared workspace facts that everyone can already see. This avoids using private person-specific rows to describe a colleague to others.

**Data flow**: It queries servable shared live facts, orders them newest first, limits the count, and returns the fact bodies.

**Call relations**: ProfileWriter.run passes these facts to _write as evidence for the model’s People-band output.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 1 external calls (select).


##### `ProfileWriter._write`  (lines 1431–1479)

```
async def _write(self, model: ModelAccess, roster: tuple[_Rostered, ...], facts: tuple[str, ...]) -> tuple[WrittenProfile, ...]
```

**Purpose**: Asks the model to produce one structured people entry per roster member. It validates each returned profile independently and drops invalid entries.

**Data flow**: It receives a model, roster, and facts, sends a forced tool-call request, reads the returned people list, validates entries as WrittenProfile objects, and returns the valid ones.

**Call relations**: ProfileWriter.run calls this after loading roster and facts. The returned entries are matched by name before _store writes them.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 4 external calls (__init__, __init__, __init__, dumps).


##### `ProfileWriter._store`  (lines 1481–1510)

```
async def _store(self, entries: tuple[tuple[UUID, WrittenProfile], ...]) -> None
```

**Purpose**: Writes profile entries into the memory_profile table, replacing the previous entry for each member. It uses an upsert, meaning insert if missing or update if already present.

**Data flow**: It receives member/profile pairs, stamps one written_at time, and for each member inserts or updates role, focus, and written time inside one transaction.

**Call relations**: ProfileWriter.run calls this after model output has been validated and matched to roster member ids.

*Call graph*: called by 1 (run); 3 external calls (now, insert, insert).


##### `admitted_curation`  (lines 1564–1599)

```
def admitted_curation(bands: tuple[tuple[int, ...], ...], retire: tuple[RetiredRow, ...]) -> AdmittedCuration
```

**Purpose**: Checks whether the page-curation model’s proposed retirements are safe enough to apply. It protects against deleting too much or deleting a row without a surviving duplicate.

**Data flow**: It receives the row indexes sent to the model by band and the model’s retire requests. It drops unknown ids, requires each retired row to name a surviving duplicate, enforces a per-band retirement limit, and returns admitted indexes or a refusal reason.

**Call relations**: PagePass._retiring calls this before converting model-selected row indexes into real database ids.

*Call graph*: called by 1 (_retiring); 1 external calls (__init__).


##### `PagePass.run`  (lines 1647–1660)

```
async def run(self) -> None
```

**Purpose**: Runs the full-page curation pass for every subject with enough rows. It asks the model which page-derived rows make the page worse and retires only admitted ones.

**Data flow**: It exits without a model, finds candidate subjects, builds each subject’s page, asks the model to curate it, filters the suggested retirements through _retiring, and applies the accepted retirements.

**Call relations**: The scheduler calls this periodically. It coordinates _subjects, _page, _curate, _retiring, and _apply.

*Call graph*: calls 5 internal fn (_apply, _curate, _page, _retiring, _subjects).


##### `PagePass._subjects`  (lines 1662–1678)

```
async def _subjects(self) -> tuple[str, ...]
```

**Purpose**: Finds subjects whose pages are large enough to be worth full-page model review.

**Data flow**: It queries live unsuperseded fact rows grouped by subject, keeps subjects with at least the configured row count, orders them, and returns subject strings.

**Call relations**: PagePass.run calls this first to decide which pages to inspect.

*Call graph*: called by 1 (run); 1 external calls (select).


##### `PagePass._page`  (lines 1680–1726)

```
async def _page(self, subject: str) -> tuple[_Band, ...]
```

**Purpose**: Builds the page payload for one subject, organized by section. It includes only page-derived fact rows that still match their source page’s current revision.

**Data flow**: It receives a subject, loops over that subject’s section headings, reads live page-backed rows for each kind, reads the current section summary if any, assigns compact numeric indexes to rows, and returns bands.

**Call relations**: PagePass.run calls this before model curation. It uses section_headings and live_page_link so the payload matches the page a reader sees.

*Call graph*: calls 2 internal fn (live_page_link, section_headings); called by 1 (run); 3 external calls (__init__, __init__, select).


##### `PagePass._curate`  (lines 1728–1777)

```
async def _curate(self, model: ModelAccess, bands: tuple[_Band, ...]) -> CuratedPage
```

**Purpose**: Asks the model to judge which rows a full page would read better without. The model must answer through a structured tool call.

**Data flow**: It receives bands, builds a compact JSON payload with headings, summaries, row indexes, and clipped row text, sends it to the model, validates returned retire entries, and returns a CuratedPage.

**Call relations**: PagePass.run calls this after building a page. Its output is not trusted directly; _retiring checks what can safely be applied.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, dumps).


##### `PagePass._retiring`  (lines 1779–1790)

```
def _retiring(self, subject: str, bands: tuple[_Band, ...], retire: tuple[RetiredRow, ...]) -> frozenset[UUID]
```

**Purpose**: Turns admitted model row indexes into real memory row ids. If the proposed curation is too aggressive, it logs a warning and retires nothing for that subject.

**Data flow**: It receives a subject, bands, and requested retirements, calls admitted_curation, warns on refusal, and otherwise collects the database ids whose displayed indexes were admitted.

**Call relations**: PagePass.run calls this between _curate and _apply, making it the safety gate before any destructive write.

*Call graph*: calls 1 internal fn (admitted_curation); called by 1 (run); 1 external calls (warn).


##### `PagePass._apply`  (lines 1792–1808)

```
async def _apply(self, retiring: frozenset[UUID]) -> None
```

**Purpose**: Marks selected rows as retired and clears their embedding fields so they leave recall/search indexes. It only touches rows that are still live.

**Data flow**: It receives memory row ids, opens a transaction, and updates matching rows in this workspace to set retired_at, clear embedding data, and update the timestamp.

**Call relations**: PagePass.run calls this after _retiring approves a non-empty set of row ids.

*Call graph*: called by 1 (run); 1 external calls (update).
