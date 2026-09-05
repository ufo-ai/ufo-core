# Index, memory, and profile derivation  `stage-14.2`

This stage is shared behind-the-scenes support for memory and search. It takes source text, cuts it into smaller chunks, turns those chunks into embeddings, which are number lists that capture meaning, and stores them so agents can find useful information later.

The core indexing file defines the common rules for chunking, embedding, storing, and searching text. The default index stores and searches chunks in the local database, while the Turbopuffer file can send the same kind of work to an external search service. The OpenAI embedding file supplies the meaning-vectors, carefully splitting requests so they are not too large.

The memory files build on that search base. The manifest connects memory tools, automatic recall, page-change reactions, cleanup, and scheduled writing to the rest of the system. The store records facts and searches pages or remembered items. The condenser turns messy notes and synced pages into clean facts, summaries, profiles, and wiki pages. The events and runtime memory files define the shared event names and result shapes.

The enrichment files add optional, consent-based profile lookup: they track permission, choose live or replayed providers, store results, and surface short summaries later.

## Files in this stage

### Memory orchestration and condensation
Connects the memory extension to the runtime, turns raw material into usable knowledge, stores and searches memories, and defines emitted memory-event constants.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, prompt handling, page-change handling, scheduled background jobs`

This file is like the switchboard for the memory system. It does not contain every detail of storage, indexing, or summarizing, but it decides when those parts are used and exposes them to agents and the user interface. Without it, agents could not search or write long-term memory, submitted prompts would not get relevant memories added automatically, synced pages would not be indexed or turned into facts, and nightly memory cleanup or summary jobs would not run.

The file defines input shapes for memory tools, so callers must provide clear fields such as search queries, memory text, confidence, and optional dates. It provides tool handlers for searching memory, recording new facts, recording corrections, recording first-run setup information, and asking the system to rebuild facts derived from synced pages. It also defines a recall hook that runs just before the model answers: it searches for relevant memories, trims them to safe size limits, and injects them into the model context if possible.

For background work, it registers jobs that index memories, consolidate old facts into summaries, remove duplicates, write wiki-like section and overview paragraphs, update member profiles, and curate whole memory pages. Finally, the `manifest` function packages all of these declarations so the core system can discover and run the extension.

#### Function details

##### `_date_bound`  (lines 254–265)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: This helper turns an optional date string from a search request into a real time boundary. It lets users filter memory search results by when items were created.

**Data flow**: It receives a string such as `2026-01-31` or a full date-and-time, plus a flag saying whether this is an end boundary. If there is no string, it returns nothing. Otherwise it parses the string, assumes UTC time if no time zone was given, and for a plain end date moves the boundary to the next midnight so the whole day is included.

**Call relations**: The memory search tool handler calls this before running a search. It uses Python's date parser and time-delta helper, then hands the resulting start and end times to the search service.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 274–352)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the shared search workflow for memory. It searches both stored memory items and synced source-page snippets, then combines the results into one list an agent or extension can read.

**Data flow**: It receives one to three search queries, a source reader that says what subjects the caller may read, and optional date bounds. It asks the memory store to run recall searches and source-document searches in parallel. It then interleaves results from the different queries, removes duplicates, wraps each result with a reference to the memory item or page it came from, and returns a tuple of `MemoryMatch` objects.

**Call relations**: Tool handlers and dependent extensions use this service when they need memory search. Inside, it calls the memory store through `store_for`, runs parallel work with `asyncio.gather`, uses `zip_longest` to fairly merge per-query result lists, and creates `ObjectRef` and `MemoryMatch` objects for the outside world.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 354–357)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This tells callers which memory item classes can appear in a browsable memory listing. It avoids maintaining a second hand-written list.

**Data flow**: It reads the allowed item-class type definition and returns its possible string values as a tuple. It does not change any data.

**Call relations**: This belongs to the memory search provider service. It uses `get_args` to extract the allowed values from the type definition so listing filters stay aligned with the store's item classes.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 359–409)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This returns a page of recent live memory items for given subjects, newest first. It is for browsing memory directly, not searching by meaning.

**Data flow**: It receives readable subjects, a limit, optional item kinds, and an optional paging cursor. It builds a database query for non-retired, non-superseded memory items in the current workspace, applies the kind filter if present, fetches one page, and converts rows into `MemoryMatch` objects with stable references.

**Call relations**: This is the browse side of `MemorySearchService`. It uses SQLAlchemy to build the database query, then uses the shared listing helpers `page_query` and `page_of` so memory browsing pages behave like other paged listings in the system.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 412–419)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: This formats one memory search hit into a readable line for a tool result. It makes the result easy for an agent to inspect and, when available, gives a reference the agent can open later.

**Data flow**: It receives a `MemoryMatch`. It builds a line with the match kind and text, and if there is an object reference, it appends that reference and the creation date if known. It returns the finished string.

**Call relations**: The memory search tool handler calls this for every match before returning text to the agent. It does not call other project helpers; it is the final presentation step for search results.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 422–442)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: This is the actual handler behind the `memory_search` tool. It lets an agent ask for relevant memories and source snippets using focused search queries.

**Data flow**: It receives the tool context and validated search arguments. It checks that extension context is available, converts optional date strings to time bounds, builds a source reader for the caller, runs `MemorySearchService.search`, and returns either a no-results message or formatted result lines.

**Call relations**: The `manifest` function registers this as the handler for the `memory_search` tool. During a tool call, it uses `_date_bound`, asks the tool context for a source reader, creates a `MemorySearchService`, and formats each match with `match_line` before returning a `ToolResult`.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 445–459)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: This is the handler behind the `memory_update` tool. It records a new durable memory item so future turns can recall it.

**Data flow**: It receives the tool context and a memory write request. It checks for extension context, chooses the current effective audience as the memory subject, builds a `MemoryWrite` containing the text, class, kind, confidence, and source reference, commits it to the memory store, and returns a short confirmation.

**Call relations**: The `manifest` function registers this as a side-effecting tool because it changes stored memory. It uses `store_for` to reach the memory store and `MemoryWrite` to describe the item being saved.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_correction_handler`  (lines 462–480)

```
async def record_correction_handler(ctx: ToolContext, args: RecordCorrectionInput) -> ToolResult
```

**Purpose**: This records a corrected version of an existing memory item. It does not edit the old item directly; it writes a new fact that names what it corrects.

**Data flow**: It receives the current speaker context and correction input: the old memory item's ID and the replacement statement. It writes a new fact under the speaker's effective audience, uses standard confidence and kind values, stores a source reference pointing to the corrected memory, and returns a confirmation.

**Call relations**: The `manifest` function registers this as the portal-facing correction action. Later deduplication work can notice that the new item supersedes the old near-duplicate, but this handler's job is only to add the corrected row through `store_for` and `MemoryWrite`.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_first_run_handler`  (lines 483–499)

```
async def record_first_run_handler(ctx: ToolContext, args: RecordFirstRunInput) -> ToolResult
```

**Purpose**: This records the setup fact created during a workspace's first-run flow, such as what tools the team uses. It makes that onboarding information available to later memory recall.

**Data flow**: It receives the tool context and one body string. It writes that text as a standard fact under the current effective audience, tags it with the fixed first-run source reference, commits it to the memory store, and returns a confirmation.

**Call relations**: The `manifest` function registers this as a special side-effecting action for first-run setup. Like other write handlers, it reaches storage through `store_for` and describes the row with `MemoryWrite`.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 502–584)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook automatically adds relevant memory to the model's context just before the model responds to a submitted prompt. It is best-effort: if recall is slow or fails, the user turn continues without memory instead of failing.

**Data flow**: It receives a hook context. If the event is not a user prompt, or if it is a speakerless internal root turn, it returns nothing. Otherwise it works out readable subjects, searches memory for the prompt text within a soft timeout, drops topic-only results from the injected text, trims long items and the total injected block to size limits, logs what happened, and returns an `InjectContext` containing the memory lines if any survived.

**Call relations**: The `manifest` function registers this for `user_prompt_submit`. It calls `recall_subjects` to decide the memory scope, creates a `SourceReader`, uses `store_for(...).recall` to fetch matches, uses `asyncio.timeout` to keep recall from delaying the turn too much, and logs recall outcomes through the observability logger.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 587–596)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job indexes committed memory items so they can be searched by meaning. Indexing turns text into searchable chunks and embeddings, which are numeric representations used for similarity search.

**Data flow**: It receives an extension context. It verifies that both an index backend and an embedding backend are wired, builds a `MemoryIndexer` with those services, a transaction function, page state access, and a text chunker, then runs the indexer.

**Call relations**: The `manifest` function registers this as the `memory_index` job. The job candidate query `_items_awaiting_index` decides which workspaces need it, and this function hands the actual work to `MemoryIndexer`.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 599–615)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: This page-change hook indexes synced source pages and mirrors their state for memory search. It lets memory search find useful snippets from documents, not just hand-written memory rows.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it ignores it. Otherwise it checks that index and embedding services exist, builds a `PageIndexer`, applies the delivered page changes, and returns no injected hook outcome.

**Call relations**: The `manifest` function registers this for `page_change` events. The core runner supplies batches and owns the cursor; this function uses `PageIndexer` and `TextChunker` to process each delivered batch.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 618–629)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: This page-change hook turns changed source pages into durable fact memory items. It is the bridge from synced documents into the user's memory wiki.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. If no model is available, it raises an error so the cursor does not move past unprocessed pages. Otherwise it builds a `FactDeriver` from the memory store and model, applies the page changes, writes derived facts, and retires facts that were replaced.

**Call relations**: The `manifest` function registers this as a second `page_change` consumer, separate from page indexing. It uses `store_for` to access memory storage and hands model-based extraction to `FactDeriver`.

*Call graph*: 2 external calls (__init__, store_for).


##### `rebuild_page_facts_handler`  (lines 641–657)

```
async def rebuild_page_facts_handler(ctx: ToolContext, args: RebuildPageFactsInput) -> ToolResult
```

**Purpose**: This tool handler lets a workspace admin ask the system to derive facts from all synced pages again. It is for broad repair when page-derived memory reads badly.

**Data flow**: It receives the tool context and empty validated input. It checks extension context, verifies the speaker is an admin, deletes the stored cursor for fact derivation, and returns a message saying the rebuild has been queued. It does not immediately rewrite facts itself.

**Call relations**: The `manifest` function registers this as the `rebuild_page_facts` tool. By deleting the derivation cursor, it causes the `derive_facts` page-change consumer to replay pages on later runner ticks.

*Call graph*: calls 1 internal fn (speaker_is_admin); 2 external calls (__init__, __init__).


##### `consolidate_memory`  (lines 660–668)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job condenses older related facts into higher-level semantic summaries. It keeps memory useful as facts pile up over time.

**Data flow**: It receives an extension context, checks that the embedding backend exists, builds a `MemoryConsolidator` with embeddings, database access, workspace ID, and model, then runs it.

**Call relations**: The `manifest` function registers this as the `memory_consolidate` job. `_consolidatable_workspaces` selects workspaces with enough old live facts, and this function delegates the actual clustering and summary writing to `MemoryConsolidator`.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 671–679)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job removes duplicate memory clutter by retiring older repeated rows toward the newest copy. It keeps the memory wiki from filling with the same fact many times.

**Data flow**: It receives an extension context, checks that the embedding backend exists, builds a `MemoryDeduper` with embeddings, database access, workspace ID, and key-value store access, then runs it.

**Call relations**: The `manifest` function registers this as the `memory_dedup` job. `_dedupable_workspaces` picks workspaces with likely duplicate groups, and this function gives the work to `MemoryDeduper`.

*Call graph*: 1 external calls (__init__).


##### `write_memory_sections`  (lines 682–687)

```
async def write_memory_sections(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job writes or refreshes section paragraphs for the memory wiki. A section paragraph summarizes the facts under one subject-and-kind band.

**Data flow**: It receives an extension context, builds a `SectionWriter` with database access, workspace ID, and model, then runs it. The writer reads live facts and writes section-level prose where needed.

**Call relations**: The `manifest` function registers this as the `memory_section` job. `_summarizable_workspaces` decides where section writing may be useful, and this function delegates the model-written paragraph work to `SectionWriter`.

*Call graph*: 1 external calls (__init__).


##### `write_memory_overview`  (lines 690–695)

```
async def write_memory_overview(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job writes the opening overview paragraph for a memory page. It gives readers a short top-level summary before they inspect individual rows.

**Data flow**: It receives an extension context, builds an `OverviewWriter` with database access, workspace ID, and model, then runs it. The writer reads suitable shared facts and creates or updates the overview text.

**Call relations**: The `manifest` function registers this as the `memory_overview` job. `_overviewable_workspaces` selects workspaces needing overview work, and this function hands off to `OverviewWriter`.

*Call graph*: 1 external calls (__init__).


##### `write_member_profiles`  (lines 698–703)

```
async def write_member_profiles(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job writes profile information for workspace members, such as role and current focus, based on shared facts. It keeps the people view aligned with memory.

**Data flow**: It receives an extension context, builds a `ProfileWriter` with database access, workspace ID, and model, then runs it. The writer reads facts and roster information and updates stored member profiles.

**Call relations**: The `manifest` function registers this as the `memory_people` job. `_peopled_workspaces` selects candidate workspaces, and this function delegates the profile-writing pass to `ProfileWriter`.

*Call graph*: 1 external calls (__init__).


##### `curate_memory_pages`  (lines 706–711)

```
async def curate_memory_pages(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job performs a whole-page curation pass over memory facts. It uses the deploy model to read a subject's full page and retire rows that repeat one another.

**Data flow**: It receives an extension context, builds a `PagePass` with database access, workspace ID, and model, then runs it. The pass reads enough facts under a subject and decides which rows should remain active.

**Call relations**: The `manifest` function registers this as the `memory_page_pass` job and marks that job as needing the deploy model. `_curatable_workspaces` selects workspaces with pages large enough to justify this whole-page read.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 714–719)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with memory items that still need embeddings. It prevents the index job from running where there is no indexing backlog.

**Data flow**: It creates a SQL query selecting distinct workspace IDs from memory items whose embedding digest is missing. It returns the query object, not the rows themselves.

**Call relations**: The `manifest` function wraps this query with `owner_candidates` for the `memory_index` job. The job scheduler uses it to decide which workspace owners should receive indexing work.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 722–739)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the query that finds workspaces with enough old facts to consolidate. It avoids spending model and embedding work on workspaces where consolidation cannot yet produce a useful cluster.

**Data flow**: It computes an age cutoff from the current UTC time, then returns a SQL query for workspaces with at least the required number of live, non-page-derived facts older than that cutoff. Superseded and retired rows are excluded.

**Call relations**: The `manifest` function uses this as the candidate selector for the `memory_consolidate` job. It relies on the same minimum age and count rules used by the consolidator.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 742–763)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the query that finds workspaces with likely duplicate memory rows. It keeps the deduplication job focused on places where it can actually retire repeats.

**Data flow**: It computes an age cutoff from the current UTC time, then returns a SQL query for workspaces that have enough old live tool-written rows sharing the same subject and item class. It excludes section paragraphs, page-derived rows, superseded rows, and retired rows.

**Call relations**: The `manifest` function uses this as the candidate selector for the `memory_dedup` job. The query mirrors the deduper's own minimum-copy and minimum-age rules.

*Call graph*: 2 external calls (now, select).


##### `_class_count`  (lines 766–770)

```
def _class_count(item_class: ItemClass) -> sa.Function[int]
```

**Purpose**: This helper builds a SQL count for rows of one memory item class inside a grouped query. It lets candidate queries ask questions like 'how many facts' and 'how many section paragraphs' in the same group.

**Data flow**: It receives an item class and returns a SQL expression that counts only rows whose class matches it. It does not run the query itself.

**Call relations**: _summarizable_workspaces and _overviewable_workspaces call this while building their grouped candidate queries. It uses SQLAlchemy's conditional expression helper to make class-specific counts.

*Call graph*: called by 2 (_overviewable_workspaces, _summarizable_workspaces); 1 external calls (case).


##### `_summarizable_workspaces`  (lines 773–796)

```
def _summarizable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the query that finds workspaces where section paragraphs may need writing or removal. It catches both pages with enough facts to summarize and pages where an old section paragraph is still standing after facts dropped below the threshold.

**Data flow**: It returns a SQL query over live, non-retired fact and section rows, grouped by workspace, subject, and memory kind. A workspace is selected if any group has enough facts for a section or already has a section paragraph.

**Call relations**: The `manifest` function uses this as the candidate selector for the `memory_section` job. It calls `_class_count` to count facts and section paragraphs separately inside each group.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_overviewable_workspaces`  (lines 799–821)

```
def _overviewable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the query that finds workspaces where the memory page overview may need writing or removal. It focuses only on the shared workspace subject, because the overview describes the shared page.

**Data flow**: It returns a SQL query over live shared facts and overview rows. A workspace is selected if it has enough shared facts for an overview or already has an overview paragraph that may need refreshing or clearing.

**Call relations**: The `manifest` function uses this as the candidate selector for the `memory_overview` job. It calls `_class_count` to separately count facts and overview rows.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_peopled_workspaces`  (lines 824–838)

```
def _peopled_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the query that finds workspaces where member profile writing could have useful material. It selects workspaces that have at least one live shared fact.

**Data flow**: It returns a SQL query selecting distinct workspace IDs from live shared fact rows. Superseded and retired facts are ignored.

**Call relations**: The `manifest` function uses this as the candidate selector for the `memory_people` job. The profile writer later does the richer work of reading facts and roster details.

*Call graph*: 1 external calls (select).


##### `_curatable_workspaces`  (lines 841–857)

```
def _curatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the query that finds workspaces with memory pages large enough for a whole-page curation pass. It avoids running an expensive model read on tiny pages.

**Data flow**: It returns a SQL query over live facts, grouped by workspace and subject. A workspace is selected if any subject page has at least the required number of fact rows.

**Call relations**: The `manifest` function uses this as the candidate selector for the `memory_page_pass` job. The selected workspaces are later processed by `curate_memory_pages` through `PagePass`.

*Call graph*: 1 external calls (select).


##### `manifest`  (lines 860–1019)

```
def manifest() -> Manifest
```

**Purpose**: This declares the whole memory extension to the host system. It tells the system what tools, objects, hooks, jobs, search provider, and user-interface surface this extension offers.

**Data flow**: It creates and returns a `Manifest` object. Inside that object it names the extension version, defines tool entries and their handlers, exposes memory and profile object types, registers hooks for prompt submission and page changes, registers scheduled jobs with candidate selectors, declares the memory search provider, and exposes the memory surface routes.

**Call relations**: This is the file's main registration point. At startup the host reads it so it can call handlers such as `memory_search_handler`, `recall_hook`, `index_pages`, and the scheduled job functions at the right time. It constructs the framework objects such as `ToolDef`, `HookSpec`, `JobSpec`, `MemorySearchProviderSpec`, `SurfaceSpec`, and finally `Manifest`.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change consumption and periodic background maintenance`

The memory system stores many small rows of information, but raw rows are not enough for people or search to use well. This file is the “editorial desk” for that memory. It reads changed source pages, asks a language model to extract durable facts, and writes those facts into the memory store. Later background jobs clean up and reshape that store: they merge old related facts into a broader summary, collapse near-duplicate user or tool-written rows onto the newest copy, rewrite the short paragraph at the top of each wiki section, write the workspace overview, and produce a People profile for each roster member.

It also has a stricter page-wide pass. That pass shows a model a whole subject page at once and lets it retire rows that repeat other rows. It only retires; it does not write new prose, so there is a clear owner for each kind of text.

A recurring theme is safety. Model calls happen before database write transactions, so the database is not held open while waiting. Payloads are bounded so one huge workspace cannot create an unbounded model request. Old rows are usually marked as superseded or retired rather than deleted, like filing an old draft behind the current one. The result is memory that stays current, readable, and less repetitive.

#### Function details

##### `section_headings`  (lines 176–182)

```
def section_headings(subject: str) -> dict[MemoryKind, str]
```

**Purpose**: Chooses the section titles for a memory page. Shared workspace pages use team-facing headings, while personal member pages use headings addressed to that member.

**Data flow**: It receives a subject string, checks whether that subject is the shared workspace subject, and returns the matching dictionary of memory kinds to human-readable headings.

**Call relations**: SectionWriter uses it when deciding which bands can have section paragraphs and when telling the model what heading a paragraph will sit under. PagePass uses it to build the whole page that the curation model reads.

*Call graph*: called by 3 (_page, _sections, _summarize); 1 external calls (subject_shared).


##### `live_page_link`  (lines 244–257)

```
def live_page_link() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a page-derived memory row still matches the current version of its source page. This keeps summaries and curation from using facts that came from an old page revision.

**Data flow**: It reads no rows itself. It returns a SQL condition joining memory rows to page mirror rows by page id, workspace, subject, and revision.

**Call relations**: member_servable uses this condition for general live-memory reads. PagePass uses it directly because it only curates facts still backed by the current synced page.

*Call graph*: called by 2 (_page, member_servable); 1 external calls (and_).


##### `member_servable`  (lines 260–272)

```
def member_servable() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition for rows a member is allowed to see. Tool-written rows are always eligible; page-derived rows are eligible only if their source page is still live at the same revision.

**Data flow**: It combines two possibilities into one SQL condition: rows with no source page, or rows whose source page still matches through live_page_link.

**Call relations**: SectionWriter, OverviewWriter, and ProfileWriter use this condition before summarizing facts, so their prose describes the same rows a member could actually read.

*Call graph*: calls 1 internal fn (live_page_link); called by 4 (_facts, _facts, _facts, _sections); 2 external calls (or_, select).


##### `ExtractedFact.within_row_budget`  (lines 312–313)

```
def within_row_budget(cls, body: str) -> str
```

**Purpose**: Ensures a fact extracted by the model is not too long for a memory row. It trims at a word boundary so the stored text does not end in the middle of a word.

**Data flow**: It receives the proposed fact body, clips it to the memory row character budget, and returns the safe version.

**Call relations**: This validator runs when ExtractedFact is created from model output inside FactDeriver._extract. It protects the store from oversized model-produced text.

*Call graph*: 1 external calls (clip_to_word).


##### `FactDeriver.apply`  (lines 349–364)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Consumes a batch of changed source pages and turns eligible pages into stored memory facts. It also retires old facts for deleted pages or machine-status streams that should no longer contribute memory.

**Data flow**: It receives page changes, asks the store which pages are currently live, filters out tombstones, tiny pages, and ignored streams, then sends eligible pages in bounded groups for derivation. For pages that get replacement facts, it asks the store to supersede the old facts for that page.

**Call relations**: This is the public entry for the page-change consumer. It hands each eligible batch to FactDeriver._derive and uses the returned kept fact ids to retire only the facts that were truly replaced.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 366–405)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> dict[UUID, frozenset[UUID]]
```

**Purpose**: Derives and commits facts for one bounded group of pages, but only if each page is still at the revision being processed. This prevents facts from being written from stale page content.

**Data flow**: It receives page changes, rechecks their live page state, asks _extract for model-produced facts, validates that each page is still current just before writing, commits each fact as a MemoryWrite, and returns the stored row ids grouped by page id.

**Call relations**: FactDeriver.apply calls it for each page batch. It calls FactDeriver._extract for the model work and then hands safe facts to MemoryStore.commit.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 407–484)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: Asks the configured language model to read a small group of source pages and record concrete facts. It validates the model’s tool-call output and removes near-restatements within the same page.

**Data flow**: It turns pages into a compact JSON payload, sends a forced tool request to the model, reads the returned facts list, validates each entry as ExtractedFact, drops invalid entries, and returns the surviving facts.

**Call relations**: FactDeriver._derive calls this before any database write. It uses _restates to collapse duplicate fact phrasings from the same model reply.

*Call graph*: calls 1 internal fn (_restates); called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `_content_words`  (lines 487–488)

```
def _content_words(body: str) -> frozenset[str]
```

**Purpose**: Extracts the meaningful words from a sentence-like body of text. Common filler words such as “the” and “and” are removed so comparisons focus on substance.

**Data flow**: It receives text, lowercases and splits it on non-word characters, removes empty pieces and filler words, and returns a set of content words.

**Call relations**: _restates calls this for both pieces of text it compares. It is a small helper for duplicate detection during fact extraction.

*Call graph*: called by 1 (_restates); 1 external calls (split).


##### `_restates`  (lines 491–503)

```
def _restates(kept: str, candidate: str) -> bool
```

**Purpose**: Decides whether two extracted fact bodies are really the same claim with different wording. It protects the memory store from getting multiple rows for the same fact from one model response.

**Data flow**: It receives two text bodies, converts each to content words, checks whether the shorter one has enough meaningful words, then measures how much of it is covered by the other.

**Call relations**: FactDeriver._extract calls it while building the final list of extracted facts. If one fact restates another, the longer version is kept.

*Call graph*: calls 1 internal fn (_content_words); called by 1 (_extract).


##### `cosine`  (lines 506–514)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how close two embedding vectors are. An embedding is a list of numbers representing text meaning, and cosine similarity compares their direction rather than their size.

**Data flow**: It receives two numeric vectors, computes their dot product and magnitudes, and returns a similarity score. If either vector has no magnitude, it returns zero.

**Call relations**: MemoryConsolidator._clusters and MemoryDeduper._clusters use it to decide whether facts or copies are similar enough to group together.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 544–553)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic job that turns clusters of old, related tool-written facts into one semantic summary. If no model is configured, it does nothing.

**Data flow**: It reads aged candidate facts, groups them by subject, embeds each group, clusters similar facts in a worker thread, and asks _consolidate to replace each large enough cluster with a summary.

**Call relations**: This is the consolidator’s top-level flow. It coordinates _aged_facts, _buckets, _embed, _clusters, and _consolidate.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 555–586)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: Finds old live facts that are eligible for consolidation. It only reads tool-written facts, not facts derived from synced pages.

**Data flow**: It computes an age cutoff, queries memory_item for unsuperseded, unretired facts in the workspace older than that cutoff, and returns them as _AgedFact records.

**Call relations**: MemoryConsolidator.run calls it at the start of a consolidation pass. The returned facts become the raw material for bucketing, embedding, and summarizing.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 588–597)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: Groups aged facts by subject so facts about different people or pages are not merged together. It also limits each bucket to a bounded number of newest facts.

**Data flow**: It receives aged facts, collects them under their subject, sorts each group by recency, trims each group to the maximum bucket size, and returns ordered subject buckets.

**Call relations**: MemoryConsolidator.run calls it after reading candidates. Each bucket is then embedded and clustered separately.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 599–603)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Turns fact text into embeddings so related facts can be compared mathematically. This lets the job group by meaning, not just exact wording.

**Data flow**: It receives aged facts, sends clipped fact bodies to the embedding client, and returns a mapping from fact id to embedding vector.

**Call relations**: MemoryConsolidator.run calls it before clustering. MemoryConsolidator._clusters then reads the returned vectors.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 605–626)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: Clusters related facts using embedding similarity. It walks newest-first and attaches each fact to the first similar cluster head.

**Data flow**: It receives facts and their embeddings, compares each fact’s vector to existing cluster heads using cosine, and returns groups of related facts.

**Call relations**: MemoryConsolidator.run runs this in a worker thread so CPU-heavy comparison work does not block the async event loop. Clusters large enough are passed to _consolidate.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 628–692)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: Replaces one cluster of related facts with a single semantic summary. It carefully checks that the original facts have not changed before stamping them as superseded.

**Data flow**: It receives a cluster, asks _summarize for a paragraph, creates a new semantic memory row, locks and verifies the donor facts, then updates those facts to point at the new summary.

**Call relations**: MemoryConsolidator.run calls it for each cluster that passes the size threshold. It calls _summarize before opening the write transaction.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 694–704)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: Asks the model to write one concise summary from a cluster of related facts. The result is clipped to the same paragraph budget used by overview-style text.

**Data flow**: It receives a model and fact cluster, sends clipped fact bodies as JSON, gets a text completion, trims it through _to_overview_budget, and returns the final summary string.

**Call relations**: MemoryConsolidator._consolidate calls it before writing the semantic summary row.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_to_overview_budget`  (lines 707–719)

```
def _to_overview_budget(summary: str) -> str
```

**Purpose**: Cuts a model-written paragraph down to the allowed size while keeping whole sentences when possible. This keeps summaries readable and predictable.

**Data flow**: It receives a summary string, keeps sentences until the word, sentence, or character budget would be exceeded, and returns the kept text. If even the first sentence is too long, it clips by word.

**Call relations**: MemoryConsolidator._summarize, SectionWriter._summarize, and OverviewWriter._write all use it to enforce the shared paragraph budget.

*Call graph*: called by 3 (_summarize, _write, _summarize); 1 external calls (clip_to_word).


##### `_recency`  (lines 722–723)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: Provides a stable sort key for facts by creation time and id. It makes newest-first ordering deterministic.

**Data flow**: It receives an aged fact and returns a pair containing its creation time and id.

**Call relations**: MemoryConsolidator’s bucketing and clustering logic uses it when sorting facts by recency.


##### `_Group.key`  (lines 743–744)

```
def key(self) -> tuple[str, str]
```

**Purpose**: Returns the identity used to walk deduplication groups in order. A group is identified by subject and item class.

**Data flow**: It reads the group’s subject and item_class fields and returns them as a tuple.

**Call relations**: MemoryDeduper.run uses this key to advance its cursor through duplicate groups.


##### `_Group.fingerprint`  (lines 747–748)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: Returns a compact marker showing whether a deduplication group has changed since the last sweep. It is based on the live copy count and latest update time.

**Data flow**: It reads the group’s copies and latest timestamp and returns them as JSON-friendly values.

**Call relations**: MemoryDeduper.run compares this fingerprint with one stored in the scoped key-value store. If it matches, the expensive embedding pass is skipped.


##### `MemoryDeduper.run`  (lines 792–804)

```
async def run(self) -> None
```

**Purpose**: Runs one step of the periodic deduplication sweep. It chooses one eligible group, skips it if unchanged, or collapses near-duplicate rows inside it.

**Data flow**: It reads candidate groups, restores the last cursor, chooses the next group, stores the new cursor, checks the group fingerprint, and if needed runs _dedup_group and records the completed fingerprint.

**Call relations**: This is the deduper’s top-level flow. It calls _groups, _cursor, and _dedup_group, while using the scoped store to remember progress between ticks.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 806–818)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: Reads and validates the stored cursor for the deduplication walk. It treats malformed cursor data as an error instead of silently restarting.

**Data flow**: It receives a stored JSON value, returns an empty tuple if absent, returns a subject/item_class tuple if valid, or raises if the value has the wrong shape.

**Call relations**: MemoryDeduper.run calls it before choosing the next group to sweep.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 820–846)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: Finds groups of live tool-written rows that have enough copies to be worth checking for duplicates. Section paragraphs are excluded because they are different bands, not duplicate statements.

**Data flow**: It queries memory_item for unsuperseded, unretired, old-enough tool-written rows grouped by subject and item class, counts them, records the latest update time, and returns _Group objects.

**Call relations**: MemoryDeduper.run calls it to decide what work exists and to get each group’s fingerprint.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 848–853)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: Deduplicates one selected group of live rows. It embeds the rows, clusters near-duplicates, and collapses each duplicate cluster onto its newest row.

**Data flow**: It receives a group, reads its live copies, creates embeddings for their bodies, clusters them in a worker thread, and calls _collapse for clusters with enough members.

**Call relations**: MemoryDeduper.run calls it when a group’s fingerprint says it has changed. It coordinates _live_copies, _embed, _clusters, and _collapse.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 855–870)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: Reads the actual live rows in a duplicate group, newest first. The first row in any duplicate cluster will be the one kept.

**Data flow**: It receives a group, queries rows matching _live_group, orders by creation time and id descending, limits the result, and returns _LiveCopy records.

**Call relations**: MemoryDeduper._dedup_group calls it before embedding and clustering the group.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 872–879)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Creates embeddings for the rows in a deduplication group. It batches requests so a very large group does not become one oversized embedding call.

**Data flow**: It receives live copies, splits them into batches, sends clipped bodies to the embedding client, and returns a mapping from row id to vector.

**Call relations**: MemoryDeduper._dedup_group calls it before running similarity clustering.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 881–910)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: Groups near-duplicate rows by embedding similarity. It is tuned for restatements, not merely related facts.

**Data flow**: It receives copies and embeddings, walks the copies newest-first, compares each to existing cluster heads with cosine, and returns duplicate clusters.

**Call relations**: MemoryDeduper._dedup_group runs it in a worker thread. Clusters with enough copies are then passed to _collapse.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 912–940)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: Marks every duplicate row in a cluster as superseded by the newest row. It locks and verifies the rows first so it does not point rows at a head that changed meanwhile.

**Data flow**: It receives a group and duplicate cluster, treats the first copy as the head, locks all still-live matching rows, compares their bodies to the expected snapshot, and updates donor rows to superseded_by the head id.

**Call relations**: MemoryDeduper._dedup_group calls it after clustering. It uses _live_group to make the read and update use the same eligibility rules.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 942–951)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: Builds the shared database conditions for rows that count as live members of a deduplication group. This keeps reads and updates aligned.

**Data flow**: It receives a group and returns SQL conditions for workspace, subject, item class, tool-written origin, not superseded, not retired, and old enough.

**Call relations**: MemoryDeduper._live_copies and MemoryDeduper._collapse both use it so they operate on the same definition of a live duplicate candidate.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


##### `_rewrite_in_place`  (lines 972–1022)

```
async def _rewrite_in_place(connection: AsyncConnection, workspace_id: UUID, standing: _Standing, paragraph: str, confidence: int) -> None
```

**Purpose**: Writes one new standing paragraph and supersedes the previous live paragraph at the same place. It enforces the rule that a section or overview has only one current paragraph.

**Data flow**: It receives a database connection, workspace id, standing location, paragraph text, and confidence. It locks existing live paragraphs for that location, inserts the new paragraph, and updates the old ones to point to it.

**Call relations**: SectionWriter.run and OverviewWriter.run call it after the model has produced new prose. It centralizes the replacement logic so both writers behave the same way.

*Call graph*: called by 2 (run, run); 5 external calls (execute, insert, select, update, uuid4).


##### `_retire_standing`  (lines 1025–1053)

```
async def _retire_standing(connection: AsyncConnection, workspace_id: UUID, standing: _Standing) -> None
```

**Purpose**: Retires a standing paragraph when its page or section no longer has enough facts to deserve one. This prevents old prose from lingering above rows that no longer support it.

**Data flow**: It receives a connection, workspace id, and standing location, then updates the live paragraph there with retired_at and clears embedding fields so it leaves search indexes too.

**Call relations**: SectionWriter.run calls it for sections that fell below the floor. OverviewWriter.run calls it when the shared page lacks enough facts for an overview.

*Call graph*: called by 2 (run, run); 2 external calls (execute, update).


##### `SectionWriter.run`  (lines 1079–1102)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic job that rewrites section-opening paragraphs for every subject and memory kind with enough live facts. It also retires paragraphs for sections that no longer qualify.

**Data flow**: It checks for a model, reads sections that deserve paragraphs, reads paragraphs currently standing, retires stale ones, then for each eligible section reads facts, asks the model for a summary, and rewrites the paragraph in place.

**Call relations**: This is the top-level flow for section prose. It calls _sections, _standing, _facts, _summarize, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 6 internal fn (_facts, _sections, _standing, _summarize, _retire_standing, _rewrite_in_place).


##### `SectionWriter._sections`  (lines 1104–1137)

```
async def _sections(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds the subject/kind bands that currently have enough live facts to deserve a section paragraph. It also rejects memory kinds that have no heading on that subject’s page.

**Data flow**: It queries live servable fact rows, groups them by subject and memory kind, filters to groups above the minimum count, checks that each kind has a heading, and returns _Standing locations.

**Call relations**: SectionWriter.run calls it to know what should be written. It uses member_servable and section_headings so paragraph eligibility matches what readers see.

*Call graph*: calls 2 internal fn (member_servable, section_headings); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._standing`  (lines 1139–1157)

```
async def _standing(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds section paragraphs that are currently live, whether or not their underlying facts still qualify. This lets the job retire paragraphs that should no longer stand.

**Data flow**: It queries live unsuperseded SECTION rows for the workspace, groups by subject and memory kind, and returns their _Standing locations.

**Call relations**: SectionWriter.run compares this result to _sections. Anything standing but no longer eligible is sent to _retire_standing.

*Call graph*: called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._facts`  (lines 1159–1180)

```
async def _facts(self, section: _Standing) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the live facts that will be summarized for one section band. It uses newest facts first and applies a size limit.

**Data flow**: It receives a section location, queries servable live FACT rows matching that subject and memory kind, orders newest first, and returns body/confidence pairs.

**Call relations**: SectionWriter.run calls it for each eligible section before asking _summarize to write the paragraph.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._summarize`  (lines 1182–1201)

```
async def _summarize(self, model: ModelAccess, section: _Standing, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write one short paragraph for a specific section. The section heading is included so the model writes prose suited to where it will appear.

**Data flow**: It receives a model, section location, and facts, builds a JSON payload with the heading and clipped fact bodies, sends a completion request, and trims the result to the paragraph budget.

**Call relations**: SectionWriter.run calls it before opening the write transaction. It uses section_headings and _to_overview_budget.

*Call graph*: calls 3 internal fn (complete, _to_overview_budget, section_headings); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `OverviewWriter.run`  (lines 1225–1246)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic job that writes the workspace-wide opening overview paragraph. If there are too few shared facts, it retires the overview instead.

**Data flow**: It checks for a model, reads shared facts, retires the standing overview if the fact count is below the minimum, otherwise reads the workspace domain, asks the model for an overview, and rewrites the overview in place.

**Call relations**: This is the top-level flow for the workspace overview. It calls _facts, _write, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 4 internal fn (_facts, _write, _retire_standing, _rewrite_in_place); 2 external calls (__init__, workspace_domain).


##### `OverviewWriter._facts`  (lines 1248–1269)

```
async def _facts(self) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the live shared workspace facts used to write the overview. It pulls across all memory kinds because the overview sits above the whole page.

**Data flow**: It queries servable live shared FACT rows in the workspace, orders newest first, limits the number, and returns body/confidence pairs.

**Call relations**: OverviewWriter.run calls it before deciding whether to retire or rewrite the overview.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `OverviewWriter._write`  (lines 1271–1291)

```
async def _write(self, model: ModelAccess, domain: str | None, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write the workspace overview paragraph. The workspace domain is included when available so the model can name the company context.

**Data flow**: It receives a model, optional domain, and facts, sends a bounded JSON payload to the model, trims the returned text with _to_overview_budget, and returns the paragraph.

**Call relations**: OverviewWriter.run calls it after reading enough facts and before replacing the standing overview.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `WrittenProfile.within_role_budget`  (lines 1328–1329)

```
def within_role_budget(cls, role: str) -> str
```

**Purpose**: Keeps a model-written person role short enough to be a phrase, not a paragraph. It trims cleanly at a word boundary.

**Data flow**: It receives the role text, clips it to the profile role character budget, and returns the clipped role.

**Call relations**: This validator runs when ProfileWriter._write validates model output as WrittenProfile.

*Call graph*: 1 external calls (clip_to_word).


##### `WrittenProfile.within_row_budget`  (lines 1333–1334)

```
def within_row_budget(cls, focus: str) -> str
```

**Purpose**: Keeps a model-written focus sentence within the normal memory body size. It prevents oversized profile text from entering the profile table.

**Data flow**: It receives the focus text, clips it to the memory body budget at a word boundary, and returns the safe text.

**Call relations**: This validator runs during WrittenProfile validation inside ProfileWriter._write.

*Call graph*: 1 external calls (clip_to_word).


##### `ProfileWriter.run`  (lines 1372–1387)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic People pass that writes each roster member’s role and current focus. If no model or no roster is available, it does nothing.

**Data flow**: It reads the roster, reads shared facts, asks the model to write profiles, matches returned names to roster members, and stores only matched entries.

**Call relations**: This is the top-level profile flow. It calls _roster, _facts, _write, and _store.

*Call graph*: calls 4 internal fn (_facts, _roster, _store, _write).


##### `ProfileWriter._roster`  (lines 1389–1408)

```
async def _roster(self) -> tuple[_Rostered, ...]
```

**Purpose**: Reads the workspace roster in the same way the seating system understands it. It records each member’s email and standing, such as admin/member and seated/unseated.

**Data flow**: It opens a transaction, asks Seats for a snapshot, takes up to the roster limit, and returns _Rostered records.

**Call relations**: ProfileWriter.run calls it before asking the model to write people entries. The returned names are later used to match model output back to member ids.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `ProfileWriter._facts`  (lines 1410–1427)

```
async def _facts(self) -> tuple[str, ...]
```

**Purpose**: Reads shared workspace facts that everyone on the roster could already see. This avoids using a person’s private page to write a public profile about them.

**Data flow**: It queries servable live shared FACT rows, orders newest first, limits the result, and returns only their bodies.

**Call relations**: ProfileWriter.run calls it before ProfileWriter._write builds the model prompt.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 1 external calls (select).


##### `ProfileWriter._write`  (lines 1429–1477)

```
async def _write(self, model: ModelAccess, roster: tuple[_Rostered, ...], facts: tuple[str, ...]) -> tuple[WrittenProfile, ...]
```

**Purpose**: Asks the model to produce People entries as a forced tool call. Each returned entry is validated on its own so one bad profile does not discard the rest.

**Data flow**: It receives the roster and facts, builds a JSON payload, sends a tool-based model request, extracts the people list from the returned tool input, validates each entry as WrittenProfile, and returns the valid profiles.

**Call relations**: ProfileWriter.run calls it after reading roster and facts. Its output is matched to roster member ids before _store writes anything.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 4 external calls (__init__, __init__, __init__, dumps).


##### `ProfileWriter._store`  (lines 1479–1508)

```
async def _store(self, entries: tuple[tuple[UUID, WrittenProfile], ...]) -> None
```

**Purpose**: Writes profile entries into the memory_profile table, replacing the previous entry for each member. It uses an upsert, meaning insert if missing or update if already present.

**Data flow**: It receives member/profile pairs, creates one timestamp, and for each pair inserts or updates role, focus, and written_at under the workspace/member key.

**Call relations**: ProfileWriter.run calls it only for model entries that matched known roster members.

*Call graph*: called by 1 (run); 3 external calls (now, insert, insert).


##### `admitted_curation`  (lines 1562–1597)

```
def admitted_curation(bands: tuple[tuple[int, ...], ...], retire: tuple[RetiredRow, ...]) -> AdmittedCuration
```

**Purpose**: Decides which page-curation retirements are safe to apply. It rejects retirements that do not name a surviving duplicate and refuses the whole page if too much of one band would be removed.

**Data flow**: It receives the row indexes sent for each band and the model’s proposed retirements, drops ids not on the page, checks each duplicate_of target survives, enforces the per-band retirement limit, and returns admitted indexes or a refusal reason.

**Call relations**: PagePass._retiring calls it before converting model row indexes into real database row ids.

*Call graph*: called by 1 (_retiring); 1 external calls (__init__).


##### `PagePass.run`  (lines 1645–1658)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic whole-page curation pass. For each subject with enough rows, it asks the model which page-derived rows are redundant and retires the admitted ones.

**Data flow**: It checks for a model, reads eligible subjects, builds each subject page, asks _curate for model judgments, filters them through _retiring, and applies retirements in one transaction per subject.

**Call relations**: This is the top-level page curation flow. It coordinates _subjects, _page, _curate, _retiring, and _apply.

*Call graph*: calls 5 internal fn (_apply, _curate, _page, _retiring, _subjects).


##### `PagePass._subjects`  (lines 1660–1676)

```
async def _subjects(self) -> tuple[str, ...]
```

**Purpose**: Finds subjects with enough live facts to be worth whole-page curation. Thin pages are skipped because a person can already read them at a glance.

**Data flow**: It queries live unsuperseded, unretired FACT rows grouped by subject, keeps groups above the minimum row count, orders them, and returns subject strings.

**Call relations**: PagePass.run calls it to decide which pages to inspect.

*Call graph*: called by 1 (run); 1 external calls (select).


##### `PagePass._page`  (lines 1678–1724)

```
async def _page(self, subject: str) -> tuple[_Band, ...]
```

**Purpose**: Builds the model-readable version of one subject’s page. It includes each section heading, the standing section summary, and current page-derived fact rows.

**Data flow**: It receives a subject, loops through that subject’s section headings, queries live page-linked fact rows for each memory kind, assigns compact numeric indexes, reads the current section paragraph, and returns bands.

**Call relations**: PagePass.run calls it before _curate. It uses live_page_link so it only curates rows still backed by the current source page.

*Call graph*: calls 2 internal fn (live_page_link, section_headings); called by 1 (run); 3 external calls (__init__, __init__, select).


##### `PagePass._curate`  (lines 1726–1775)

```
async def _curate(self, model: ModelAccess, bands: tuple[_Band, ...]) -> CuratedPage
```

**Purpose**: Asks the model to identify rows that make the whole page read worse because they repeat other rows. The model returns row indexes, not database ids, to keep the prompt compact.

**Data flow**: It receives page bands, builds a bounded JSON payload of section summaries and clipped rows, sends a forced tool request, validates each proposed retirement as RetiredRow, and returns a CuratedPage.

**Call relations**: PagePass.run calls it after building a page. Its proposed retirements are not trusted directly; PagePass._retiring screens them first.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, dumps).


##### `PagePass._retiring`  (lines 1777–1788)

```
def _retiring(self, subject: str, bands: tuple[_Band, ...], retire: tuple[RetiredRow, ...]) -> frozenset[UUID]
```

**Purpose**: Converts model-proposed row indexes into real memory row ids, but only after safety checks. If the curation is too aggressive, it logs a warning and retires nothing for that subject.

**Data flow**: It receives a subject, page bands, and proposed retirements, asks admitted_curation what is allowed, logs any refusal, and returns the database ids corresponding to admitted row indexes.

**Call relations**: PagePass.run calls it between _curate and _apply. It is the safety gate before destructive retirement.

*Call graph*: calls 1 internal fn (admitted_curation); called by 1 (run); 1 external calls (warn).


##### `PagePass._apply`  (lines 1790–1806)

```
async def _apply(self, retiring: frozenset[UUID]) -> None
```

**Purpose**: Marks admitted page rows as retired and clears their embedding fields so search stops serving them. It does not delete the rows.

**Data flow**: It receives memory row ids, opens a transaction, and updates matching live rows in the workspace with retired_at, cleared embedding fields, and updated_at.

**Call relations**: PagePass.run calls it after _retiring returns non-empty ids. This is the only write step in the page pass.

*Call graph*: called by 1 (run); 1 external calls (update).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file solves a practical problem: the system needs to remember useful statements, but it must not show stale, duplicate, private, or no-longer-valid memories. It stores each memory in a database table, links page-derived memories back to the source pages they came from, and uses a separate index so memories can be found by both exact words and meaning.

A write is deliberately simple. `MemoryStore.commit` saves one memory row and marks it as needing indexing. It does not split text into chunks or create embeddings, which are numeric representations of meaning. That work is done later by `MemoryIndexer`, like a mailroom that sorts letters after they are dropped in the box.

Recall combines several signals. It searches the index by words, searches by embedding similarity, also checks very new unindexed rows, blends those results, then reads the real database rows back through permission and freshness checks. It applies time decay for facts, removes near-duplicates, and prevents one kind of memory from crowding out all others.

The file also mirrors source pages into the index through `PageIndexer`, and removes or rechecks indexed chunks when pages change. This matters because the index can otherwise keep returning facts from old page revisions even after the source has moved on.

#### Function details

##### `recall_subjects`  (lines 176–177)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience description into the set of memory subjects that audience is allowed to recall. A subject is the visibility label used to decide which memories belong in a reader’s view.

**Data flow**: It receives an `Audience` object → asks the shared audience helper to expand it into subjects → returns those subjects as a frozen set.

**Call relations**: Recall setup uses this as the small bridge from audience rules into the memory store’s subject filtering. It hands off the real interpretation to the shared audience code.

*Call graph*: 1 external calls (audience_subjects).


##### `clip_to_word`  (lines 180–189)

```
def clip_to_word(text: str, limit: int) -> str
```

**Purpose**: Shortens text to a character limit without cutting through the middle of a word. It adds an ellipsis inside the limit so the reader can tell the text was shortened.

**Data flow**: It receives text and a maximum length → if the text already fits, it returns it unchanged → otherwise it keeps as much as possible, backs up to the last space when it can, trims dangling punctuation, and returns the shortened text with `…`.

**Call relations**: Callers use this before creating a `MemoryWrite` if they want to trim over-long model output. The write model itself rejects bodies that exceed the storage limit rather than silently cutting them.


##### `_granted_link`  (lines 192–200)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds the database permission check for page-derived memories. A memory learned from a source page is readable if the reader has access to at least one source link that produced it.

**Data flow**: It receives a set of readable source IDs → creates a database `EXISTS` condition tied to the current memory row → the condition is later used inside larger reads to keep unauthorized rows out.

**Call relations**: `MemoryStore._untail_leg` uses it when searching newly written but unindexed memories, and `MemoryStore._enrich` uses it when reading indexed candidates back. It is the common fence that stops source-derived memories from leaking across source permissions.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 240–309)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, operator-facing list of stored memories in a workspace. It is for inspection, not for recall, so it includes details like indexing status, source links, age, and decay weight.

**Data flow**: It receives a transaction opener and workspace ID → reads the newest memory rows and their source links from the database → computes age, half-life, and decay using one shared current time → returns `MemoryInventoryItem` objects.

**Call relations**: This is separate from user recall. It calls the date and decay helpers so the operator view reports the same freshness math that recall later uses for ranking.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 312–313)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has a timezone. If a stored time is missing timezone information, it treats it as UTC.

**Data flow**: It receives a datetime → checks whether it already has timezone information → returns it unchanged or returns a UTC-marked version.

**Call relations**: Inventory, recall enrichment, source search, and decay calculation call this before doing time math. This avoids subtle bugs where timezone-naive and timezone-aware dates cannot be safely compared.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.body_is_within_budget`  (lines 340–346)

```
def body_is_within_budget(self) -> Self
```

**Purpose**: Validates that a memory body is not too long to store as a single memory. Long documents belong as pages, not one oversized memory row.

**Data flow**: It reads the `body` length on the `MemoryWrite` being validated → if it exceeds the configured limit, validation fails with an error → otherwise the write object is accepted unchanged.

**Call relations**: This runs automatically when a caller constructs a `MemoryWrite`. It protects `MemoryStore.commit` from receiving bodies that break the memory store’s size promise.


##### `MemoryWrite.page_origin_is_complete`  (lines 349–357)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Validates that page-derived memories name their origin completely. If a memory says it came from a page, it must include page ID, page revision, and source ID together.

**Data flow**: It checks the three origin fields on the `MemoryWrite` → if some are present but not all, validation fails → if none or all are present, the write object is accepted.

**Call relations**: This runs before `MemoryStore.commit`. It ensures later permission checks, page freshness checks, and source-link records have enough information to work correctly.


##### `_fuse`  (lines 387–410)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines several ranked search-result lists into one best score per owning row. This lets the system blend word matches and meaning matches without letting duplicate chunks from the same memory dominate.

**Data flow**: It receives one or more result lists plus the vector-result list → calculates reciprocal-rank fusion, meaning high-ranked hits in each list contribute more → keeps the best chunk per owner and the owner’s best cosine similarity → returns a mapping from owner ID to fused score, cosine score, and snippet text.

**Call relations**: `fuse_hits` and `fuse_recall` both use this shared core. It does the mechanical merge so those public helpers can apply their different ranking rules for source-page search versus memory recall.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 413–426)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search hits by combining word-search and meaning-search results. It also avoids returning random nearest-neighbor results when no words matched at all.

**Data flow**: It receives lexical hits, vector hits, and a limit → fuses them with `_fuse` → if there were no lexical matches, drops weak vector-only matches below a similarity floor → sorts by fused rank and returns `Fused` results up to the limit.

**Call relations**: `MemoryStore.search_sources` calls this after asking the index for page hits. It produces the ordered page candidates that are then checked against the live page mirror and reader permissions.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 429–457)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending rank-based search with raw semantic closeness. It also includes a special tail of very new memories that have not been indexed yet.

**Data flow**: It receives lexical hits, vector hits, unindexed-tail hits, and a limit → fuses all legs with `_fuse` → normalizes the rank score, blends it with cosine similarity, applies a floor only when the query had no word matches, then returns top `Fused` results.

**Call relations**: `MemoryStore.recall` calls this after gathering all recall legs. Its output is not served directly; `_enrich` must still read real database rows and apply permissions, freshness, and lifecycle filters.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 475–481)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Returns how quickly a fact should fade in recall ranking. Only fact-class memories decay over time; other memory classes keep their relevance score unchanged.

**Data flow**: It receives an item class and memory kind → if the item is not a fact, returns `None` → otherwise returns the configured half-life for that kind, falling back to the fact default.

**Call relations**: `decay_multiplier` uses this for recall scoring, and `inventory` uses it to show operators the same decay setting. It centralizes the half-life rule so the display and ranking agree.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 484–496)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates the freshness multiplier for a memory’s recall score. For facts, older and lower-confidence memories count less; non-facts stay at full strength.

**Data flow**: It receives item class, kind, confidence, source date, and current time → finds the half-life → if no decay applies, returns `1.0` → otherwise computes age in days and returns the confidence-scaled decay factor.

**Call relations**: `decay_factor` wraps this for recalled items, and `inventory` calls it for operator display. It also uses `_aware` so stored dates are safe for time arithmetic.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 499–502)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Calculates the decay multiplier for one recalled memory item. It is a convenience wrapper around the shared decay formula.

**Data flow**: It receives a `Recalled` item and a current time → chooses the item’s `as_of` date when available, otherwise its creation time → passes those values into `decay_multiplier` → returns the multiplier.

**Call relations**: `MemoryStore._shortlist` calls this while reranking recall candidates. That keeps the main recall flow readable while still using the central decay logic.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 510–512)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns a memory body into small three-word fingerprints used for duplicate detection. These fingerprints make it cheap to compare whether two memories say nearly the same thing.

**Data flow**: It receives text → lowercases it, splits it into words, and groups neighboring words into three-word phrases → returns a frozen set of those phrases.

**Call relations**: `drop_near_duplicates` calls this for each candidate it considers. It is the low-level text preparation step for the recall duplicate guard.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 515–541)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes near-copy memories from the recall list so limited context slots are not wasted repeating the same fact. It keeps the highest-ranked version and skips later ones that overlap too much.

**Data flow**: It receives ranked recalled items and a maximum number to keep → walks them in order → computes word-shingle overlap against already kept items → appends only sufficiently different items → returns the kept tuple.

**Call relations**: `MemoryStore._shortlist` calls this after applying decay. It protects recall output until slower cleanup jobs can permanently merge or retire duplicate memories.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 544–562)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from filling the entire recall result. For example, many facts should not crowd out all episodic or semantic pointers when there is room for variety.

**Data flow**: It receives ranked recalled rows and a limit → admits only a capped number per item class on the first pass → stores overflow rows → backfills from overflow if there are still open slots → returns at most the requested limit.

**Call relations**: `MemoryStore._shortlist` calls this after duplicate removal. It is the final shaping step before recall results are returned to the caller.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 565–575)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Rewrites episodic memory hits into topic pointers instead of injecting their full body. Episodic memories are treated as breadcrumbs for browsing, not as verbatim context.

**Data flow**: It receives a recalled item and its index in the result list → if it is not episodic, returns it unchanged → if it is episodic, returns a copied item with a short pointer body and `recall_mode` set to `topic`.

**Call relations**: `MemoryStore.recall` applies this to the final shortlist. It changes presentation without changing the stored memory row.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 602–728)

```
async def commit(self, write: MemoryWrite) -> UUID
```

**Purpose**: Writes one memory into the durable store and returns its stable ID. It intentionally leaves indexing for the background indexer, so writes stay fast and predictable.

**Data flow**: It receives a validated `MemoryWrite` → creates a content-based UUID from workspace, subject, class, and body → inserts or updates the memory row → records or updates a source-page link when present → returns the memory ID.

**Call relations**: Callers that extract facts from pages use the returned ID to tell `supersede_page_facts` which facts the page still supports. Later, `MemoryIndexer` sees the row because its embedding digest is empty and turns it into searchable chunks.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 730–851)

```
async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None
```

**Purpose**: Removes a page’s support for facts it no longer produced. If no other page still supports a fact, the fact row and its index chunks are removed.

**Data flow**: It receives a page ID and either the set of kept memory IDs or `None` for a gone page → deletes stale source links → for each affected memory, either deletes it, repoints it to a surviving source link, or leaves it alone → deletes index chunks for fully deleted memories.

**Call relations**: The fact-derivation flow calls this after committing the facts a page currently supports. It coordinates with `commit`, which adds links, and with the index backend, which must stop serving rows that have no surviving source.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 853–892)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Finds the best memories for a query, subject list, time window, and reader permissions. It is the main read path used when the system wants remembered context.

**Data flow**: It receives the query, allowed subjects, limit, optional dates, and a source reader → obtains readable source IDs → asks the index for lexical and vector legs → scans the unindexed tail → fuses candidates → enriches them from the database with permissions and freshness checks → shortlists them in a worker thread → returns final recalled items.

**Call relations**: This orchestrates `_source_ids`, `_legs`, `_untail_leg`, `fuse_recall`, `_enrich`, `_shortlist`, and `as_topic_pointer`. It is the central story for memory retrieval.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 894–909)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Narrows an enriched candidate pool down to the final recall slots. It applies freshness decay, duplicate removal, and type diversity.

**Data flow**: It receives recalled candidates, a limit, and current time → multiplies each score by its decay factor → sorts by the new score → drops near-duplicates → enforces class diversity → returns the final shortlist.

**Call relations**: `MemoryStore.recall` runs this in a worker thread because it is CPU work with no awaits. It calls the decay, duplicate, and diversity helpers in the final ranking stage.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 911–977)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages, not stored memory facts. It returns matching snippets from page chunks while checking that the page is still current and readable.

**Data flow**: It receives query, subjects, limit, optional date bounds, and a source reader → gets lexical and vector page hits → fuses them → reads matching mirror rows from `mem_page` → verifies live readable page state → returns `SourceMatch` results.

**Call relations**: This shares `_legs` and the fusion style with recall, but it reads page mirror data and calls `_readable_states` instead of memory enrichment. It protects against stale page chunks by comparing the mirror to current page state.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 979–985)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Gets the set of source IDs the current reader may access. Source permissions are needed before serving page-derived memories.

**Data flow**: It receives a `SourceReader` → if no source-grant function is wired, raises an error → otherwise asks that function for readable source IDs and returns them.

**Call relations**: `MemoryStore.recall` calls this before any source-derived memory can be returned. It is the permission authority entry point for memory reads.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 987–999)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main index searches for a query: word-based search and vector-based meaning search. Vector search is skipped if the query cannot be embedded.

**Data flow**: It receives query text, subjects, owner kind, and limit → embeds the query → asks the index for lexical hits → asks for vector hits only when an embedding exists → returns both hit tuples.

**Call relations**: Both `MemoryStore.recall` and `MemoryStore.search_sources` use this. It delegates query embedding to `_embed_query` and leaves result blending to `fuse_recall` or `fuse_hits`.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 1001–1057)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Searches very new memory rows that have not yet been indexed. This makes freshly committed memories recallable before the background indexer runs.

**Data flow**: It receives query, subjects, limit, and readable source IDs → splits the query into terms → reads newest unindexed, live, permitted memory rows from the database → counts query-term matches in each body → returns synthetic `Hit` objects sorted by match count.

**Call relations**: `MemoryStore.recall` adds this as a third search leg before calling `fuse_recall`. It uses `_granted_link` so unindexed source-derived rows are still permission-checked.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 1059–1067)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a recall or source-search query into an embedding vector. If embedding fails, it logs a warning and lets the rest of search continue with word matching only.

**Data flow**: It receives query text → returns an empty tuple for blank text → otherwise calls the embedding backend → returns the first vector, or an empty tuple if none is produced or an error occurs.

**Call relations**: `MemoryStore._legs` calls this before vector search. Its failure-tolerant behavior keeps recall from breaking completely when the embedding service is unavailable.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 1069–1149)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused memory hit IDs into full recalled memory objects, while applying the real database fences. It removes candidates that are superseded, retired, unauthorized, outside the time window, or bound to an old page revision.

**Data flow**: It receives fused hits, allowed subjects, readable source IDs, and optional dates → reads matching live rows from the database under permission conditions → checks current page states for page-derived rows → returns `Recalled` objects in fused order.

**Call relations**: `MemoryStore.recall` calls this after search fusion. It is the safety gate between the broad index candidate window and memories actually shown to a reader.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1151–1158)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Fetches current page states only for pages the reader may access. This is used to verify source-page search results before showing them.

**Data flow**: It receives page IDs and a source reader → returns an empty dictionary if there are no pages → raises if no readable-page authority is wired → otherwise returns the readable current page states.

**Call relations**: `MemoryStore.search_sources` calls this after finding candidate page IDs. It provides the final permission and freshness check for source-page snippets.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1161–1174)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a `MemoryStore` from the extension context. It fails early if the required index or embedding backends are missing.

**Data flow**: It receives an `ExtensionContext` → checks that index and embed clients exist → copies the scoped transaction opener, workspace ID, page-state readers, and permission readers into a new `MemoryStore` → returns it.

**Call relations**: Other extension code uses this as the construction point for memory operations. It keeps `MemoryStore` wired to the correct workspace and backend services.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1198–1200)

```
async def run(self) -> None
```

**Purpose**: Processes one batch of memory rows that need indexing. It is the public tick for the background memory-index job.

**Data flow**: It claims due memory items → loops through them one by one → asks `_index_item` to publish, withdraw, or settle each item.

**Call relations**: The background scheduler calls this. It connects batch claiming with per-item indexing while leaving the details to `_claim_due` and `_index_item`.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1202–1238)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically claims memory rows whose embedding work is due. The claim prevents overlapping indexer runs from embedding the same row at the same time.

**Data flow**: It computes a lease cutoff time → selects rows with no digest and no active claim, limited to the batch size → locks them when the database supports skip-locked behavior → stamps their claim time → returns them as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this at the start of each tick. The claimed rows are then handed to `_index_item`, and `_settle` later clears the claim when a row reaches a terminal state.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1240–1277)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Decides what to do with one claimed memory: publish it to the index, withdraw its chunks, or leave it for a later run if its page binding changed.

**Data flow**: It receives a claimed `MemoryItem` → checks whether it is retired or not publishable → deletes chunks and settles if it should be withheld → otherwise creates chunks and embeddings if the index lacks them → rereads the row binding → verifies it is still publishable and unchanged → settles the row when safe.

**Call relations**: `MemoryIndexer.run` calls this for each claimed row. It uses `_publishable` to guard page-derived memories, `chunk_embed_upsert` to write index chunks, and `_settle` to mark successful decisions.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1279–1288)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Checks whether a memory is allowed to appear in the search index. Tool-written memories are always publishable; page-derived memories are publishable only if their source page still has the same subject and revision.

**Data flow**: It receives subject, page ID, and revision → returns true immediately for non-page memories → otherwise reads current page state → returns true only when the page exists and exactly matches subject and revision.

**Call relations**: `MemoryIndexer._index_item` calls this before and after index writes. This double-check stops stale page-derived memories from occupying recall candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1290–1314)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as no longer due for indexing. It writes a digest of the body and clears the claim, but only if the row still matches what this run claimed.

**Data flow**: It receives a `MemoryItem` → computes a SHA-256 digest of the body → updates the row’s `embedding_digest`, clears `embedding_claimed_at`, and updates the timestamp under strict matching conditions → returns nothing.

**Call relations**: `MemoryIndexer._index_item` calls this after publishing or intentionally withholding a row. The guarded update prevents an old indexing decision from settling a row that has since been rebound or changed.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1337–1339)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the memory extension’s page index. It is the public entry point for page-change indexing work.

**Data flow**: It receives a tuple of page changes → processes each change in order by calling `_apply` → returns when all changes have been attempted.

**Call relations**: The core page-change runner calls this with batches it owns. This method keeps batching simple and delegates all per-page rules to `_apply`.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1341–1399)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Updates the page search index and page mirror for one source-page change. It deletes stale page chunks, indexes current page bodies, and records the current page revision in `mem_page`.

**Data flow**: It receives one `PageChange` → reads current page state → marks facts from left-behind revisions as due for rechecking → if the change is a tombstone or stale, deletes page chunks and mirror row → otherwise chunks and embeds the page body → verifies the page did not change during embedding → upserts the `mem_page` mirror row.

**Call relations**: `PageIndexer.apply` calls this for each change. It uses `_unsettle_left_behind_facts` to wake the memory indexer for old facts, and `chunk_embed_upsert` to publish current page chunks.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1401–1429)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memories from old page revisions as needing index review again. This lets the memory indexer remove chunks for facts whose source page has moved on.

**Data flow**: It receives a page ID and the page’s current state, if any → builds a condition for memories from that page that no longer match the live subject and revision, or all memories if the page is gone → clears their embedding digest and claim fields → returns nothing.

**Call relations**: `PageIndexer._apply` calls this before handling each page change. It does not retire memory rows itself; instead, it asks `MemoryIndexer` to revisit whether their chunks should remain searchable.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### `extensions/memory/ufo_ext_memory/events.py`

`data_model` · `cross-cutting`

The memory extension likely reports what it is doing by emitting structured events: small, named records that other parts of the system can log, display, or inspect. This file is the label maker for one of those events. It defines the event name used when the extension recalls memories before producing a response, plus two guardrails that keep event data from becoming too large or messy.

The constant `MEMORY_RECALL_EVENT` is the exact event name: `memory.pre_response_recall`. Using a shared constant matters because event names must match exactly. If different files typed this string by hand, a small spelling difference could make listeners miss the event.

`MAX_RECALLED_MEMORY_IDS` limits how many recalled memory identifiers should be included in the event. This is like showing only the first few receipt numbers instead of printing an entire archive. `MAX_RECALL_ERROR_CLASS_CHARS` limits how much of an error class name is recorded, so unusual or very long error text does not bloat the event.

There are no functions here. The file exists as a tiny shared contract: everyone agrees on the event name and the maximum amount of supporting detail to include.


### Consent-gated enrichment
Handles enrichment consent, provider selection or replay, normalized profile records, and database state for lookup retries and results.

### `extensions/enrichment/ufo_ext_enrichment/manifest.py`

`orchestration` · `startup, user action, scheduled job, object reads, prompt submission`

This file solves a privacy-sensitive onboarding problem: the system wants a useful first guess about who a workspace member works for, but it must not send anyone’s information to an outside data provider unless that member agreed. The agreement happens through the confirm_website action. That action only records the member’s answer and clears any old profile; it does not immediately call the provider. A scheduled job later finds consenting members who still need profiles and looks them up in small batches.

The file also protects against bad guesses. Common personal email domains like gmail.com and yahoo.com are treated as mail providers, not companies, so they are not used as company websites. If the provider fails or asks the system to slow down, the job pauses the workspace instead of retrying every minute.

Once profiles exist, the read-only enrichment_profile object lets the portal or tools list and inspect them. Attempts to write or delete those rows are refused because the only write path is confirming or clearing a website. Finally, on each user prompt, the inject hook may add a short, clearly walled-off summary of the company and speaker. “Walled” means the text is marked as third-party, unverified data, so the assistant should treat the member’s own words as more trustworthy.

#### Function details

##### `website_host`  (lines 162–172)

```
def website_host(raw: str) -> str | None
```

**Purpose**: Turns a member-entered website into a clean host name, such as turning “https://www.example.com/path” into “example.com”. It returns nothing for an empty answer and rejects text that does not look like a real website.

**Data flow**: It receives raw text from the website field → trims spaces, lowercases it, parses it like a URL, removes a leading “www.”, and checks that the host contains a dot → returns the cleaned host, returns null for an empty field, or raises an error for invalid website text.

**Call relations**: Enrichment.confirm_website calls this first when a member submits the confirm website action, so the rest of the flow stores a normalized domain rather than whatever text the member typed.

*Call graph*: called by 1 (confirm_website); 1 external calls (urlsplit).


##### `Enrichment.confirm_website`  (lines 182–203)

```
async def confirm_website(self, ctx: ToolContext, args: ConfirmWebsiteInput) -> ToolResult
```

**Purpose**: Records whether the speaking member agreed to be looked up and what website they confirmed. It deliberately does not perform the lookup itself; it leaves that work for the scheduled job.

**Data flow**: It receives the tool context and the website answer → requires an extension context and a speaking seated member, cleans the website, checks the member exists, records consent and the website unless it is a known free mail domain, deletes any older stored profile for that member → returns a short message saying either that the profile will be built soon or that nothing was looked up.

**Call relations**: This is the action registered by manifest when a provider exists. It uses website_host to clean the input, uses Profiles and Consents storage to record the decision, and relies on Enrichment.tick to later build the profile.

*Call graph*: calls 2 internal fn (_require_ext, website_host); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `Enrichment.tick`  (lines 205–224)

```
async def tick(self, ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled enrichment work for a workspace. It finds consenting members who do not yet have a profile and looks them up one by one.

**Data flow**: It receives an extension context for a workspace → reads a small batch of due members, looks up each member, writes each resulting profile, and clears any previous pause when work succeeds → if the provider reports an enrichment error, it records a backoff pause, logs a warning, and stops for now.

**Call relations**: The job declared by manifest calls this on a schedule. It delegates the actual provider calls to Enrichment._lookup and uses Backoff so a failing provider does not get hammered every minute.

*Call graph*: calls 2 internal fn (transaction, _lookup); 3 external calls (__init__, __init__, warn).


##### `Enrichment._lookup`  (lines 226–233)

```
async def _lookup(self, email: str, website: str | None) -> Profile
```

**Purpose**: Asks the provider for information about a person and, when appropriate, their company. It chooses the company lookup domain from the confirmed website or the email domain, while avoiding common personal email providers.

**Data flow**: It receives an email address and optional confirmed website → asks the provider for the person by email, chooses a company domain, skips company lookup if that domain is a free mail service, otherwise asks the provider for the company → passes the pieces into Enrichment._profile and returns the completed profile object.

**Call relations**: Enrichment.tick calls this for each due member. After it has gathered provider data, it hands the raw person and company results to Enrichment._profile to package them consistently.

*Call graph*: calls 1 internal fn (_profile); called by 1 (tick).


##### `Enrichment._profile`  (lines 235–249)

```
def _profile(self, email: str, website: str | None, person: Person | None, company: Company | None) -> Profile
```

**Purpose**: Builds the stored profile record from the lookup results. It marks whether anything matched and stamps the result with the current time and provider source.

**Data flow**: It receives the member email, confirmed website, optional person data, and optional company data → chooses status “matched” if either person or company was found, otherwise “no_match” → returns a Profile containing the input data, source name, and fetch time.

**Call relations**: Enrichment._lookup calls this after provider queries finish. The returned Profile is then written by Enrichment.tick and later read by object views and prompt injection.

*Call graph*: called by 1 (_lookup); 2 external calls (__init__, now).


##### `inject`  (lines 252–274)

```
async def inject(ctx: HookContext) -> HookOutcome
```

**Purpose**: Adds a short unverified enrichment summary to a user prompt when useful profile data exists. This gives the assistant starting context, while clearly marking it as third-party data.

**Data flow**: It receives a hook context for a prompt submission → if there is no turn, it returns nothing; otherwise it reads the speaker’s profile and recent workspace rows, chooses company information and speaker person information, formats short lines, wraps them in a safety wall with the source label → returns an InjectContext or nothing if there is no useful data.

**Call relations**: manifest registers this as a best-effort user_prompt_submit hook, so it runs around prompt submission time. It calls _company_lines and _member_lines to format the readable text and wall to mark the text as untrusted outside information.

*Call graph*: calls 2 internal fn (_company_lines, _member_lines); 3 external calls (__init__, __init__, wall).


##### `_company_lines`  (lines 277–285)

```
def _company_lines(company: Company | None) -> list[str]
```

**Purpose**: Creates a compact human-readable company line for prompt injection. It includes the company name and, when known, details like industry, size, or location.

**Data flow**: It receives optional company data → if there is no useful company name, it returns an empty list; otherwise it clips long parts and joins the details → returns a one-item list such as “company: Example — Software, 50-100, London”.

**Call relations**: inject calls this while building the short context block for the assistant. It uses _clip so long provider values do not flood the prompt.

*Call graph*: calls 1 internal fn (_clip); called by 1 (inject).


##### `_member_lines`  (lines 288–292)

```
def _member_lines(person: Person | None) -> list[str]
```

**Purpose**: Creates a compact human-readable member line for prompt injection. It focuses on the speaker’s name and job title when either is known.

**Data flow**: It receives optional person data → if there is no name or title, it returns an empty list; otherwise it clips the known parts and joins them → returns a one-item list such as “member: Ada Lovelace — Founder”.

**Call relations**: inject calls this beside _company_lines so the assistant can see both organization context and speaker context. It also uses _clip to keep the injected text short.

*Call graph*: calls 1 internal fn (_clip); called by 1 (inject).


##### `_clip`  (lines 295–297)

```
def _clip(value: str, limit: int) -> str
```

**Purpose**: Shortens a piece of text to a safe display length and makes it one line. This prevents long or messy provider values from taking over summaries or prompt injections.

**Data flow**: It receives a string and a character limit → collapses repeated whitespace into normal spaces, checks the length, and if needed cuts the text and adds an ellipsis → returns the cleaned, possibly shortened string.

**Call relations**: _company_lines, _member_lines, and summary all call this before showing provider-derived text to users or prompts.

*Call graph*: called by 3 (_company_lines, _member_lines, summary).


##### `summary`  (lines 300–312)

```
def summary(profile: Profile) -> str
```

**Purpose**: Builds the one-line summary shown for a profile row. It tries to say the most useful known thing, such as a title at a company, and falls back to “No match” when nothing useful was found.

**Data flow**: It receives a Profile → looks for the person’s job title, the company name, the person’s full name, and the company industry → combines the best available pieces into a readable line and clips it to the summary limit → returns that summary string.

**Call relations**: _row calls this whenever a stored profile is turned into an object row for listing or detail display.

*Call graph*: calls 1 internal fn (_clip); called by 1 (_row).


##### `_row`  (lines 315–338)

```
def _row(profile: Profile) -> ObjectRow
```

**Purpose**: Converts a stored Profile into the object-row shape used by the portal and object API. It lays out the profile’s fields in a flat, listable form.

**Data flow**: It receives a Profile → extracts person fields, company fields, source, status, email, and website into a dictionary, builds a row name from the email, and adds the summary from summary → returns an ObjectRow.

**Call relations**: ProfileObjects._page calls this for lists, and ProfileObjects._entry calls it for a single detail view. It is the bridge from enrichment storage data to the generic object display system.

*Call graph*: calls 1 internal fn (summary); called by 2 (_entry, _page); 1 external calls (__init__).


##### `_require_ext`  (lines 341–344)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that an extension context is present before code tries to read or write workspace data. Without that context, the code would not know which workspace or transaction to use.

**Data flow**: It receives an optional extension context → if it is present, returns it unchanged; if it is missing, raises a runtime error explaining the dispatch problem.

**Call relations**: The action and object methods call this at their boundaries. It acts like a guard at the door before Enrichment.confirm_website, ProfileObjects.list, get, member_page, and member_detail access storage.

*Call graph*: called by 5 (confirm_website, get, list, member_detail, member_page).


##### `ProfileObjects.list`  (lines 353–356)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists enrichment profile rows for callers allowed to read shared workspace data. If the caller does not have that shared read permission, it returns an empty page.

**Data flow**: It receives a tool context and list query → checks whether the shared subject is readable, requires the extension context, and either returns an empty object page or asks _page to build the real page → returns an ObjectPage.

**Call relations**: The PROFILE_OBJECT store uses this for normal collection listing. When access is allowed, it delegates the actual storage read and row conversion to ProfileObjects._page.

*Call graph*: calls 2 internal fn (_page, _require_ext); 1 external calls (object_page).


##### `ProfileObjects.member_page`  (lines 358–374)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists enrichment profiles through the member-object path, but only from the main agent. This prevents the same workspace-wide rows from appearing once for every agent lane.

**Data flow**: It receives an optional extension context, member information, admin flag, and list query → requires the extension context, checks whether the current object agent is the main one, returns an empty page if not, otherwise builds the page through _page → returns an ObjectPage.

**Call relations**: The broader member object system calls this when it fans out over agents. It uses agent_is_main and object_agent_id to narrow the result, then delegates to ProfileObjects._page for the actual rows.

*Call graph*: calls 2 internal fn (_page, _require_ext); 3 external calls (object_agent_id, object_page, agent_is_main).


##### `ProfileObjects.get`  (lines 376–380)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[Profile] | None
```

**Purpose**: Fetches one enrichment profile by its row name, which is the member’s email address, for callers allowed to read shared workspace data.

**Data flow**: It receives a tool context and row name → checks shared read permission, requires the extension context, looks up the entry by email through _entry → returns the object detail or null if not allowed or not found.

**Call relations**: The PROFILE_OBJECT store uses this for normal single-row reads. It hands the storage and formatting work to ProfileObjects._entry.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 382–397)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[Profile] | None
```

**Purpose**: Fetches one enrichment profile through the member-object path, but only from the main agent. Like member_page, this avoids duplicate workspace-wide rows.

**Data flow**: It receives an optional extension context, row name, member information, and admin flag → requires the extension context, checks whether the current object agent is the main one, returns null if not, otherwise asks _entry for the row → returns a MemberObject or null.

**Call relations**: The member object system calls this for single-row detail reads. It uses the same main-agent narrowing as ProfileObjects.member_page, then delegates to ProfileObjects._entry.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (object_agent_id, agent_is_main).


##### `ProfileObjects.status`  (lines 399–406)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no separate write status for enrichment profiles. Since these rows are read-only through the object API, there is no apply/delete progress to report.

**Data flow**: It receives a context, row name, and optional expected generation → ignores them → returns null.

**Call relations**: The object API may ask the store for mutation status. This implementation simply says there is no status because confirm_website and the scheduled job are the only write path.


##### `ProfileObjects.apply`  (lines 408–417)

```
async def apply(self, ctx: ToolContext, name: str, spec: Profile, old: Profile | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update enrichment profiles directly. This protects the rule that profiles come only from confirmed consent and the enrichment job.

**Data flow**: It receives the requested row name, new profile spec, old profile, and optional generation check → does not store anything → raises VerbNotSupported with an explanation of the correct write path.

**Call relations**: The object system calls this if someone tries to apply a profile change. Instead of passing work elsewhere, it stops the request and points users toward confirm_website.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 419–426)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete enrichment profiles directly through the object API. Clearing a member’s website through confirm_website is the supported way to withdraw lookup consent and remove stored data.

**Data flow**: It receives the context, row name, and optional generation check → does not delete anything through this path → raises VerbNotSupported with the shared write-refusal message.

**Call relations**: The object system calls this if someone tries to delete a row. It deliberately blocks the operation so all writes remain tied to the consent action.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects._page`  (lines 428–431)

```
async def _page(self, ext: ExtensionContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Reads stored profiles and turns them into a paged object-list response. It is the common helper behind the two list paths.

**Data flow**: It receives an extension context and list query → opens a transaction, reads up to the list limit of stored profiles, converts each profile with _row, and applies the query paging helper → returns an ObjectPage.

**Call relations**: ProfileObjects.list and ProfileObjects.member_page call this after their access checks. It centralizes the storage read and row formatting so both list paths behave the same.

*Call graph*: calls 2 internal fn (transaction, _row); called by 2 (list, member_page); 2 external calls (__init__, object_page).


##### `ProfileObjects._entry`  (lines 433–445)

```
async def _entry(self, ext: ExtensionContext, name: str) -> MemberObject[Profile] | None
```

**Purpose**: Reads one stored profile by email and turns it into the detailed object shape. It includes both the list row and the full profile data.

**Data flow**: It receives an extension context and profile name → opens a transaction, looks up the stored profile by email, returns null if absent, otherwise converts it to a row and wraps the full Profile with created and updated timestamps → returns a MemberObject.

**Call relations**: ProfileObjects.get and ProfileObjects.member_detail call this after access and main-agent checks. It uses _row so the detail view and list view show the same row fields.

*Call graph*: calls 2 internal fn (transaction, _row); called by 2 (get, member_detail); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 468–510)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension declaration that the host system loads at startup. It always exposes the read-only profile object and prompt hook, and only exposes the consent action and scheduled lookup job when a provider is configured.

**Data flow**: It reads provider configuration from the environment → if no provider is available, creates a manifest with no lookup tool and no scheduled job; if a provider is available, creates an Enrichment instance, registers the confirm_website tool, and registers the enrichment job → returns the complete Manifest.

**Call relations**: The extension loader calls this to discover what the extension offers. It wires Enrichment.confirm_website into a tool, Enrichment.tick into a scheduled job, PROFILE_OBJECT into object reads, and inject into the prompt-submission hook.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates, provider_from_env).


### `extensions/enrichment/ufo_ext_enrichment/providers.py`

`io_transport` · `startup and enrichment lookup time`

This file answers a simple question: when the enrichment extension sees an email address or company website, how does it learn who or what is behind it? In normal mode it calls People Data Labs, an outside data service. In recorded mode it reads saved response bodies from a JSON file, so tests or safe deployments can replay known answers without sending anything over the network.

At startup, provider_from_env reads environment variables to choose the mode. If live People Data Labs is selected but no API key is available, it returns no provider, meaning the extension can still start but will not enrich anyone. If recordings are enabled with the live provider, the file acts like a read-through notebook: look there first, and if the answer is missing, call the service and write the raw response back for next time.

The file also protects callers from messy outside data. People Data Labs may return a match, a clean “not found,” a rate limit, bad JSON, or an unexpected shape. This module turns those cases into clear project-level results: Person, Company, None, RateLimited, or EnrichmentError. The parsing functions are shared by live and recorded providers, so replayed data is interpreted exactly like fresh data.

#### Function details

##### `RateLimited.__init__`  (lines 80–82)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: Creates a special error for the case where People Data Labs says too many requests have been made. It can carry a suggested waiting time, so the caller knows when trying again may be reasonable.

**Data flow**: It receives a message and an optional retry delay in seconds. It stores the message as the error text and saves the delay on the error object. The result is an exception that can be raised and caught by higher-level code.

**Call relations**: PdlProvider._get creates this error when the outside service returns a rate-limit response. That lets the lookup flow stop cleanly instead of treating the situation like a permanent failure.

*Call graph*: called by 1 (_get).


##### `Provider.source`  (lines 87–87)

```
def source(self) -> ProfileSource
```

**Purpose**: Defines that every provider must be able to say where its data came from. This is a contract rather than working code.

**Data flow**: There is no real data processing here. Any concrete provider must return a source label, such as live People Data Labs or recorded replay, so saved profiles can remember their origin.

**Call relations**: RecordedProvider.source and PdlProvider.source provide the actual answers. Other code can depend on the Provider shape without caring which provider was chosen at startup.


##### `Provider.person`  (lines 89–89)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: Defines that every provider must know how to look up a person from an email address. This is part of the shared provider contract.

**Data flow**: A caller supplies an email address. A real provider must turn that into either a Person record or None if it has no match.

**Call relations**: RecordedProvider.person and PdlProvider.person implement this behavior in different ways. The rest of the extension can call person on any Provider without knowing whether it is using the network or a recordings file.


##### `Provider.company`  (lines 91–91)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: Defines that every provider must know how to look up a company from a website. This is part of the shared provider contract.

**Data flow**: A caller supplies a website. A real provider must return a Company record or None if no company is known.

**Call relations**: RecordedProvider.company and PdlProvider.company implement this behavior. This keeps the rest of the enrichment system independent from the provider choice.


##### `person_from_body`  (lines 137–148)

```
def person_from_body(body: object) -> Person | None
```

**Purpose**: Turns a raw People Data Labs person response into the project’s Person object. It also recognizes the provider’s normal “not found” response and turns that into None.

**Data flow**: It receives an unknown raw response body, usually parsed from JSON. First it checks whether the body says “not found.” If not, it validates that the body has the expected person match shape, copies the useful fields, and returns a Person. If the shape is wrong, it raises EnrichmentError.

**Call relations**: Both PdlProvider.person and RecordedProvider.person call this after they obtain a raw body. Because both routes share this parser, a recorded response and a live response are read in the same way.

*Call graph*: calls 1 internal fn (_is_not_found); called by 2 (person, person); 2 external calls (__init__, __init__).


##### `company_from_body`  (lines 151–163)

```
def company_from_body(body: object) -> Company | None
```

**Purpose**: Turns a raw People Data Labs company response into the project’s Company object. It treats a normal “not found” response as None.

**Data flow**: It receives a raw response body. It first checks for “not found,” then validates the expected company fields. If a nested location is present, it turns that into a Location object before building and returning the Company. Badly shaped data becomes an EnrichmentError.

**Call relations**: Both PdlProvider.company and RecordedProvider.company call this after getting a body. This shared path keeps live and replayed company lookups consistent.

*Call graph*: calls 1 internal fn (_is_not_found); called by 2 (company, company); 3 external calls (__init__, __init__, __init__).


##### `_is_not_found`  (lines 166–167)

```
def _is_not_found(body: object) -> bool
```

**Purpose**: Checks whether a raw response body is People Data Labs’ standard “not found” answer. It is a small helper that keeps person and company parsing from duplicating the same check.

**Data flow**: It receives any object. If that object is a dictionary with a status value of 404, it returns true; otherwise it returns false.

**Call relations**: person_from_body and company_from_body call this before validating a response. It helps them return None for a clean miss instead of raising an error.

*Call graph*: called by 2 (company_from_body, person_from_body).


##### `Recordings.body`  (lines 180–182)

```
async def body(self, key: str) -> object | None
```

**Purpose**: Looks up one saved raw response body from the recordings file. This lets the system replay a previous provider answer without making a network call.

**Data flow**: It receives a key such as a person email key or company website key. It reads the recordings file in a background thread so the async program is not blocked, then returns the saved body for that key or None if the key is absent.

**Call relations**: RecordedProvider uses this as its whole data source. PdlProvider._body also uses it first when recordings are configured, like checking a notebook before calling someone.

*Call graph*: 1 external calls (to_thread).


##### `Recordings.append`  (lines 184–186)

```
async def append(self, key: str, body: object) -> None
```

**Purpose**: Adds or replaces one saved raw response in the recordings file. This is how live lookups can be captured for later replay.

**Data flow**: It receives a key and a raw response body. It takes a lock, which is a guard that stops two writes happening at once, then writes the update in a background thread. The file ends up containing the new body under that key.

**Call relations**: PdlProvider._body calls this after a successful live fetch when recordings are enabled. The lock makes the recording step safer when multiple lookups happen near the same time.

*Call graph*: 1 external calls (to_thread).


##### `Recordings._read`  (lines 188–194)

```
def _read(self) -> dict[str, object]
```

**Purpose**: Reads the whole recordings JSON file into memory. It is the low-level file reader behind lookup and append operations.

**Data flow**: It checks whether the file exists. If not, it returns an empty dictionary. If it exists, it parses the JSON text and confirms the top-level value is an object mapping keys to bodies. If the file has the wrong shape, it raises EnrichmentError.

**Call relations**: Recordings.body calls this indirectly through a thread, and Recordings._append calls it before writing an updated file. It is the single place that defines what a valid recordings file looks like.

*Call graph*: called by 1 (_append); 2 external calls (__init__, loads).


##### `Recordings._append`  (lines 196–201)

```
def _append(self, key: str, body: object) -> None
```

**Purpose**: Rewrites the recordings file with one additional saved response. It writes through a temporary file first, so readers do not see a half-written JSON file.

**Data flow**: It reads the current recordings, sets the given key to the new body, writes the full updated dictionary to a temporary file, and then replaces the real file with that temporary file. The visible file changes from the old complete version to the new complete version.

**Call relations**: Recordings.append calls this while holding the write lock. PdlProvider._body reaches it through append after a live People Data Labs response should be captured.

*Call graph*: calls 1 internal fn (_read); 2 external calls (dumps, getpid).


##### `RecordedProvider.source`  (lines 213–214)

```
def source(self) -> ProfileSource
```

**Purpose**: Reports that this provider’s data comes from recorded responses. This label can be stored with enriched profiles so their origin is clear.

**Data flow**: It takes no input beyond the provider object and returns the fixed source value “recorded.” It does not change anything.

**Call relations**: This fulfills the Provider.source contract for RecordedProvider. Code using a Provider can ask for the source without knowing it is a replay provider.


##### `RecordedProvider.person`  (lines 216–218)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: Looks up a person using only the recordings file. No network request is made.

**Data flow**: It receives an email address, builds the matching recording key, and asks Recordings for the saved body. If no body is saved, it returns None. If a body is present, it passes that body to person_from_body and returns the parsed Person or None.

**Call relations**: This is called when startup selected recorded mode and the enrichment flow asks for a person. It hands parsing to person_from_body so recorded answers match live-answer behavior.

*Call graph*: calls 1 internal fn (person_from_body).


##### `RecordedProvider.company`  (lines 220–222)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: Looks up a company using only the recordings file. It is the company-side replay path.

**Data flow**: It receives a website, builds the matching recording key, and reads the saved body from Recordings. Missing saved data becomes None. Present saved data is passed to company_from_body, which returns a Company or None.

**Call relations**: This is called when recorded mode is active and the enrichment flow asks for a company. It delegates the interpretation of the raw body to company_from_body.

*Call graph*: calls 1 internal fn (company_from_body).


##### `PdlProvider.source`  (lines 236–237)

```
def source(self) -> ProfileSource
```

**Purpose**: Reports that this provider’s data comes from People Data Labs. This helps later code or stored records know the origin of the enrichment.

**Data flow**: It takes no input beyond the provider object and returns the fixed source value “pdl.” It does not change anything.

**Call relations**: This fulfills the Provider.source contract for the live provider. Other enrichment code can treat it the same way it treats RecordedProvider.source.


##### `PdlProvider.person`  (lines 239–247)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: Looks up a person by email through People Data Labs, optionally using recordings as a cache. It also refuses email addresses that are too long for a valid lookup.

**Data flow**: It receives an email address. If the email is over the allowed length, it raises EnrichmentError. Otherwise it asks _body for the raw People Data Labs person response, including only the fields this project needs. It then turns that raw body into a Person or None with person_from_body.

**Call relations**: The enrichment flow calls this when the live provider is active. It uses PdlProvider._body to either replay or fetch the response, then hands parsing to person_from_body.

*Call graph*: calls 2 internal fn (_body, person_from_body); 1 external calls (__init__).


##### `PdlProvider.company`  (lines 249–257)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: Looks up a company by website through People Data Labs, optionally using recordings as a cache. It also refuses website strings that are too long.

**Data flow**: It receives a website. If the website exceeds the allowed length, it raises EnrichmentError. Otherwise it asks _body for the raw company response, requesting only useful company fields, then converts that body into a Company or None with company_from_body.

**Call relations**: The enrichment flow calls this when the live provider is active. It relies on PdlProvider._body for the retrieve-or-replay step and on company_from_body for safe parsing.

*Call graph*: calls 2 internal fn (_body, company_from_body); 1 external calls (__init__).


##### `PdlProvider._body`  (lines 259–267)

```
async def _body(self, key: str, path: str, params: dict[str, str]) -> object
```

**Purpose**: Gets the raw response body for a lookup, choosing between a recording and a live network request. It is the provider’s read-through cache logic.

**Data flow**: It receives a recording key, an API path, and request parameters. If recordings are configured and the key already exists, it returns the saved body. Otherwise it calls _get to fetch from People Data Labs. After a successful fetch, it appends the raw body to recordings if recording is enabled, then returns the body.

**Call relations**: PdlProvider.person and PdlProvider.company call this before parsing results. It calls PdlProvider._get only when there is no usable recorded body.

*Call graph*: calls 1 internal fn (_get); called by 2 (company, person).


##### `PdlProvider._get`  (lines 269–298)

```
async def _get(self, path: str, params: dict[str, str]) -> object
```

**Purpose**: Performs the actual HTTP request to People Data Labs and turns transport-level outcomes into clear enrichment outcomes. HTTP means the web request protocol used to talk to the outside service.

**Data flow**: It reads the deploy API key, receives an API path and query parameters, and sends a GET request with the key in a header. A 200 or 404 response is parsed as JSON and returned. A 429 response becomes RateLimited, including any usable retry delay. Network errors, invalid JSON, missing keys, and other status codes become EnrichmentError.

**Call relations**: PdlProvider._body calls this when it cannot satisfy a lookup from recordings. It uses _retry_after to understand rate-limit timing and creates RateLimited when the service asks the caller to slow down.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 1 (_body); 3 external calls (__init__, AsyncClient, deploy_env).


##### `_retry_after`  (lines 301–310)

```
def _retry_after(header: str | None) -> float | None
```

**Purpose**: Reads a Retry-After header and extracts a positive number of seconds if one is present. A Retry-After header is a server hint saying how long to wait before trying again.

**Data flow**: It receives the header text or None. If there is no header, if the value is not a number, or if the number is not positive, it returns None. Otherwise it returns the number of seconds as a float.

**Call relations**: PdlProvider._get calls this when People Data Labs returns a rate-limit response. The result is placed on the RateLimited error so scheduling code can make a better retry decision.

*Call graph*: called by 1 (_get).


##### `provider_from_env`  (lines 313–348)

```
def provider_from_env() -> Provider | None
```

**Purpose**: Chooses and builds the enrichment provider based on environment variables. This is the startup switchboard for live mode, recorded replay mode, or no provider.

**Data flow**: It reads the provider mode, recordings file name, and deploy API key. In live mode, it returns None if no API key is available, a plain PdlProvider if no recordings file is named, or a PdlProvider connected to Recordings if a safe recordings path is given. In recorded mode, it requires an existing recordings file and returns RecordedProvider. Unknown modes or unsafe/missing files raise RuntimeError.

**Call relations**: This is called during setup so the rest of the extension can receive one Provider-like object or None. It constructs PdlProvider, RecordedProvider, and Recordings as needed, and uses warnings to make important startup choices visible.

*Call graph*: 6 external calls (__init__, __init__, __init__, Path, deploy_env, warn).


### `extensions/enrichment/ufo_ext_enrichment/store.py`

`io_transport` · `background enrichment job and profile/consent request handling`

This file is like the filing cabinet for the enrichment feature. Enrichment means looking up extra person or company details for a seated member, but only after that member has agreed. The file defines three database tables: one for saved enrichment profiles, one for consent decisions, and one for temporary waiting periods after an outside provider says “try later.”

It also defines the shapes of the saved data. Person, Company, Location, and Profile describe what the system expects to store and read back. These shapes are strict, so unexpected fields are rejected instead of silently becoming part of the saved profile.

The main helper classes are Profiles, Consents, and Backoff. Profiles finds members who are ready to enrich, saves profile results, reads saved profiles, and deletes them when needed. Consents records whether a member agreed to enrichment and what website they confirmed. Backoff prevents the job from hammering the provider after a refusal; it waits longer after repeated failures, up to one hour.

A key rule runs through the file: no consent row with granted set to true means no enrichment. A member who never answered and a member who declined are both skipped. That keeps the enrichment job tied to an explicit yes.

#### Function details

##### `due_workspaces`  (lines 161–176)

```
def due_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query that finds workspaces with at least one member ready for enrichment. A workspace is ready only if it has a seated member who granted consent, has no saved profile yet, and is not currently paused by backoff.

**Data flow**: It reads the member, consent, profile, and backoff tables in the form of a SQL query. It filters out members without consent, members already enriched, and workspaces whose retry time is still in the future. It returns a selectable query that produces workspace IDs, rather than running the query itself.

**Call relations**: This is the broad first pass for the enrichment job: it asks “which workspaces should we even look at?” It uses _has_profile as a shared test for whether a member already has an enrichment row, and uses the current time to ignore workspaces still waiting after a provider refusal.

*Call graph*: calls 1 internal fn (_has_profile); 3 external calls (now, exists, select).


##### `_has_profile`  (lines 179–180)

```
def _has_profile() -> sa.ColumnElement[bool]
```

**Purpose**: Creates a small reusable database condition that answers: “does this member already have an enrichment profile?” It keeps the “already enriched” test consistent wherever it is needed.

**Data flow**: It refers to the current member row being considered and checks whether an enrichment_profile row exists for that member ID. It returns a SQL condition, not a true-or-false answer yet; the database evaluates it later when a larger query runs.

**Call relations**: due_workspaces uses it while choosing candidate workspaces, and Profiles.due uses it while choosing candidate members inside one workspace. It is a shared building block that prevents both flows from scheduling duplicate enrichment work.

*Call graph*: called by 2 (due, due_workspaces); 1 external calls (exists).


##### `Profiles.due`  (lines 190–212)

```
async def due(self, limit: int) -> tuple[SeatedMember, ...]
```

**Purpose**: Finds the next seated members in one workspace who are allowed to be enriched and do not yet have a profile. It is what the enrichment worker uses when it is ready to process actual members, not just workspaces.

**Data flow**: It takes a maximum count as input and reads the member and consent tables for this workspace. It keeps only seated members with granted consent and no profile row, orders them by creation time, and returns SeatedMember objects containing member ID, email, and the confirmed website.

**Call relations**: After a higher-level job has picked a workspace, this method narrows the work down to specific members. It reuses _has_profile so it follows the same “do not enrich twice” rule as due_workspaces, then wraps each database row in SeatedMember for the caller.

*Call graph*: calls 1 internal fn (_has_profile); 2 external calls (__init__, select).


##### `Profiles.seated`  (lines 214–224)

```
async def seated(self, member_id: UUID) -> SeatedMember | None
```

**Purpose**: Checks whether a particular member belongs to this workspace and is seated. It gives callers a safe way to confirm that a member is eligible to be treated as an active member before doing profile work.

**Data flow**: It receives a member ID and reads the member table for this workspace. If it finds a seated member, it returns a SeatedMember with the ID and email. If not, it returns nothing.

**Call relations**: Code with a Profiles store can call this before acting on a single member. It uses a simple database select and creates a SeatedMember only when the member passes the workspace and seated checks.

*Call graph*: 2 external calls (__init__, select).


##### `Profiles.write`  (lines 226–249)

```
async def write(self, member_id: UUID, profile: Profile) -> None
```

**Purpose**: Saves an enrichment profile for a member, either by updating an existing row or inserting a new one. This makes repeated writes safe: the caller does not need to know whether the row already exists.

**Data flow**: It receives a member ID and a Profile object. It turns the profile into plain database values, including converting person and company objects into JSON-friendly dictionaries. It first tries to update the existing row for this workspace and member; if no row was updated, it inserts a new row.

**Call relations**: This is the point where enrichment results become durable database records. It uses SQL update first and SQL insert second, so callers can simply say “store this profile” without doing their own existence check.

*Call graph*: 2 external calls (insert, update).


##### `Profiles.forget`  (lines 251–257)

```
async def forget(self, member_id: UUID) -> None
```

**Purpose**: Deletes a saved enrichment profile for one member in this workspace. This is useful when profile data must be removed or recalculated from scratch.

**Data flow**: It receives a member ID and issues a database delete against the enrichment_profile table for this workspace and member. It does not return a value; the database row is gone if it existed.

**Call relations**: Code with a Profiles store calls this when a profile should no longer be kept. It hands the actual removal to the database through a delete statement.

*Call graph*: 1 external calls (delete).


##### `Profiles.rows`  (lines 259–268)

```
async def rows(self, limit: int) -> tuple[StoredProfile, ...]
```

**Purpose**: Reads a batch of saved enrichment profiles for one workspace. It is useful for displaying, exporting, or processing existing profile records in a stable order.

**Data flow**: It receives a limit, reads profile rows for this workspace, orders them by fetch time and member ID, and caps the result size. Each database row is passed through _stored, which turns raw database values back into typed profile objects. It returns a tuple of StoredProfile items.

**Call relations**: This method is a bulk reader. It relies on _stored to perform the conversion from database row to application object, so callers get clean StoredProfile values instead of raw SQL rows.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Profiles.one`  (lines 270–279)

```
async def one(self, member_id: UUID) -> StoredProfile | None
```

**Purpose**: Reads the saved enrichment profile for one member in this workspace. It is the focused version of Profiles.rows for a single member lookup.

**Data flow**: It receives a member ID and queries the enrichment_profile table for that exact workspace and member. If no row exists, it returns nothing. If a row exists, it sends the row through _stored and returns a StoredProfile.

**Call relations**: Code that needs one member’s saved enrichment calls this method. Like the other profile readers, it delegates row-to-object conversion to _stored so the rest of the system sees the same shape every time.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Profiles.by_email`  (lines 281–290)

```
async def by_email(self, email: str) -> StoredProfile | None
```

**Purpose**: Finds a saved enrichment profile by email address within one workspace. It compares email in a case-insensitive way, so “A@EXAMPLE.com” and “a@example.com” match.

**Data flow**: It receives an email string, trims outside spaces, lowercases it, and compares it to lowercased saved email values in the database. If it finds a row, it converts it with _stored and returns a StoredProfile. If not, it returns nothing.

**Call relations**: This supports flows that know an email address rather than a member ID. It uses the same _stored conversion path as Profiles.rows and Profiles.one, keeping returned profile data consistent.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (select).


##### `Consents.record`  (lines 301–316)

```
async def record(self, member_id: UUID, *, granted: bool, website: str | None=None) -> None
```

**Purpose**: Records a member’s answer about whether they allow enrichment, along with the website they confirmed if one was provided. It treats “no row” as not granted, so this method is the place where an explicit answer is written down.

**Data flow**: It receives a member ID, a granted-or-not value, and an optional website. It adds the current time as the decision time. It first updates any existing consent row for this workspace and member; if none exists, it inserts a new row.

**Call relations**: Consent-gathering code calls this after a member answers. The enrichment selection logic later reads these rows, especially Profiles.due and due_workspaces, to decide who may be looked up.

*Call graph*: 3 external calls (now, insert, update).


##### `Backoff.pause`  (lines 328–354)

```
async def pause(self, retry_after: float | None) -> float
```

**Purpose**: Pauses enrichment attempts for this workspace after the provider refuses or asks the system to wait. It either follows the provider’s requested delay or increases the delay after repeated failures, up to one hour.

**Data flow**: It receives an optional retry delay in seconds. It reads the current number of failed attempts for this workspace, calculates the next wait time, stores the new attempt count and retry-after timestamp, and returns the number of seconds chosen.

**Call relations**: When an enrichment attempt cannot continue because the provider pushed back, higher-level job code calls this to mark the workspace as temporarily paused. due_workspaces later sees the backoff row and skips that workspace until the retry time has passed.

*Call graph*: 5 external calls (now, timedelta, insert, select, update).


##### `Backoff.clear`  (lines 356–361)

```
async def clear(self) -> None
```

**Purpose**: Removes the pause record for this workspace. This lets the workspace return to normal after enrichment succeeds or the pause is no longer needed.

**Data flow**: It reads no extra inputs beyond the store’s workspace ID. It deletes the matching row from the enrichment_backoff table and returns nothing.

**Call relations**: A successful enrichment tick can call this to reset the workspace after previous provider refusals. Once cleared, due_workspaces will no longer skip the workspace because of backoff.

*Call graph*: 1 external calls (delete).


##### `agent_is_main`  (lines 364–375)

```
async def agent_is_main(connection: AsyncConnection, workspace_id: UUID, agent_id: UUID) -> bool
```

**Purpose**: Checks whether a given agent is the main agent for a workspace. This helps mirror the core member page rule that only the main agent should see or act in certain portal contexts.

**Data flow**: It receives a database connection, workspace ID, and agent ID. It reads the agent table for that exact pair and returns true if the stored is_main value is true; missing rows or false values become false.

**Call relations**: Code that needs to decide whether an agent has the main-agent role calls this helper. It performs one database select and returns a plain yes-or-no answer.

*Call graph*: 2 external calls (execute, select).


##### `_stored`  (lines 378–392)

```
def _stored(row: sa.Row) -> StoredProfile
```

**Purpose**: Turns a raw database profile row into the application’s StoredProfile shape. It is the translator between database storage and the typed objects used by the rest of the enrichment code.

**Data flow**: It receives a database row containing member ID and profile columns. It validates person and company JSON back into Person and Company objects when present, fixes a missing timezone on fetched_at by treating it as UTC, and returns a StoredProfile containing the member ID and Profile.

**Call relations**: Profiles.rows, Profiles.one, and Profiles.by_email all call this after reading from the database. That means every profile read path gets the same validation, timezone cleanup, and object shape before handing data to callers.

*Call graph*: called by 3 (by_email, one, rows); 2 external calls (__init__, __init__).


### Index implementations
Provides local and external search backends for storing chunks, embeddings, keyword indexes, and vector-search results.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `cross-cutting: active during indexing, re-indexing, deletion, and search`

This file is the project’s default memory search engine. The rest of the system talks in neutral objects like Chunk, Hit, and IndexScope, while this file translates those objects into the database-specific tools that actually store and find text. Without it, a deployment with no custom index backend would have nowhere to put searchable chunks, and recall by keywords or embeddings would not work.

The main class, DefaultIndex, is given a transaction opener. Each operation opens a database transaction, checks whether the connection is Postgres or SQLite, and then uses the right storage and search method. Postgres uses its built-in full-text search plus pgvector, a database extension for comparing numeric vectors. SQLite uses FTS5, its full-text search table, and does vector comparison in Python by scanning rows and computing cosine similarity.

A useful analogy is a bilingual librarian: callers ask the same questions either way, but the librarian speaks Postgres in one building and SQLite in another. The file also keeps the search tables tidy. It can add or update chunks, delete all chunks for one owner, check whether an owner already has chunks, and prune old chunks after re-chunking so stale pieces do not remain searchable.

#### Function details

##### `pgvector_literal`  (lines 35–36)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the bracketed text format that Postgres pgvector expects. This lets vector data be passed safely into SQL statements for storage or search.

**Data flow**: It receives a tuple of floating-point numbers. It converts each value to a plain float representation, joins them with commas, wraps the result in square brackets, and returns that string.

**Call relations**: When DefaultIndex writes chunks or runs vector search on Postgres, it asks this helper to translate the embedding into pgvector’s text form before handing it to the database.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 39–47)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two vectors are using cosine similarity, where higher means the directions are more alike. SQLite uses this because it does not have the same vector search support as Postgres here.

**Data flow**: It receives two equal-length tuples of numbers. It calculates the length of each vector, returns 0 if either length is zero, otherwise divides their dot product by the two lengths and returns the similarity score.

**Call relations**: DefaultIndex.vector uses this only on the SQLite path, after reading stored embeddings from the database. It relies on math.sqrt to compute vector lengths.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 50–51)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Packs an embedding, which is a tuple of floating-point numbers, into raw bytes for SQLite storage. SQLite stores the vector as a blob because it does not have the same native vector column type used by Postgres.

**Data flow**: It receives a tuple of floats. It writes those floats into a compact little-endian binary format and returns the resulting bytes.

**Call relations**: DefaultIndex.upsert calls this when saving chunks into SQLite, so the embedding can be stored in the chunk table.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 54–55)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Reverses pack_embedding by turning stored SQLite bytes back into a tuple of floats. This makes saved embeddings usable again for similarity comparison.

**Data flow**: It receives a byte blob from SQLite. It treats every four bytes as one floating-point number, unpacks the whole blob, and returns the tuple of numbers.

**Call relations**: DefaultIndex.vector calls this on SQLite rows before passing the recovered vector to cosine for scoring.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 58–67)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object, the standard search result shape used by the rest of the system. It keeps database row details from leaking outward.

**Data flow**: It receives a database row and a score. It copies the chunk identity, owner, subject, order, text, and score into a Hit object, then returns that Hit.

**Call relations**: Both lexical and vector search use this as the final packaging step, so callers receive the same kind of result no matter which database or search method produced it.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 177–214)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing chunks with the same identity. This is how searchable memory is refreshed when content is created or reprocessed.

**Data flow**: It receives a tuple of Chunk objects. If the tuple is empty, it does nothing. Otherwise it opens a transaction, detects the database type, writes each chunk’s text and metadata, converts embeddings into the right database format, and for SQLite also refreshes the full-text search row.

**Call relations**: Indexing code calls this when it has chunks ready to store. On Postgres it uses pgvector_literal for embeddings; on SQLite it uses pack_embedding and updates the companion full-text table so later word search can find the text.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 216–223)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes every indexed chunk belonging to one owner, such as one document or memory item. This prevents removed content from still appearing in search results.

**Data flow**: It receives an IndexScope containing the owner kind and owner id. It opens a transaction, deletes matching rows directly on Postgres, and on SQLite first removes matching full-text rows and then removes the main chunk rows.

**Call relations**: Callers use this for explicit removal. DefaultIndex.prune also uses it as the simpler path when there are no chunks to keep.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 225–228)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a particular owner already has any chunks in the index. This helps the rest of the system decide whether indexing work is needed.

**Data flow**: It receives an IndexScope with owner details. It opens a transaction, asks the chunk table for one matching row, and returns true if a row exists or false if none exists.

**Call relations**: This is a small lookup method used by higher-level indexing flow when it needs a yes-or-no answer about existing indexed content.


##### `DefaultIndex.prune`  (lines 230–244)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks for an owner while keeping a known set of current chunk digests. This matters after content is split into chunks again, because old leftover chunks should not stay searchable.

**Data flow**: It receives an IndexScope and a set of chunk digests to keep. If the keep set is empty, it deletes the whole scope. Otherwise it opens a transaction and deletes only rows for that owner whose digest is not in the keep set, including SQLite’s full-text rows when needed.

**Call relations**: Re-indexing code calls this after it knows which chunks are still valid. When there is nothing to preserve, it hands off to DefaultIndex.delete instead of repeating the full-delete logic.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 246–282)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by words in the query, returning text chunks that share terms with the user’s text. This is the keyword-search side of the index.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a maximum result count. If there are no subjects or no usable query terms, it returns no results. Otherwise it opens a transaction, runs the database’s full-text search, ranks matching rows, converts each row into a Hit, and returns the hits as a tuple.

**Call relations**: Search or recall code calls this when it wants word-based evidence. It uses _hit at the end so Postgres and SQLite search rows become the project’s normal Hit objects.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 284–317)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by embedding similarity, meaning it finds text whose stored numeric meaning-vector is close to the query vector. This is the semantic-search side of the index.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. If the embedding or subjects are empty, it returns no results. On Postgres it sends the vector to the database and lets SQL rank rows. On SQLite it reads candidate rows, unpacks their stored embeddings, scores them in Python with cosine similarity, sorts them, and returns the best hits.

**Call relations**: Search or recall code calls this when meaning-based matching is needed. It uses pgvector_literal on Postgres, and on SQLite uses unpack_embedding followed by cosine; both paths finish by using _hit to return normal Hit objects.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 320–330)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system and registers the index backend under the name "default". This is how the wider application discovers and creates DefaultIndex.

**Data flow**: It creates a Manifest containing this extension’s name and version, plus an IndexBackendSpec. That spec says the backend is named "default" and provides a factory that builds DefaultIndex using the transaction opener supplied by the host context.

**Call relations**: The extension loading system calls this during setup. The manifest it returns lets core code choose this backend when no other memory index backend is configured.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `request handling and background indexing`

This file is the bridge between UFO’s memory system and Turbopuffer’s HTTP API. UFO works with small pieces of text called chunks. Each chunk has text, ownership information, and often an embedding, which is a list of numbers that represents the meaning of the text. Turbopuffer stores those chunks in a workspace-specific namespace, like a separate labeled drawer for one workspace’s search data.

The file solves two search needs. First, it supports vector search, which finds chunks with similar meaning using embeddings. Second, it supports lexical search using BM25, a keyword-ranking method that scores documents by how well their words match the query. The code also keeps searches scoped, so a query only looks at the right owner kind and subject set instead of the whole namespace.

For writing data, it converts UFO chunks into Turbopuffer’s column-style request format and uploads them in batches. For cleanup, it can delete all chunks for a scope, or prune only chunks that no longer belong after re-chunking. For reading, it turns Turbopuffer rows back into UFO Hit objects.

A notable detail is HTTP client handling. The index may be used from different event loops, so it keeps one HTTP client per loop. That avoids sharing network connection pools across loops, which can break asynchronous Python programs.

#### Function details

##### `turbopuffer_id`  (lines 49–55)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: This turns UFO’s chunk digest into a document id that Turbopuffer can store safely. Standard SHA-256 digests are shortened with URL-safe base64 so they stay well under Turbopuffer’s id length limit.

**Data flow**: It receives a chunk digest string. If the string looks like a SHA-256 digest, it decodes the hex bytes and re-encodes them as shorter URL-safe base64 without padding; otherwise it leaves the id unchanged. The result is the id sent to Turbopuffer.

**Call relations**: When chunks are uploaded, upsert_body calls this so every chunk has the right Turbopuffer id. delete and prune also call it before sending delete requests, so they remove the same ids that were written earlier.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 58–67)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: This reverses Turbopuffer’s shortened document id back into UFO’s normal chunk digest form. It makes sure search results and exported chunks use the same digest format the rest of UFO expects.

**Data flow**: It receives an id from Turbopuffer. If it has the expected shortened base64 length and can be decoded, it turns it back into a sha256-prefixed hex digest; otherwise it returns the id as-is. The output is a UFO-style chunk digest.

**Call relations**: hit_from_row uses this when turning search rows into Hit objects. _scope_chunks uses it when listing chunks during delete or prune, so cleanup logic compares and reports normal UFO digests.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 70–86)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: This builds the request body used to upload a batch of chunks to Turbopuffer. It arranges chunk data in the column-based shape Turbopuffer expects.

**Data flow**: It receives a tuple of Chunk objects. It pulls out ids, embeddings, owner information, subjects, order numbers, and text into parallel lists, and adds index settings such as cosine distance and full-text search on the text field. It returns a JSON-ready dictionary for the HTTP request.

**Call relations**: TurbopufferIndex.upsert calls this for each upload batch. Inside, it calls turbopuffer_id so the ids in the upload request match the ids used later for deletion.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 89–105)

```
def bm25_query(text: str) -> str
```

**Purpose**: This cleans and shortens a text query before sending it to Turbopuffer’s BM25 keyword search. It prevents meaningless or too-long text from producing bad or rejected searches.

**Data flow**: It receives raw query text. It keeps only tokens that contain a useful run of letters or digits, then checks the byte length against Turbopuffer’s full-text query limit. If needed, it clips the query without keeping a partial final term. It returns the cleaned query string, or an empty string if there is nothing useful to search for.

**Call relations**: TurbopufferIndex.lexical calls this before making a keyword search. If this returns an empty string, lexical search stops early instead of asking Turbopuffer to rank everything on punctuation or one-letter fragments.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 108–112)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: This builds the filter used for normal searches. It limits results to one owner kind and to the allowed recall subjects.

**Data flow**: It receives an owner kind and a set of subjects. It sorts the subjects and returns a Turbopuffer filter expression saying: owner_kind must match, and subject must be in this set.

**Call relations**: TurbopufferIndex._query calls this whenever lexical or vector search runs. It supplies the scoping rules that keep searches from crossing into unrelated stored chunks.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 115–122)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: This builds the filter used when looking through all chunks for one stored owner. It can also continue after a previous id, which supports page-by-page listing.

**Data flow**: It receives an IndexScope, which identifies an owner kind and owner id, plus an optional last-seen id. It returns a Turbopuffer filter requiring that owner kind and owner id, and optionally requiring ids greater than the last-seen id.

**Call relations**: TurbopufferIndex.has_chunks uses this to check whether a scope has at least one stored chunk. TurbopufferIndex._scope_chunks uses it repeatedly while paging through a scope for delete and prune.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 125–134)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: This converts one Turbopuffer result row into UFO’s Hit object. A Hit is the standard shape the rest of UFO uses for search results.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It reads the id, owner fields, subject, ordinal, and text, converts the id back into a chunk digest, and returns a Hit containing all of that information.

**Call relations**: TurbopufferIndex.lexical and TurbopufferIndex.vector call this after they receive rows from _query. It calls chunk_digest_from_id so returned hits match UFO’s normal digest format.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 137–143)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: This turns Turbopuffer’s vector-search result information into a score where higher means better. That gives UFO a consistent way to compare vector hits.

**Data flow**: It receives a result row, that row’s position in the result list, and the total number of rows. If Turbopuffer included a cosine distance, it converts that into similarity by calculating 1 minus the distance. If not, it falls back to a descending rank score based on position. It returns the numeric score.

**Call relations**: TurbopufferIndex.vector calls this for each row returned by _query. The score is then passed to hit_from_row when building the final Hit objects.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex._api`  (lines 160–181)

```
def _api(self) -> httpx.AsyncClient
```

**Purpose**: This provides the HTTP client used to talk to Turbopuffer. It keeps a separate client for each asynchronous event loop, which avoids reusing network connections in a loop they do not belong to.

**Data flow**: It reads the currently running event loop and the instance’s client registry. It removes entries for closed loops, then either returns the existing client for this loop or creates a new httpx asynchronous client with Turbopuffer’s base URL and timeout. It updates the registry when it creates a new client.

**Call relations**: All methods that send HTTP requests call this: upsert, delete, prune, has_chunks, _query, and _scope_chunks. It is the shared doorway through which this backend reaches Turbopuffer.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert); 2 external calls (get_running_loop, AsyncClient).


##### `TurbopufferIndex.upsert`  (lines 183–194)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This uploads chunks into Turbopuffer, replacing or adding documents for those chunk ids. It is used when UFO has new or updated memory chunks to index.

**Data flow**: It receives a tuple of Chunk objects. It ignores chunks without embeddings, gets an authorization header, splits the rest into batches, builds each request body, and posts each batch to the workspace namespace. It returns nothing, but it changes the remote Turbopuffer namespace by writing documents.

**Call relations**: This is one of the main public index operations. It calls _auth for the Bearer token, _api for the HTTP client, _path for the namespace URL, and upsert_body to shape each batch.

*Call graph*: calls 4 internal fn (_api, _auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 196–204)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes all indexed chunks that belong to a given scope. It is used when an owner’s indexed memory should be fully cleared.

**Data flow**: It receives an IndexScope. It gets authorization, lists all chunks in that scope, converts their digests to Turbopuffer ids, and sends delete requests in batches. It returns nothing, but it removes matching remote documents.

**Call relations**: This public index operation calls _scope_chunks to find what exists, turbopuffer_id to convert ids, and then _api, _path, and _auth to send the delete requests.

*Call graph*: calls 5 internal fn (_api, _auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 206–216)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes old chunks in a scope while keeping a specified set of current chunk digests. It is useful after re-chunking, when some old pieces may no longer exist and should not be left behind.

**Data flow**: It receives an IndexScope and a keep set of chunk digests. It lists all chunks in the scope, selects only those not in the keep set, converts them to Turbopuffer ids, and sends batched delete requests. It returns nothing, but it removes stale remote documents.

**Call relations**: Like delete, this public cleanup operation uses _scope_chunks to enumerate existing chunks. It then calls turbopuffer_id, _api, _path, and _auth to delete only the unwanted ids.

*Call graph*: calls 5 internal fn (_api, _auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 218–226)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This checks whether a scope has any chunks stored in Turbopuffer. It is a quick existence test rather than a full export.

**Data flow**: It receives an IndexScope. It builds a query asking for just one id in that scope, sends it to Turbopuffer, and reads whether any rows came back. If the namespace does not exist, it returns false. The output is a boolean answer.

**Call relations**: This public check uses scope_filters to describe the scope, then calls _api, _path, and _auth to make the HTTP query.

*Call graph*: calls 4 internal fn (_api, _auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 228–237)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches stored chunks by keyword text using BM25 ranking. It returns chunks whose words best match the query, within the requested owner kind and subjects.

**Data flow**: It receives query text, allowed subjects, an owner kind, and a limit. It cleans the query, stops early if there is no useful text or no subjects, asks _query to rank by BM25 on the text field, and converts rows into Hit objects with rank-based scores. It returns a tuple of hits.

**Call relations**: This is the public keyword-search path. It calls bm25_query before searching, delegates the HTTP query to _query, and uses hit_from_row to translate Turbopuffer rows into UFO search results.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 239–248)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches stored chunks by meaning using an embedding. It returns chunks with nearby vectors, which usually means similar content.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a limit. If the embedding or subject set is empty, it returns no hits. Otherwise it asks _query to rank by approximate nearest-neighbor vector search, scores each row, drops non-positive scores, and returns Hit objects.

**Call relations**: This is the public semantic-search path. It calls _query to contact Turbopuffer, vector_score to turn result distance or rank into a score, and hit_from_row to produce UFO Hit objects.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 250–265)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: This sends a scoped search query to Turbopuffer and returns the raw rows. It is the shared helper behind both keyword and vector search.

**Data flow**: It receives a ranking instruction, an owner kind, a subject set, and a limit. It builds a request with ranking, result count, requested attributes, and filters, then posts it to the namespace query endpoint. If the namespace is missing, it returns an empty list; otherwise it raises on HTTP errors and returns the response rows.

**Call relations**: TurbopufferIndex.lexical and TurbopufferIndex.vector both call this. It uses query_filters for scoping, _auth for credentials, _path for the namespace route, and _api for the HTTP client.

*Call graph*: calls 4 internal fn (_api, _auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 267–295)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: This lists all chunks stored for one scope, page by page. It is mainly used before deleting, because Turbopuffer deletion needs the document ids.

**Data flow**: It receives an IndexScope and already-built authorization headers. It repeatedly queries Turbopuffer for chunks ordered by id, asking for one page at a time. It converts each row into a Chunk with normal UFO digest format, advances using the last id, and stops when there are no more full pages or when the namespace is missing. It returns the collected chunks.

**Call relations**: TurbopufferIndex.delete and TurbopufferIndex.prune call this before deciding what to remove. It uses scope_filters to describe each page, _api and _path to make the request, and chunk_digest_from_id when rebuilding Chunk objects.

*Call graph*: calls 4 internal fn (_api, _path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 297–299)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: This builds the authorization header needed for Turbopuffer API requests. It reads the API key from UFO’s credential system at the moment it is needed.

**Data flow**: It reads the turbopuffer_api_key credential through the instance’s CredentialAccess object. It wraps that key in an HTTP Authorization header using the Bearer-token format. It returns the header dictionary.

**Call relations**: All request-sending public methods use this directly or indirectly: upsert, delete, prune, has_chunks, and _query. It keeps the API key out of the HTTP client itself, so one index instance can serve the workspace safely.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 301–302)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: This builds the Turbopuffer namespace path for the current workspace. It ensures all requests go to the workspace’s own namespace.

**Data flow**: It receives an optional suffix such as /query. It reads the workspace id from the credential context, prefixes it with the UFO namespace prefix, appends the suffix, and returns the URL path string.

**Call relations**: Every method that talks to Turbopuffer calls this when forming its endpoint: upsert, delete, prune, has_chunks, _query, and _scope_chunks.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 305–322)

```
def manifest() -> Manifest
```

**Purpose**: This describes the extension to UFO so it can be discovered and registered. It tells UFO the extension name, version, required credential, and how to create the Turbopuffer index backend.

**Data flow**: It takes no input. It constructs a Manifest containing one credential slot for the Turbopuffer API key and one index backend specification named turbopuffer. The factory in that specification creates a TurbopufferIndex using the runtime credential context. It returns the Manifest.

**Call relations**: The extension system calls this when loading available extensions. The returned manifest is how the rest of UFO learns that memory.index_backend can be set to turbopuffer.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Runtime contracts and embeddings
Defines the shared runtime interfaces for indexing and memory search, plus the OpenAI embedding transport used to vectorize text.

### `core/src/ufo/runtime/indexing.py`

`domain_logic` · `cross-cutting indexing and retrieval`

This file solves a common retrieval problem: large pieces of text are hard to search well as one block, so they need to be cut into sensible smaller pieces, converted into numeric search fingerprints, and written to an index. Think of it like preparing a book for a library catalog: the file decides how to split the pages into useful cards, while another system decides where to store the cards.

It defines simple value objects such as `Chunk`, `Hit`, and `IndexScope`. A `Chunk` is one searchable piece of text. A `Hit` is a search result. An `IndexScope` names the owner whose indexed chunks should be changed or removed.

It also defines two interfaces, called protocols: `IndexBackend` is the storage/search side, and `EmbedClient` is the service that turns text into embedding vectors, which are lists of numbers that capture meaning for similarity search.

The main workflow is `chunk_embed_upsert`. It takes one text body, splits it with `TextChunker`, asks the embedder for vectors, writes the chunks to the index, then prunes old chunks that no longer match the current text. That last step matters because edited text should not leave stale search results behind.

`TextChunker` tries to split text naturally: paragraphs first, then lines, sentences, punctuation, and finally whitespace. It adds a little overlap between chunks so meaning is not lost at the cut point, and it caps chunk size by characters as a safety limit.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the index storage operation for adding or replacing chunks. An implementation uses it when the system has fresh searchable text that should be written into the index.

**Data flow**: It receives a group of `Chunk` objects, usually already carrying embedding vectors. The backend stores them, replacing existing rows or records with the same chunk identity. It returns nothing, but the index is changed so later searches can find those chunks.

**Call relations**: `chunk_embed_upsert` calls this after text has been split and embedded. The protocol does not say how storage works; an extension supplies the real database, search engine, or other index behavior.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the index storage operation for removing all chunks that belong to one owner. It is used when a whole indexed item should disappear from search.

**Data flow**: It receives an `IndexScope`, which names the owner kind and owner id to remove. The backend deletes all indexed chunks in that scope. It returns nothing, but those chunks should no longer appear in search results.

**Call relations**: `extensions/skill_create/ufo_ext_skill_create/manifest._index_card` calls this when it needs to clear indexed content for a card. The actual deletion is provided by the extension that implements `IndexBackend`.

*Call graph*: called by 1 (_index_card).


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes old chunks for one owner except for a given set that should be kept. It prevents edited text from leaving behind outdated search results.

**Data flow**: It receives an `IndexScope` naming the owner and a set of chunk digests to keep. The backend compares what is currently stored with that keep-set and deletes anything outside it. It returns nothing, but the stored index becomes aligned with the current text.

**Call relations**: `chunk_embed_upsert` calls this after writing the current chunks. That makes the update safe for edits: new or unchanged chunks stay, while chunks from older versions are removed.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This asks whether an owner already has indexed chunks. It is a lightweight check a backend can provide before deciding whether indexing work is needed.

**Data flow**: It receives an `IndexScope` that names the owner to check. The backend looks in its storage for matching chunks. It returns `True` if any exist and `False` if none do.

**Call relations**: No caller is shown in the provided graph, but it is part of the backend contract so other indexing flows can cheaply ask whether content has already been indexed.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches the index using ordinary text matching, such as matching words from a query. It is useful when exact terms or close wording matter.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind to search within, and a maximum number of results. The backend searches stored chunk text and returns matching `Hit` objects with scores.

**Call relations**: No caller is shown in the provided graph, but this is one half of the retrieval contract. An extension implements it so the core can ask for text-based matches without knowing the database details.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches the index using an embedding vector, which is a numeric representation of meaning. It is useful for finding text that is semantically similar even when it uses different words.

**Data flow**: It receives an embedding vector, allowed subjects, an owner kind, and a result limit. The backend compares that vector with stored chunk embeddings and returns the best matching `Hit` objects.

**Call relations**: `core/src/ufo/runtime/queue._shadow_skill_selection` calls this during skill selection to find relevant indexed content by meaning. The backend implementation performs the actual vector search.

*Call graph*: called by 1 (_shadow_skill_selection).


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This turns text strings into embedding vectors, which are lists of numbers used for meaning-based search. It hides the details of the embedding provider from the rest of the runtime.

**Data flow**: It receives a tuple of text strings. The embedder converts each string into one vector and returns the vectors in the same order. It does not store anything by itself.

**Call relations**: `chunk_embed_upsert` calls this before writing chunks to the index, and `core/src/ufo/runtime/queue._shadow_skill_selection` calls it when it needs a query vector for semantic search. The real embedding service is supplied by an implementation of this protocol.

*Call graph*: called by 2 (chunk_embed_upsert, _shadow_skill_selection).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared indexing workflow for one body of text. It splits the text, embeds each piece, writes the pieces to the index, and removes old pieces that no longer belong.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner information, a subject, and the text body. First it asks the chunker for `Chunk` objects. If there are chunks, it sends their text to the embedder, attaches each returned vector to its matching chunk, and upserts them into the index. Finally it builds an `IndexScope` for the owner and prunes every stored chunk whose digest is not in the current set. It returns nothing, but the index ends up matching the latest body.

**Call relations**: This function sits between text preparation and index storage. It calls `EmbedClient.embed` to get vectors, `IndexBackend.upsert` to write the current chunks, and `IndexBackend.prune` to clean up old chunks. It also creates an `IndexScope` and uses `dataclasses.replace` to attach embeddings without mutating the original frozen chunk objects.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This turns one text body into ordered searchable chunks with stable identities. Callers use it before embedding and indexing text.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks `_slices` to split the text into pieces, then numbers each piece, creates a digest for each one with `_digest`, and returns a tuple of `Chunk` objects. The chunks do not yet have embeddings.

**Call relations**: `chunk_embed_upsert` uses this as the first step in indexing. Internally it relies on `_slices` for the actual splitting and `_digest` for each chunk identity, then constructs `Chunk` value objects.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This decides how to split raw text into final text pieces. It keeps chunks near the target size, adds context overlap, and enforces a maximum character length.

**Data flow**: It receives raw text. If the text is blank, it returns no pieces. If it is already short enough by word count, it only trims and character-caps it. Otherwise it recursively splits the text, merges small neighboring pieces, adds overlap between chunks, caps each piece by character length, and returns a list of final strings.

**Call relations**: `TextChunker.chunk` calls this to get the pieces that become `Chunk` objects. It coordinates `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars` as the main chunk-making pipeline.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates how long a text is in word-like units. It includes special handling for Chinese, Japanese, and Korean text, where spaces are not always used between words.

**Data flow**: It receives text and first counts non-whitespace characters. If there is no real content, it returns zero. If enough of the text is CJK characters, it counts non-whitespace characters instead of space-separated words. Otherwise it counts runs of non-whitespace text. The result is an integer length estimate.

**Call relations**: `_slices` uses this to decide whether text is already small enough. `_recursive_split` uses it to decide whether a piece needs further splitting, and `_greedy_merge` uses it to decide whether combined pieces are still a reasonable size.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This is a safety cutter that ensures no chunk is longer than the maximum character limit. It protects downstream systems from very large chunks even if word-based splitting missed something.

**Data flow**: It receives one text string. If it is within the character limit, it returns it as a one-item list, unless it is empty. If it is too long, it slices it into overlapping character windows, trims each slice, and returns the non-empty pieces.

**Call relations**: `_slices` calls this both for short text and after the main splitting pipeline. It is the final guardrail before chunk text is returned.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks large text into smaller pieces by trying natural boundaries from broad to narrow. It prefers paragraph breaks before falling back to rougher cuts.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split using the delimiters for that level, such as blank lines, lines, sentence endings, or punctuation. If splitting does not help, it moves to the next level. If a resulting piece is still too large, it recursively splits that piece more finely. If no delimiters remain, it falls back to whitespace splitting.

**Call relations**: `_slices` calls this for text that is too large. It calls `_split_at_delimiters` to cut at known separators, `_count_words` to test piece size, and `_split_on_whitespace` as the last-resort splitter.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This cuts text at the earliest occurrence of any delimiter in a given group. It keeps the delimiter attached to the piece so punctuation and spacing are not unnecessarily lost.

**Data flow**: It receives text and a tuple of delimiter strings. It repeatedly finds the earliest delimiter that appears in the remaining text, cuts through that delimiter, and continues with the rest. When no delimiter remains, it adds the leftover text. It returns only pieces that contain non-whitespace content.

**Call relations**: `_recursive_split` calls this while trying each delimiter level. It is the low-level cutting tool used before deciding whether the pieces are small enough.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural punctuation or paragraph boundaries are not enough. It cuts text by runs of non-whitespace text, roughly word by word.

**Data flow**: It receives text and looks for word-like runs. If there are normal words, it groups them into chunks of about the target word count and returns joined strings. If there are no normal words, or one extremely long run, it cuts the raw text into fixed-size pieces instead. Blank pieces are skipped.

**Call relations**: `_recursive_split` calls this only after delimiter-based splitting has run out of options. It is the last resort that guarantees very hard-to-split text can still be chunked.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This joins neighboring small pieces so the final chunks are not too tiny. It makes the chunks more useful for search by giving each one enough context.

**Data flow**: It receives a list of split pieces. Starting with the first piece, it tries to append the next piece if the combined text stays within a loose size limit of about one and a half times the target word count. If adding a piece would be too much, it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after `_recursive_split`. It uses `_count_words` and `math.ceil` to test the combined size before deciding whether to merge or start a new chunk.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds a little text from the end of one chunk to the start of the next. The overlap helps searches preserve meaning that crosses a chunk boundary.

**Data flow**: It receives a list of chunks. If there is only one chunk or overlap is disabled, it returns the chunks unchanged. Otherwise it keeps the first chunk as-is, then prefixes each later chunk with trailing context taken from the previous chunk. It returns the adjusted list.

**Call relations**: `_slices` calls this after merging. It walks neighboring chunk pairs with `itertools.pairwise` and asks `_trailing_context` what text should be copied forward.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the overlap text to carry from one chunk into the next. It tries to include useful recent context without starting awkwardly in the middle of a sentence when it can avoid it.

**Data flow**: It receives one chunk of text and finds its word-like runs. If the chunk is not longer than the overlap size, it returns an empty string because duplicating nearly everything would not help. Otherwise it takes the last overlap-sized group of words. If there is a sentence boundary early enough in that trailing text, it drops the earlier sentence fragment and returns the cleaner remainder. If not, it returns the trailing words as-is.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building overlapped chunks. It is the small helper that decides exactly what context gets copied forward.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable identity string for a chunk. The digest lets the index recognize the same chunk again during repeated indexing runs.

**Data flow**: It receives owner kind, owner id, subject, ordinal number, and chunk text. It joins those fields with a separator, hashes the result with SHA-256, and returns a string starting with `sha256:`. The same inputs produce the same digest; changed text or metadata produces a different one.

**Call relations**: `TextChunker.chunk` calls this once for each text piece. Those digests are later used by `chunk_embed_upsert` when pruning, so unchanged chunks can be kept and outdated chunks can be removed.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `core/src/ufo/runtime/memory.py`

`data_model` · `cross-cutting during memory search and browsing`

This file is a small contract between two sides of the system. One side wants to recall useful past information. The other side knows how to search or browse stored memory items. Without this contract, every caller would need to know the details of each memory extension, which would make the system harder to extend and easier to break.

The central data item is `MemoryMatch`, which represents one search result in a provider-neutral way. It includes the kind of memory, the text snippet to show, and optionally a durable object reference, creation time, and subject. In plain terms, it is the card handed back from a memory search: “here is what matched, when it was created, and where to open the full thing if possible.”

`MemorySearchProvider` is a protocol, meaning a formal promise about methods an object must provide. A real memory extension implements this promise. It can search by query text, list recent readable items, and report which kinds of items can be listed.

`MemorySearch` is a thin wrapper around one chosen provider. It does not search by itself. It forwards requests to the provider. This gives the runtime one stable object to pass around while still allowing different memory backends underneath.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the promised search operation that every memory provider must offer. A caller uses it to ask, “given these query phrases and this reader’s access rights, what memory items match?”

**Data flow**: The function receives one or more query strings, a `SourceReader` that represents what the caller is allowed to read, and optional start and end times. A concrete provider uses those inputs to find matching memory items and returns them as a tuple of `MemoryMatch` results. Because this is only a protocol method, this file defines the shape of the operation but not the actual search work.

**Call relations**: Higher-level code talks to a provider through this method when it needs recall. In this file, `MemorySearch.search` is the direct wrapper that calls this provider method and returns whatever results the provider supplies.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This is the promised browsing operation for recent memory items. It lets a caller list readable memory entries in recency order, optionally narrowed to certain kinds of items.

**Data flow**: The function receives a set of subjects that may be read, a maximum number of results, an optional set of memory kinds to include, and an optional cursor that marks where the previous page ended. A concrete provider uses those inputs to return a `ListingPage` of `MemoryMatch` items. The cursor style is like using a bookmark rather than a page number, which avoids skipping or repeating items if new memories arrive while someone is browsing.

**Call relations**: Code that wants to browse recent memory goes through this provider method. In this file, `MemorySearch.list_recent` simply forwards the same request to the selected provider and hands the returned page back to the caller.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This is the promised way for a provider to say which categories of memory items can be browsed. A user interface or caller can use this to offer valid filters.

**Data flow**: The function takes no extra input. A concrete provider returns a tuple of kind names, such as the provider’s own item classes. Since this is a protocol method, the exact list comes from the provider implementation elsewhere.

**Call relations**: Consumers ask this before or during browsing so they know which filters make sense. In this file, `MemorySearch.listable_kinds` forwards the request to the underlying provider.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This method gives callers a stable way to search memory without talking to the provider directly. It is a pass-through doorway to the selected memory provider.

**Data flow**: The method receives a `SourceReader`, query strings, and optional time bounds. It sends those same values to `self.provider.search`, waits for the provider’s answer, and returns the tuple of `MemoryMatch` results unchanged. It does not alter the query or decorate the results.

**Call relations**: A caller uses `MemorySearch` as the runtime-facing object. When search is requested, this method hands the work to `MemorySearchProvider.search`, letting the actual provider decide how to find matches.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This method lets callers browse recent memory through the runtime’s chosen provider. It keeps the public access point simple while leaving the storage-specific work to the provider.

**Data flow**: The method receives readable subjects, a result limit, optional kind filters, and an optional listing cursor. It passes those values directly to `self.provider.list_recent`, waits for the provider to produce a page, and returns that `ListingPage` unchanged.

**Call relations**: A caller that wants recent memory items calls this wrapper. The wrapper delegates to `MemorySearchProvider.list_recent`, which is where the real provider-specific listing happens.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This method exposes the selected provider’s list of browsable memory categories. It is useful when a caller needs to build a filter menu or validate a requested kind.

**Data flow**: The method takes no extra input. It asks `self.provider.listable_kinds` for the provider’s supported kinds and returns that tuple directly. Nothing is stored or changed.

**Call relations**: Runtime consumers call this on `MemorySearch` instead of reaching into the provider. The method immediately hands the request to `MemorySearchProvider.listable_kinds` and returns the provider’s answer.


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup and background embedding/indexing work`

This extension gives the system a ready-made embedding backend: a component that converts pieces of text into lists of numbers, called vectors, so the system can later compare meanings rather than just exact words. It uses OpenAI’s `text-embedding-3-large` model and registers itself under the backend name `default`, so it can be chosen when no other embedding backend is configured.

The file has two main jobs. First, it describes the extension to the host system through `manifest`, including the API key it needs and the factory function used to build the client. Second, it defines `OpenAIEmbedClient`, which performs the actual embedding call.

A notable design choice is that the OpenAI API key is not read when the program starts. It is read each time embedding is requested. This means a local development server can start without a key, but if someone actually tries to embed text without one, the failure is immediate and clear.

Before sending text to OpenAI, `plan_embed_batches` acts like a careful packer filling boxes: it trims any single text item that is too long, then groups items into batches that stay under item-count and character-count limits. This keeps provider requests bounded and predictable.

#### Function details

##### `plan_embed_batches`  (lines 33–49)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for the embedding provider by making sure each request stays within safe size limits. It clips very long text items and groups the remaining text into batches that are small enough to send.

**Data flow**: It receives a tuple of text strings. For each string, it cuts it down to the maximum allowed length, then adds it to the current batch unless that batch would become too large by item count or total characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send to OpenAI.

**Call relations**: This is used by `OpenAIEmbedClient.embed` right before making provider calls. In the bigger flow, it is the safety step between raw text from the system and network requests to OpenAI, preventing one embedding call from carrying an oversized payload.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 63–75)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This method turns one or more text strings into embedding vectors using OpenAI. A vector is a list of numbers that represents the meaning of the text in a form useful for search, ranking, or memory lookup.

**Data flow**: It receives a tuple of text strings. It reads the deploy API key from the environment, and if no key is available, it raises a clear error instead of failing silently. It creates an asynchronous OpenAI client, asks `plan_embed_batches` to split the input into safe-sized batches, sends each batch to OpenAI, sorts the returned rows back into the provider’s stated order, converts the embeddings to plain tuples of floats, and returns all vectors as one tuple.

**Call relations**: When the embedding system needs vectors, it calls this method on the client produced by `build`. During its work, it calls `deploy_env` to find the API key, creates an `openai.AsyncOpenAI` client for the network request, and relies on `plan_embed_batches` so each provider call is properly bounded.

*Call graph*: calls 1 internal fn (plan_embed_batches); 2 external calls (AsyncOpenAI, deploy_env).


##### `build`  (lines 78–83)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function builds the embedding client that the host system will use for this extension. It deliberately does not require an API key at construction time, so the system can start even if embedding is not yet usable.

**Data flow**: It receives an extension context, which represents the workspace or runtime scope, but this implementation does not need to read anything from it. It creates and returns an `OpenAIEmbedClient` with the default model setting. No network call happens here.

**Call relations**: The extension registration points to this function as the factory for the default embedding backend. Later, when embedding is actually requested, the returned `OpenAIEmbedClient` performs the API-key lookup and OpenAI call.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 86–92)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what this extension is: its name, version, required deploy key, and the embedding backend it provides. Without it, the system would not know how to discover or register this OpenAI embedding backend.

**Data flow**: It takes no input. It constructs an `EmbedBackendSpec` naming this backend as `default` and pointing to `build` as the way to create it. It then wraps that in a `Manifest` containing the extension name, version, and API key requirement, and returns that manifest to the extension loader.

**Call relations**: This is the file’s registration point. It hands the host system a manifest, and inside that manifest the embedding backend specification connects the name `default` to the `build` function that creates the actual client.

*Call graph*: 2 external calls (__init__, __init__).
