# Memory, Embeddings, and Search Index Backends  `stage-12.2`

This stage is shared behind-the-scenes support for long-term memory and search. It lets the system save useful information, turn it into searchable form, and bring it back later when an agent or operator needs it. The memory extension manifest is the front door: it registers the recall tools, automatic hooks, background jobs, and the web surface. The core memory and indexing files define common “contracts,” meaning standard shapes that any memory store or search index must follow.

The memory store records memories, searches them, tracks source-page snippets, and runs background indexing jobs. The condenser cleans raw saved material into clearer facts, summaries, profiles, and tidy pages, so memory does not become a pile of duplicates. The OpenAI embedding extension turns text into number lists, called vectors, that help search by meaning. The default index stores and searches chunks in SQLite or PostgreSQL, while the Turbopuffer extension can send the same kind of chunks to an outside search service. Events keeps shared event names and limits consistent. The surface file gives operators a read-only memory explorer.

## Files in this stage

### Memory extension entrypoint
The manifest wires durable memory into tools, hooks, jobs, indexing, storage, consolidation, and the operator surface.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, then tool calls, prompt hooks, page-change handling, and scheduled jobs`

This file is like the reception desk for the memory feature. It does not contain every detail of how memories are stored or summarized, but it says what services exist, when they run, and how other parts of the product reach them.

It defines tools for searching memory, writing new memory, recording corrections, saving first-run setup facts, and asking the system to rebuild facts that came from synced pages. It also registers an automatic recall hook: when a user submits a prompt, the system tries to find relevant memories and quietly adds them to the model’s context before the model answers. This recall is deliberately “best effort”: if it is slow or fails, the user’s turn still continues.

The file also wires page-change hooks. When synced source pages change, one hook indexes page text for search, and another asks the model to derive durable fact rows from those pages. Finally, it declares scheduled jobs that index new memories, merge old related facts, remove duplicates, rewrite wiki-like summary paragraphs, update people profiles, and curate whole memory pages.

Without this file, the memory subsystem would exist as disconnected parts. Agents would not know which memory tools are available, page changes would not feed memory, and scheduled cleanup or summarization would not run.

#### Function details

##### `_date_bound`  (lines 254–265)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date string from the memory search tool into a real timestamp boundary. It lets users search within a time window without needing to provide a full timestamp.

**Data flow**: It receives a string such as "2026-01-31" or a full ISO date-time, plus a flag saying whether this is the end of the range. If the value is missing, it returns nothing. If it is a bare end date, it moves the boundary to the next midnight so that the named day is included. The result is a UTC-aware datetime used by search.

**Call relations**: The memory search tool calls this before searching. If the user supplies invalid date text, the parsing error flows back as a recoverable tool error rather than silently searching the wrong window.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 274–352)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs the main memory search workflow used by the memory tool and by other extensions. It searches both saved memory items and indexed source-page snippets, then merges the results fairly across several focused queries.

**Data flow**: It receives up to three query strings, a source reader that says whose memory and sources may be read, and optional start and end dates. It asks the memory store to recall saved items and search source pages for every query at the same time. It removes duplicates, interleaves results so one query does not crowd out the others, and returns a tuple of MemoryMatch objects with text, dates, subjects, and object references.

**Call relations**: Tool handlers and dependent extensions build this service with an ExtensionContext, then call this method when they need recall-style search. It hands the actual storage work to the memory store and wraps the raw rows into the common MemoryMatch shape used outside this extension.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 354–357)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which classes of memory items can be listed. This helps consumers build filters without keeping a separate hard-coded list.

**Data flow**: It reads the allowed item classes from the ItemClass type definition and returns them as strings. It does not touch the database or change anything.

**Call relations**: This belongs to the search provider interface. Other code can ask the provider what kinds are available before listing recent memory items.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 359–409)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Returns a page of recent live memory items for specific subjects, newest first. This is browsing memory, not searching by meaning.

**Data flow**: It receives subjects, a limit, optional item-class filters, and an optional paging cursor. It builds a database query for non-retired, non-superseded memory rows in the current workspace, applies keyset paging, then converts each row into a MemoryMatch. It returns a ListingPage containing the requested slice and paging position.

**Call relations**: This supports listing-style consumers of the memory provider. It relies on the shared listing helpers so memory browsing behaves like other paged lists in the system.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 412–419)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one search result into a readable bullet line for the agent. It includes the snippet, kind, object reference, and date when available.

**Data flow**: It receives one MemoryMatch. It builds a string starting with the memory kind and text, then appends the object reference and creation date if present. The output is plain text suitable for a tool result.

**Call relations**: The memory search handler calls this for every match before returning results to the agent. It is the final presentation step after search has found and merged matches.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 422–442)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the agent-facing memory_search tool. It lets the agent look up saved facts and synced document snippets before answering a user.

**Data flow**: It receives a ToolContext and validated MemorySearchInput. It parses optional date bounds, builds a MemorySearchService from the extension context, searches using the current source reader, and returns either "No matching memory" or a newline-separated list of formatted matches.

**Call relations**: The manifest registers this as the handler for the memory_search tool. It calls _date_bound, MemorySearchService.search, and match_line to turn tool arguments into a user-visible tool result.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 445–459)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the agent-facing memory_update tool. It records a durable memory item so future conversations can recall it.

**Data flow**: It receives the tool context and a memory write request containing the body, class, kind, confidence, and optional source reference. It chooses the subject from the current audience, writes a MemoryWrite through the memory store, and returns a short confirmation naming the subject.

**Call relations**: The manifest registers this as a side-effecting tool, meaning it changes stored data. The handler delegates the actual save to the memory store.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_correction_handler`  (lines 462–480)

```
async def record_correction_handler(ctx: ToolContext, args: RecordCorrectionInput) -> ToolResult
```

**Purpose**: Records a corrected version of an existing memory item from the memory view. It does not edit the old item directly; it writes the correction as a new fact.

**Data flow**: It receives the corrected memory item id and replacement text. It writes a new fact under the speaker’s current audience, with a source reference that points to the item being corrected. The output is a confirmation message.

**Call relations**: The manifest exposes this as the "Record edit" action bound to the memory collection. Later deduplication work can notice the newer corrected statement and retire the older near-duplicate.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_first_run_handler`  (lines 483–499)

```
async def record_first_run_handler(ctx: ToolContext, args: RecordFirstRunInput) -> ToolResult
```

**Purpose**: Records the team’s initial setup choice as a memory item during first-run onboarding. This makes the chosen tools or setup facts available to later recall.

**Data flow**: It receives one sentence of setup information. It writes that sentence as a normal fact under the speaker’s audience, using a fixed source reference that marks it as coming from first run. It returns a confirmation.

**Call relations**: The manifest registers this as the "Continue" first-run action. It uses the same memory store write path as normal memory updates, but with fixed fact settings.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 502–584)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically tries to add relevant memory to a model turn before the model responds. This gives the model useful context without the user or agent explicitly calling search.

**Data flow**: It receives a hook context for a submitted prompt. If the event is not a user prompt, or if it is a root internal machine-only turn, it returns nothing. Otherwise it computes readable subjects, recalls matching memory with a short timeout, removes topic-only items from the injected text, truncates long lines and the total block, logs what happened, and returns an InjectContext containing "Relevant memory" when there is something safe to add.

**Call relations**: The manifest registers this on the user_prompt_submit event as best effort. It calls the memory store for recall and the observability logger for audit events; if recall fails, it logs the failure and lets the conversation continue without injected memory.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 587–596)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that turns committed memory items into searchable index chunks. This keeps newly written memories available for similarity search.

**Data flow**: It receives an ExtensionContext with index and embedding backends. It checks that both are available, creates a MemoryIndexer with a text chunker and transaction access, and runs it. It returns nothing but updates index-related state through the indexer.

**Call relations**: The manifest registers this as the memory_index job. The job scheduler calls it for workspaces that have memory items waiting for embeddings.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 599–615)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to synced source-page changes by indexing page text and mirroring page state for memory search. This makes document snippets searchable alongside saved memory.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it ignores it. Otherwise it checks for index and embedding backends, builds a PageIndexer, and applies the delivered page changes. It returns no hook injection or visible result.

**Call relations**: The manifest registers this as one of the page_change hooks. The core runner supplies page-change batches and owns the cursor; this function processes each batch for indexing.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 618–629)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to synced source-page changes by deriving durable fact memories from page content. This is how source documents become wiki-like memory rows.

**Data flow**: It receives a hook context containing a page-change batch. If the payload is not a page batch, it does nothing. If no model is wired, it raises an error so the cursor does not advance past unprocessed pages. Otherwise it creates a FactDeriver and applies it to the changed pages, which can write new facts and retire replaced page-derived facts.

**Call relations**: The manifest registers this as a second page_change hook with its own cursor. It works alongside index_pages: one makes pages searchable, the other converts page content into memory facts.

*Call graph*: 2 external calls (__init__, store_for).


##### `rebuild_page_facts_handler`  (lines 641–657)

```
async def rebuild_page_facts_handler(ctx: ToolContext, args: RebuildPageFactsInput) -> ToolResult
```

**Purpose**: Implements the admin-only tool that queues all synced pages to have their derived facts rebuilt. It is for cases where page-derived memory reads badly and should be regenerated.

**Data flow**: It receives a tool context and empty input. It checks that an extension context exists and that the speaker is a workspace admin. If allowed, it deletes the derivation cursor key so the page fact derivation pass starts again from the beginning. It returns a message saying the rebuild has been queued.

**Call relations**: The manifest exposes this as the rebuild_page_facts tool bound to the page collection. It does not rebuild facts itself; it resets the cursor so derive_facts will do the actual replacement work during later page-change processing.

*Call graph*: calls 1 internal fn (speaker_is_admin); 2 external calls (__init__, __init__).


##### `consolidate_memory`  (lines 660–668)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled pass that groups older related facts into higher-level summaries. This reduces clutter while preserving the meaning of repeated or connected facts.

**Data flow**: It receives an ExtensionContext, checks that the embedding backend is available, builds a MemoryConsolidator with database access, workspace id, embeddings, and model access, then runs it. The consolidator performs the actual clustering and summary writing.

**Call relations**: The manifest registers this as the memory_consolidate job. The scheduler calls it only for candidate workspaces that appear to have enough old facts to consolidate.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 671–679)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled pass that collapses duplicate memory rows toward the newest useful copy. This keeps memory from filling with repeated statements.

**Data flow**: It receives an ExtensionContext, checks for embeddings, builds a MemoryDeduper with the workspace database and store access, then runs it. The deduper performs the duplicate detection and retirement updates.

**Call relations**: The manifest registers this as the memory_dedup job. Candidate selection tries to call it only for workspaces with likely duplicate backlogs.

*Call graph*: 1 external calls (__init__).


##### `write_memory_sections`  (lines 682–687)

```
async def write_memory_sections(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled writer that creates or updates section paragraphs for memory pages. A section paragraph summarizes the facts under one subject-and-kind band.

**Data flow**: It receives an ExtensionContext, builds a SectionWriter with transaction access, workspace id, and model access, then runs it. The writer reads live facts and writes or removes section summary rows as needed.

**Call relations**: The manifest registers this as the memory_section job. It is scheduled after curation so the paragraph reflects the facts currently standing beneath it.

*Call graph*: 1 external calls (__init__).


##### `write_memory_overview`  (lines 690–695)

```
async def write_memory_overview(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled writer that creates or updates the opening overview paragraph for a memory page. This gives readers a short high-level summary before the detailed rows.

**Data flow**: It receives an ExtensionContext, builds an OverviewWriter with transaction access, workspace id, and model access, then runs it. The writer reads the relevant live facts and updates the overview memory row.

**Call relations**: The manifest registers this as the memory_overview job. It runs near the section writer so the page overview and section summaries are based on the same nightly state.

*Call graph*: 1 external calls (__init__).


##### `write_member_profiles`  (lines 698–703)

```
async def write_member_profiles(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled writer that updates people profiles from shared memory facts and the roster. It captures each member’s role and current focus where the system can infer them.

**Data flow**: It receives an ExtensionContext, builds a ProfileWriter with transaction access, workspace id, and model access, then runs it. The writer reads facts and member information and writes profile rows.

**Call relations**: The manifest registers this as the memory_people job. It runs after the page summary passes in the nightly window.

*Call graph*: 1 external calls (__init__).


##### `curate_memory_pages`  (lines 706–711)

```
async def curate_memory_pages(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled whole-page curation pass. It reads a subject’s memory page as a whole and retires rows that repeat each other.

**Data flow**: It receives an ExtensionContext, builds a PagePass with transaction access, workspace id, and model access, then runs it. The PagePass performs the full-page model read and retirement decisions.

**Call relations**: The manifest registers this as the memory_page_pass job and marks it as needing the deploy model. It runs before the nightly writing passes so summaries are written from already-curated rows.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 714–719)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with memory items still missing embeddings. An embedding is a numeric representation of text used for similarity search.

**Data flow**: It creates a SQL query selecting distinct workspace ids from memory rows where the embedding digest is missing. It returns the query object, not the rows themselves.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_index job. The scheduler uses it to decide which workspaces need indexing work.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 722–739)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with enough old live facts to make consolidation worthwhile. This avoids spending scheduled work on places with too little useful input.

**Data flow**: It computes an age cutoff from the current time, then creates a SQL query for workspaces with at least the minimum number of eligible old facts. It excludes page-derived, superseded, and retired rows. It returns the query object.

**Call relations**: The manifest uses this query to choose candidates for the memory_consolidate job. The consolidator itself still owns the actual summary work.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 742–763)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces likely to have duplicate tool-written memory rows. This keeps the dedup job focused on real backlog.

**Data flow**: It computes an age cutoff, then creates a grouped SQL query looking for enough old live rows with the same workspace, subject, and item class. It excludes section summaries, page-derived rows, superseded rows, and retired rows. It returns distinct workspace ids as a query.

**Call relations**: The manifest uses this query to choose candidates for the memory_dedup job. Fresh duplicates are left to the write path; this scheduled pass is for older accumulated copies.

*Call graph*: 2 external calls (now, select).


##### `_class_count`  (lines 766–770)

```
def _class_count(item_class: ItemClass) -> sa.Function[int]
```

**Purpose**: Builds a SQL counting expression for rows of one memory class inside a grouped query. It is a small helper for candidate queries that need to count facts separately from summary paragraphs.

**Data flow**: It receives an item class and returns a SQL expression that counts only rows matching that class. It does not run the query itself.

**Call relations**: _summarizable_workspaces and _overviewable_workspaces use this helper when deciding whether a workspace has enough facts to write a paragraph, or already has a paragraph that may need removal.

*Call graph*: called by 2 (_overviewable_workspaces, _summarizable_workspaces); 1 external calls (case).


##### `_summarizable_workspaces`  (lines 773–796)

```
def _summarizable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces where section summaries may need writing or cleanup. A section can need work either because enough facts exist or because an old section paragraph still stands.

**Data flow**: It creates a SQL query over live fact and section rows, grouped by workspace, subject, and memory kind. It keeps groups with enough facts for a section or with any existing section paragraph, then returns distinct workspace ids.

**Call relations**: The manifest uses this query to choose candidates for the memory_section job. It calls _class_count to separate fact counts from section paragraph counts within the same grouped scan.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_overviewable_workspaces`  (lines 799–821)

```
def _overviewable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces where the shared memory overview may need writing or cleanup. It focuses only on the shared subject, not private member memory.

**Data flow**: It creates a SQL query over live shared fact and overview rows. It groups by workspace and keeps workspaces with enough shared facts for an overview, or with an existing overview paragraph that may now need removal. It returns the query object.

**Call relations**: The manifest uses this query to choose candidates for the memory_overview job. It calls _class_count so the query can test facts and overview rows separately.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_peopled_workspaces`  (lines 824–838)

```
def _peopled_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces where people profiles might be writable. A workspace with no shared facts has nothing for the people pass to infer from.

**Data flow**: It creates a SQL query selecting distinct workspace ids that have at least one live shared fact. It excludes superseded and retired rows. It returns the query object.

**Call relations**: The manifest uses this query to choose candidates for the memory_people job. The ProfileWriter later decides what profile text, if any, can be written.

*Call graph*: 1 external calls (select).


##### `_curatable_workspaces`  (lines 841–857)

```
def _curatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with enough facts under a subject to justify a whole-page curation pass. This avoids paying for a model read of pages that are too small.

**Data flow**: It creates a SQL query over live fact rows, groups by workspace and subject, and keeps groups whose row count meets the page-pass minimum. It returns distinct workspace ids as a query.

**Call relations**: The manifest uses this query to choose candidates for the memory_page_pass job. The scheduled PagePass then performs the actual full-page read and retirement work.

*Call graph*: 1 external calls (select).


##### `manifest`  (lines 860–1018)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest: the formal declaration of everything the memory extension contributes to the host system. This is how tools, hooks, jobs, objects, search providers, and web routes become visible.

**Data flow**: It constructs a Manifest object using constants, input models, handlers, candidate queries, object definitions, and route definitions from this file and related modules. The result is a complete registration package; it does not itself run the jobs or tools.

**Call relations**: The host system calls this during extension loading. The returned manifest tells the runtime which handlers to call for agent tools, prompt hooks, page changes, scheduled jobs, memory search provider construction, and the memory surface.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### Built-in index backend
The default index implementation stores and searches text chunks locally with SQLite or PostgreSQL when no external backend is selected.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `cross-cutting`

This file is the project’s default memory search engine. Other parts of the system give it small pieces of text called chunks, often with an embedding, which is a list of numbers that represents the meaning of the text. Later, the system can ask for chunks that match certain words or are close in meaning to a query embedding.

The same public backend works with two different databases. With PostgreSQL, it uses database-native full-text search for word matching and pgvector-style vector comparison for meaning matching. With SQLite, it uses FTS5, SQLite’s built-in full-text search table, for word matching, and calculates vector similarity in Python by scanning stored embeddings. This keeps development simple while still letting larger deployments use PostgreSQL features.

The file also keeps database-specific details hidden. Callers deal with neutral project objects like Chunk, Hit, and IndexScope, not PostgreSQL vector strings or SQLite byte blobs. Think of it like an adapter plug: the outside shape stays the same, but the inside wiring changes for the wall socket.

It can add or update chunks, delete all chunks for one owner, check whether an owner has chunks, prune old chunks after re-indexing, and return ranked search hits. Without this file, a basic deployment would have no default way to remember and retrieve indexed text.

#### Function details

##### `pgvector_literal`  (lines 35–36)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the text format PostgreSQL’s vector extension expects. This lets embeddings be safely passed into SQL statements that compare or store vectors.

**Data flow**: It receives a tuple of floating-point numbers. It converts each value to a plain float representation, joins them with commas, wraps them in square brackets, and returns that string. Nothing else is changed.

**Call relations**: When DefaultIndex.upsert stores a chunk in PostgreSQL, it uses this helper to format the chunk’s embedding. When DefaultIndex.vector searches by meaning in PostgreSQL, it uses the same formatting for the query embedding before handing it to the database.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 39–47)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Compares two embeddings and returns how similar their directions are. This is used for SQLite vector search, where the file does the comparison itself instead of asking the database.

**Data flow**: It receives two equal-length tuples of numbers. It calculates the length of each vector, returns 0 if either one has no length, otherwise divides their dot product by those lengths. The result is a similarity score, where larger means more alike.

**Call relations**: DefaultIndex.vector calls this after reading SQLite rows from the database. It uses math.sqrt to compute vector lengths, then hands the similarity score back to the vector search flow so rows can be sorted from best to worst.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 50–51)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding from Python numbers into compact bytes for SQLite storage. SQLite does not have the same vector type used by PostgreSQL, so the numbers are stored as a binary blob.

**Data flow**: It receives a tuple of floating-point numbers. It packs them into little-endian 32-bit float bytes and returns the byte string. The input tuple is not changed.

**Call relations**: DefaultIndex.upsert calls this when saving chunks to SQLite. The packed bytes later become input for unpack_embedding during SQLite vector search.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 54–55)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts a SQLite-stored embedding back from bytes into Python numbers. This is needed before the code can compare embeddings in Python.

**Data flow**: It receives a byte string whose length is a multiple of four. It reads every four bytes as one floating-point number and returns a tuple of those numbers. It does not change the database or the original bytes.

**Call relations**: DefaultIndex.vector calls this while scoring SQLite rows. The unpacked tuple is passed to cosine so the stored chunk embedding can be compared with the query embedding.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 58–67)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a standard Hit object from a database row and a score. A Hit is the neutral result shape that callers receive, regardless of which database produced the row.

**Data flow**: It receives one row from a SQL result plus a numeric score. It copies the chunk identity, owner information, subject, order number, text, and score into a new Hit object. The returned Hit is ready to be sent back to search callers.

**Call relations**: DefaultIndex.lexical and DefaultIndex.vector both use this helper after they have found matching rows. It keeps result construction in one place so word search and vector search return the same kind of object.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 177–214)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing chunks with the same digest. This is how the backend records the text and optional embedding that later searches will use.

**Data flow**: It receives a tuple of Chunk objects. If the tuple is empty, it does nothing. Otherwise it opens a database transaction, checks whether the connection is PostgreSQL or SQLite, then writes each chunk using the matching SQL. PostgreSQL embeddings are converted with pgvector_literal; SQLite embeddings are converted with pack_embedding, and SQLite’s full-text table is refreshed for each chunk.

**Call relations**: This is one of the main operations exposed by DefaultIndex. It calls pgvector_literal for PostgreSQL vector values and pack_embedding for SQLite binary storage. For SQLite, it also updates the separate full-text search table so later lexical searches can find the text.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 216–223)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks that belong to one owner. This is used when an indexed item should no longer be searchable.

**Data flow**: It receives an IndexScope, which identifies an owner kind and owner id. It opens a transaction and deletes matching rows. In PostgreSQL it deletes from the chunk table; in SQLite it first removes matching full-text rows and then removes the chunk rows.

**Call relations**: DefaultIndex.prune calls this when the keep-set is empty, meaning no chunks for that owner should remain. It is also a core cleanup operation for the backend because it keeps the main chunk table and SQLite full-text table in sync.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 225–228)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a particular owner already has any chunks in the index. This lets the system avoid unnecessary work or decide whether indexing is needed.

**Data flow**: It receives an IndexScope containing the owner kind and owner id. It opens a transaction, asks the database for one matching chunk, and returns true if a row exists or false if none exists. It does not modify stored data.

**Call relations**: This method is part of the DefaultIndex backend’s public behavior. It uses the shared chunk table query and does not call helper functions because it only needs a simple yes-or-no answer.


##### `DefaultIndex.prune`  (lines 230–244)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes stale chunks for one owner while keeping a chosen set of current chunks. This matters after re-chunking, when old pieces of text should not remain searchable forever.

**Data flow**: It receives an IndexScope and a frozen set of chunk digests to keep. If the keep-set is empty, it deletes the whole scope. Otherwise it opens a transaction and removes only rows for that owner whose digest is not in the keep-set. In SQLite it also removes matching rows from the full-text table.

**Call relations**: When there is nothing to keep, it hands off to DefaultIndex.delete. Otherwise it performs database-specific pruning itself, using PostgreSQL or SQLite SQL so the main chunk storage and, for SQLite, the full-text index stay consistent.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 246–282)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches for chunks whose text shares words with a query. This is the word-based search path, useful when exact terms or close word matches matter.

**Data flow**: It receives a query string, a set of subjects to search within, an owner kind, and a maximum number of results. If there are no subjects, or the cleaned query has no terms, it returns no hits. Otherwise it opens a transaction, runs PostgreSQL full-text search or SQLite FTS5 search, ranks the rows by text match score, and converts each row into a Hit.

**Call relations**: This method is the lexical search half of DefaultIndex. After the database returns matching rows, it calls _hit so callers get normal Hit objects rather than raw SQL rows.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 284–317)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches for chunks whose embeddings are closest to a query embedding. This is the meaning-based search path, useful when two pieces of text use different words but discuss similar ideas.

**Data flow**: It receives a query embedding, a set of subjects, an owner kind, and a result limit. If the embedding or subjects are empty, it returns no hits. With PostgreSQL, it formats the query embedding and lets the database score nearby vectors. With SQLite, it reads candidate rows, unpacks each stored embedding, computes cosine similarity in Python, sorts the rows by score, and returns the best hits.

**Call relations**: This method uses pgvector_literal when PostgreSQL can do vector comparison in SQL. For SQLite, it uses unpack_embedding and cosine to score rows in Python. In both paths, it finishes by calling _hit to turn scored rows into standard search results.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 320–330)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the larger system and registers the default index backend. This is how the project discovers that the name "default" should create a DefaultIndex.

**Data flow**: It takes no input. It creates a Manifest containing this extension’s name and version, plus an IndexBackendSpec whose factory builds a DefaultIndex using the transaction opener supplied by the system context. It returns that Manifest.

**Call relations**: This is the connection point between the extension file and the host application. It constructs the IndexBackendSpec and Manifest so the backend core can later create DefaultIndex instances without knowing the class directly.

*Call graph*: 2 external calls (__init__, __init__).


### Memory consolidation and storage
The memory extension cleans raw source material into useful facts, profiles, and summaries, then stores, recalls, and indexes it for later use.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background maintenance`

The memory system stores many small rows: facts from source pages, notes written by tools, and paragraphs shown at the top of wiki-like memory pages. This file is the workshop that keeps those rows useful. It extracts facts from changed source pages, combines older related facts into summaries, collapses near-duplicate tool-written rows, writes section and overview paragraphs, writes a People/profile table, and periodically asks a model to read a whole page and retire repeated rows.

A helpful analogy is a newsroom. Raw source pages are incoming reports. FactDeriver turns each report into clean bullet points. MemoryDeduper removes repeated copies of the same statement. MemoryConsolidator turns clusters of old related bullets into one summary. SectionWriter and OverviewWriter write the short paragraphs above the rows so readers know what the page currently says. ProfileWriter writes a roster-style view of people. PagePass is the editor who reads the whole page and marks rows that make the page worse because they repeat another row.

The file is careful about safety. Expensive model calls happen before database write transactions, so locks are not held while waiting. Reads are bounded, so one large workspace cannot create unlimited prompts. Model output is validated before storage. Rows are usually superseded or retired rather than deleted, so the system can preserve history while keeping live memory clean.

#### Function details

##### `section_headings`  (lines 176–182)

```
def section_headings(subject: str) -> dict[MemoryKind, str]
```

**Purpose**: Chooses the human-readable section titles for a memory page. Shared workspace pages use company-facing wording, while an individual member’s page uses wording addressed to that person.

**Data flow**: It receives a subject string, checks whether that subject means the shared workspace, and returns the matching dictionary of memory kinds to headings.

**Call relations**: SectionWriter uses this when deciding which sections exist and when asking the model to summarize a section. PagePass also uses it when building the page that the model will review.

*Call graph*: called by 3 (_page, _sections, _summarize); 1 external calls (subject_shared).


##### `live_page_link`  (lines 244–257)

```
def live_page_link() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a fact still matches the exact source page revision it came from. This prevents summaries or curation from using facts whose source page has since changed.

**Data flow**: It reads no rows directly. It returns a SQL condition tying a memory item to a page by page id, workspace, subject, and revision.

**Call relations**: member_servable uses it as part of the wider “can a member see this?” rule. PagePass uses it directly because it only wants page-derived rows that still match live source pages.

*Call graph*: called by 2 (_page, member_servable); 1 external calls (and_).


##### `member_servable`  (lines 260–272)

```
def member_servable() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition for rows that a user could actually be shown. Tool-written rows always count; page-derived rows count only if their source page revision is still current.

**Data flow**: It creates a SQL expression: either the row has no source page, or a matching live page exists. The output is a filter used in later database reads.

**Call relations**: SectionWriter, OverviewWriter, and ProfileWriter call this before summarizing facts, so their prose is based only on rows readers can really see.

*Call graph*: calls 1 internal fn (live_page_link); called by 4 (_facts, _facts, _facts, _sections); 2 external calls (or_, select).


##### `ExtractedFact.within_row_budget`  (lines 312–313)

```
def within_row_budget(cls, body: str) -> str
```

**Purpose**: Keeps a model-extracted fact short enough to fit the memory row size limit. It trims at a word boundary so stored text does not end awkwardly in the middle of a word.

**Data flow**: It receives a fact body string during validation, clips it to the allowed length, and returns the safe version.

**Call relations**: This is run automatically when ExtractedFact objects are validated after FactDeriver receives model output.

*Call graph*: 1 external calls (clip_to_word).


##### `FactDeriver.apply`  (lines 349–364)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Processes a delivered batch of source-page changes and turns eligible pages into memory facts. It also retires old facts for pages that disappeared or are intentionally ignored.

**Data flow**: It receives page changes, asks the store which pages are currently live, filters out deleted, tiny, or machine-status pages, sends eligible pages in small batches to _derive, then tells the store which old page facts have been replaced.

**Call relations**: This is the public entry for the page-change consumer. It batches work and hands each batch to FactDeriver._derive.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 366–405)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> dict[UUID, frozenset[UUID]]
```

**Purpose**: Commits the facts extracted from one batch, but only if each page is still at the same revision that was read. This prevents stale model output from overwriting newer page state.

**Data flow**: It receives page changes, rechecks current page state, asks _extract for facts, commits each valid fact to the memory store, and returns the ids of facts that successfully landed for each page.

**Call relations**: FactDeriver.apply calls this for each bounded page batch. It hands the actual model-reading work to FactDeriver._extract, then writes MemoryWrite records through the store.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 407–484)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: Asks the model to read a small group of source pages and record concrete facts from them. It validates the model’s tool output and drops malformed entries instead of trusting them blindly.

**Data flow**: It receives page changes, builds a compact JSON payload with page ids, titles, streams, and clipped bodies, sends a forced tool-call request to the model, validates each returned fact, removes near-restatements, and returns the kept facts.

**Call relations**: FactDeriver._derive calls this before committing facts. It uses _restates to collapse repeated facts within the same model answer.

*Call graph*: calls 1 internal fn (_restates); called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `_content_words`  (lines 487–488)

```
def _content_words(body: str) -> frozenset[str]
```

**Purpose**: Extracts the meaningful words from a sentence-like body by lowercasing it and removing common filler words. This gives duplicate detection a simple content-focused comparison.

**Data flow**: It receives text, splits it into words, removes words such as “the” and “and,” and returns the remaining unique words.

**Call relations**: _restates calls this for both pieces of text it is comparing.

*Call graph*: called by 1 (_restates); 1 external calls (split).


##### `_restates`  (lines 491–503)

```
def _restates(kept: str, candidate: str) -> bool
```

**Purpose**: Decides whether two extracted facts are really the same claim in slightly different words. It protects memory from storing two model outputs that repeat each other.

**Data flow**: It receives two fact bodies, turns each into content words, compares how much of the smaller set is covered by the other, and returns true only when the overlap is high enough.

**Call relations**: FactDeriver._extract calls this while reviewing facts returned by the model, keeping the fuller version when two entries restate one another.

*Call graph*: calls 1 internal fn (_content_words); called by 1 (_extract).


##### `cosine`  (lines 506–514)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how close two embedding vectors are. An embedding is a list of numbers representing text meaning; cosine similarity tells whether two pieces of text point in a similar meaning direction.

**Data flow**: It receives two number tuples, computes their dot product divided by their lengths, and returns a similarity score; if either vector has no length, it returns 0.0.

**Call relations**: MemoryConsolidator._clusters and MemoryDeduper._clusters use this as their basic test for whether two rows belong together.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 544–553)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic pass that turns clusters of old, related tool-written facts into one semantic summary. It reduces clutter while preserving the meaning of related older facts.

**Data flow**: It exits if no model is configured, reads aged facts, groups them by subject, embeds each group, clusters similar facts in a worker thread, and consolidates clusters that are large enough.

**Call relations**: This is the main driver for MemoryConsolidator. It coordinates _aged_facts, _buckets, _embed, _clusters, and _consolidate.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 555–586)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: Finds old live facts that are eligible to be summarized. It only looks at tool-written facts, not facts derived from source pages.

**Data flow**: It computes an age cutoff, queries the database for matching unsuperseded and unretired fact rows, and returns them as _AgedFact records.

**Call relations**: MemoryConsolidator.run calls this first to get the candidate pool for consolidation.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 588–597)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: Groups consolidation candidates by subject so facts about different pages or people are not summarized together. It also caps each subject group to a safe size.

**Data flow**: It receives aged facts, collects them by subject, sorts each group newest first, trims large groups, and returns subject-to-facts pairs.

**Call relations**: MemoryConsolidator.run calls this after loading aged facts, before embedding and clustering.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 599–603)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Turns each candidate fact body into an embedding vector so similar meanings can be found mathematically.

**Data flow**: It receives facts, sends clipped bodies to the embedding service, and returns a mapping from fact id to vector.

**Call relations**: MemoryConsolidator.run calls this before sending facts to _clusters.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 605–626)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: Groups similar facts using their embeddings. It uses a simple newest-first rule: each fact joins the first close-enough cluster or starts a new one.

**Data flow**: It receives facts and their vectors, compares each fact to existing cluster heads with cosine similarity, and returns clusters of facts.

**Call relations**: MemoryConsolidator.run sends this work to a background thread so CPU-heavy comparison does not block the async event loop.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 628–692)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: Replaces one cluster of related facts with a single semantic summary. It writes the summary and marks the original facts as superseded in one safe database transaction.

**Data flow**: It receives a fact cluster, asks _summarize for summary text, locks and verifies the original rows, inserts a new semantic row, and updates the originals to point to it.

**Call relations**: MemoryConsolidator.run calls this for each cluster large enough to collapse. It calls _summarize before opening the write transaction.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 694–704)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: Asks the model to write a concise summary of a cluster of related facts.

**Data flow**: It receives a model and facts, builds a bounded JSON payload of clipped fact bodies, sends a completion request, trims the result to the overview budget, and returns the final summary string.

**Call relations**: MemoryConsolidator._consolidate calls this before writing the semantic summary row.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_to_overview_budget`  (lines 707–719)

```
def _to_overview_budget(summary: str) -> str
```

**Purpose**: Cuts a generated paragraph down to the allowed size. It prefers removing whole sentences, falling back to word-boundary clipping if needed.

**Data flow**: It receives summary text, keeps sentences until the word, sentence, or character budget would be exceeded, and returns the kept paragraph.

**Call relations**: MemoryConsolidator._summarize, SectionWriter._summarize, and OverviewWriter._write all use this to make model-written prose safe to store.

*Call graph*: called by 3 (_summarize, _write, _summarize); 1 external calls (clip_to_word).


##### `_recency`  (lines 722–723)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: Provides a consistent sort key for facts based on creation time and id. It lets newer facts come first while still giving ties a stable order.

**Data flow**: It receives an _AgedFact and returns a tuple of its created_at time and id.

**Call relations**: It is used as the sorting key inside MemoryConsolidator’s bucketing and clustering flow.


##### `_Group.key`  (lines 743–744)

```
def key(self) -> tuple[str, str]
```

**Purpose**: Returns the identity used to order duplicate-sweep groups. A group is defined by subject and item class.

**Data flow**: It reads the group’s subject and item_class fields and returns them as a tuple.

**Call relations**: MemoryDeduper.run uses this key to continue its cursor-based walk through groups.


##### `_Group.fingerprint`  (lines 747–748)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: Returns a small snapshot that says whether a duplicate group has changed since it was last swept. It is based on live row count and latest update time.

**Data flow**: It reads the group’s copy count and latest timestamp, converts the timestamp to text, and returns both as a JSON-friendly list.

**Call relations**: MemoryDeduper.run compares and stores this value to skip groups that have not changed.


##### `MemoryDeduper.run`  (lines 792–804)

```
async def run(self) -> None
```

**Purpose**: Runs one step of the periodic duplicate cleanup. It chooses one eligible group, skips it if unchanged, or collapses near-duplicate rows onto the newest copy.

**Data flow**: It loads duplicate groups, reads the saved cursor, picks the next group, stores the new cursor, checks the group fingerprint, runs _dedup_group if needed, and records the completed fingerprint.

**Call relations**: This is the main driver for MemoryDeduper. It coordinates _groups, _cursor, and _dedup_group.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 806–818)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: Parses the stored duplicate-sweep cursor. It also detects corrupt cursor data instead of silently restarting.

**Data flow**: It receives a stored JSON value, returns an empty tuple for no cursor, returns a subject/item_class tuple for valid data, or raises an error for invalid data.

**Call relations**: MemoryDeduper.run calls this before choosing which group to sweep next.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 820–846)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: Finds subject-and-class groups that have enough live tool-written rows to possibly contain duplicates.

**Data flow**: It queries the database for old enough, live, non-section, tool-written rows, groups them by subject and item class, counts copies, records latest update time, and returns _Group objects.

**Call relations**: MemoryDeduper.run calls this to know what groups exist and where the cursor can move.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 848–853)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: Performs duplicate detection inside one group. It embeds the group’s rows, clusters near matches, and collapses clusters that contain multiple copies.

**Data flow**: It receives a group, loads live copies, embeds their bodies, clusters them in a worker thread, and calls _collapse for each duplicate cluster.

**Call relations**: MemoryDeduper.run calls this after deciding a group is due for sweeping.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 855–870)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: Loads the live rows in a duplicate group, newest first. The newest row becomes the winner if a duplicate cluster is found.

**Data flow**: It receives a group, builds the group’s live-row filter, queries ids and bodies ordered newest first, and returns _LiveCopy records.

**Call relations**: MemoryDeduper._dedup_group calls this before embedding and clustering.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 872–879)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Turns duplicate-candidate row bodies into embedding vectors in bounded batches.

**Data flow**: It receives live copies, batches them, sends clipped bodies to the embedding service, and returns a mapping from row id to vector.

**Call relations**: MemoryDeduper._dedup_group calls this before _clusters compares rows by meaning.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 881–910)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: Groups near-duplicate rows by embedding similarity. Because copies are read newest first, each duplicate group keeps its newest row as the head.

**Data flow**: It receives copies and vectors, compares each copy to cluster heads with cosine similarity, and returns clusters.

**Call relations**: MemoryDeduper._dedup_group runs this in a worker thread before deciding which clusters to collapse.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 912–940)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: Marks all duplicate rows in a cluster as superseded by the newest row. It locks and verifies rows first so it does not point duplicates at a row that changed meanwhile.

**Data flow**: It receives a group and cluster, separates the head from donor rows, locks current matching rows, checks their bodies still match what was embedded, and updates donors to point to the head.

**Call relations**: MemoryDeduper._dedup_group calls this for each duplicate cluster. It uses _live_group to make sure it only touches still-live rows in the same group.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 942–951)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: Builds the database conditions that define a live deduplication group. This keeps duplicate reads and writes using exactly the same rule.

**Data flow**: It receives a group and returns SQL conditions for workspace, subject, item class, no source page, not superseded, not retired, and old enough.

**Call relations**: MemoryDeduper._live_copies uses it to read candidates, and MemoryDeduper._collapse uses it again before updating them.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


##### `_rewrite_in_place`  (lines 972–1022)

```
async def _rewrite_in_place(connection: AsyncConnection, workspace_id: UUID, standing: _Standing, paragraph: str, confidence: int) -> None
```

**Purpose**: Writes one live paragraph for a fixed place in the wiki and supersedes the paragraph that used to stand there. It enforces the rule that a section or overview has one current paragraph, not a growing pile.

**Data flow**: It receives an open database connection, workspace id, paragraph identity, paragraph text, and confidence. It locks current live paragraphs for that place, inserts the new one, and updates old ones to point to it.

**Call relations**: SectionWriter.run and OverviewWriter.run call this after model text has been generated.

*Call graph*: called by 2 (run, run); 5 external calls (execute, insert, select, update, uuid4).


##### `_retire_standing`  (lines 1025–1053)

```
async def _retire_standing(connection: AsyncConnection, workspace_id: UUID, standing: _Standing) -> None
```

**Purpose**: Removes a standing paragraph when its section or overview no longer has enough live facts to justify it. This prevents stale prose from staying above rows that no longer support it.

**Data flow**: It receives a connection, workspace id, and paragraph identity, then updates matching live rows with retired_at and clears embedding fields so they leave search results.

**Call relations**: SectionWriter.run calls this for sections that fell below the threshold. OverviewWriter.run calls it when the workspace overview no longer has enough facts.

*Call graph*: called by 2 (run, run); 2 external calls (execute, update).


##### `SectionWriter.run`  (lines 1079–1102)

```
async def run(self) -> None
```

**Purpose**: Rewrites the paragraph at the top of each fact band, such as Decisions or Tasks. It also retires section paragraphs whose underlying facts have dropped below the minimum.

**Data flow**: It exits if no model is configured, finds sections worth writing, retires currently standing sections no longer eligible, loads facts for each eligible section, asks the model for a paragraph, and writes it in place.

**Call relations**: This is the main driver for SectionWriter. It calls _sections, _standing, _facts, _summarize, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 6 internal fn (_facts, _sections, _standing, _summarize, _retire_standing, _rewrite_in_place).


##### `SectionWriter._sections`  (lines 1104–1137)

```
async def _sections(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds the subject-and-kind bands that currently have enough servable facts to deserve a section paragraph.

**Data flow**: It queries live servable fact counts by subject and memory kind, checks that each memory kind has a valid heading for that subject, and returns _Standing identities for eligible sections.

**Call relations**: SectionWriter.run uses this to decide what to write and what old paragraphs should be retired.

*Call graph*: calls 2 internal fn (member_servable, section_headings); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._standing`  (lines 1139–1157)

```
async def _standing(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds section paragraphs that are currently live, whether or not their facts still justify them.

**Data flow**: It queries live section rows by subject and memory kind and returns their _Standing identities.

**Call relations**: SectionWriter.run compares this with _sections so it can retire paragraphs for bands that no longer qualify.

*Call graph*: called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._facts`  (lines 1159–1180)

```
async def _facts(self, section: _Standing) -> tuple[_SectionFact, ...]
```

**Purpose**: Loads the live facts that will feed one section paragraph. It reads newest facts first and caps the number to keep the model request bounded.

**Data flow**: It receives a section identity, queries servable live facts for that subject and memory kind, and returns body/confidence pairs.

**Call relations**: SectionWriter.run calls this before asking _summarize to write the section paragraph.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._summarize`  (lines 1182–1201)

```
async def _summarize(self, model: ModelAccess, section: _Standing, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write one short paragraph for a specific section. The section heading is included so the model knows what question the paragraph should answer.

**Data flow**: It receives a model, section identity, and facts, builds a JSON payload with the heading and clipped facts, sends a completion request, trims the response to budget, and returns it.

**Call relations**: SectionWriter.run calls this before _rewrite_in_place stores the new paragraph.

*Call graph*: calls 3 internal fn (complete, _to_overview_budget, section_headings); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `OverviewWriter.run`  (lines 1225–1246)

```
async def run(self) -> None
```

**Purpose**: Rewrites the one opening paragraph for the shared workspace memory page. If there are too few facts, it retires the overview instead of leaving stale text.

**Data flow**: It exits if no model is configured, loads shared workspace facts, retires the overview if below the threshold, otherwise reads the workspace domain, asks _write for a paragraph, and writes it in place.

**Call relations**: This is the main driver for OverviewWriter. It calls _facts, _write, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 4 internal fn (_facts, _write, _retire_standing, _rewrite_in_place); 2 external calls (__init__, workspace_domain).


##### `OverviewWriter._facts`  (lines 1248–1269)

```
async def _facts(self) -> tuple[_SectionFact, ...]
```

**Purpose**: Loads the live shared workspace facts that can support the overview paragraph.

**Data flow**: It queries newest servable live facts for the shared subject, across all fact bands, capped to the overview limit, and returns body/confidence pairs.

**Call relations**: OverviewWriter.run calls this to decide whether to write or retire the overview and to supply facts to _write.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `OverviewWriter._write`  (lines 1271–1291)

```
async def _write(self, model: ModelAccess, domain: str | None, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write the workspace overview paragraph from current facts.

**Data flow**: It receives a model, optional workspace domain, and facts, builds a bounded JSON payload, sends a completion request, trims the response to budget, and returns the paragraph.

**Call relations**: OverviewWriter.run calls this before storing the result with _rewrite_in_place.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `WrittenProfile.within_role_budget`  (lines 1328–1329)

```
def within_role_budget(cls, role: str) -> str
```

**Purpose**: Keeps a model-written role short enough to behave like a phrase, not a paragraph.

**Data flow**: It receives a role string during validation, clips it at a word boundary to the role length limit, and returns the clipped role.

**Call relations**: This runs automatically when ProfileWriter validates WrittenProfile entries returned by the model.

*Call graph*: 1 external calls (clip_to_word).


##### `WrittenProfile.within_row_budget`  (lines 1333–1334)

```
def within_row_budget(cls, focus: str) -> str
```

**Purpose**: Keeps a model-written focus sentence within the normal memory row size limit.

**Data flow**: It receives a focus string during validation, clips it at a word boundary, and returns the safe version.

**Call relations**: This runs automatically when ProfileWriter validates WrittenProfile entries returned by the model.

*Call graph*: 1 external calls (clip_to_word).


##### `ProfileWriter.run`  (lines 1372–1387)

```
async def run(self) -> None
```

**Purpose**: Updates the People/profile table with each roster member’s role and current focus. It uses only shared workspace facts so private member-page facts are not leaked to colleagues.

**Data flow**: It exits if no model is configured, loads the roster, loads shared facts, asks the model to write profile entries, matches entries back to roster names, and stores matched entries.

**Call relations**: This is the main driver for ProfileWriter. It calls _roster, _facts, _write, and _store.

*Call graph*: calls 4 internal fn (_facts, _roster, _store, _write).


##### `ProfileWriter._roster`  (lines 1389–1408)

```
async def _roster(self) -> tuple[_Rostered, ...]
```

**Purpose**: Reads the workspace roster and turns each member into the simple identity the People prompt needs.

**Data flow**: It asks Seats for a roster snapshot, takes up to the configured limit, and returns each member’s id, email-as-name, and standing such as admin/member and seated/unseated.

**Call relations**: ProfileWriter.run calls this before asking the model to write profiles.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `ProfileWriter._facts`  (lines 1410–1427)

```
async def _facts(self) -> tuple[str, ...]
```

**Purpose**: Loads shared workspace facts that the People pass may use to describe members.

**Data flow**: It queries newest servable live shared facts, capped to the profile fact limit, and returns their bodies.

**Call relations**: ProfileWriter.run sends these facts, together with the roster, to _write.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 1 external calls (select).


##### `ProfileWriter._write`  (lines 1429–1477)

```
async def _write(self, model: ModelAccess, roster: tuple[_Rostered, ...], facts: tuple[str, ...]) -> tuple[WrittenProfile, ...]
```

**Purpose**: Asks the model to produce one role and current focus per roster member through a forced tool call.

**Data flow**: It receives a model, roster, and facts, builds a compact JSON payload, sends the tool-call request, validates each returned profile entry, drops invalid ones, and returns the valid entries.

**Call relations**: ProfileWriter.run calls this after loading roster and facts, then matches its entries back to roster members before storing.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 4 external calls (__init__, __init__, __init__, dumps).


##### `ProfileWriter._store`  (lines 1479–1508)

```
async def _store(self, entries: tuple[tuple[UUID, WrittenProfile], ...]) -> None
```

**Purpose**: Writes profile entries into the memory_profile table, replacing any previous entry for the same member. This keeps the People band current instead of append-only.

**Data flow**: It receives member/profile pairs, records one timestamp, and upserts each row by workspace id and member id so existing rows are updated and missing rows are inserted.

**Call relations**: ProfileWriter.run calls this after the model output has been validated and matched to real roster members.

*Call graph*: called by 1 (run); 3 external calls (now, insert, insert).


##### `admitted_curation`  (lines 1562–1597)

```
def admitted_curation(bands: tuple[tuple[int, ...], ...], retire: tuple[RetiredRow, ...]) -> AdmittedCuration
```

**Purpose**: Checks whether the page-curation model’s proposed retirements are safe to apply. It makes sure every retired row points to a surviving duplicate and prevents one pass from removing too much of a section.

**Data flow**: It receives the row indexes sent in each band and the model’s retirement entries. It drops invalid retirements, checks duplicate targets, enforces a per-band retirement cap, and returns either admitted row indexes or a refusal reason.

**Call relations**: PagePass._retiring calls this before converting model-selected row numbers into database ids.

*Call graph*: called by 1 (_retiring); 1 external calls (__init__).


##### `PagePass.run`  (lines 1645–1658)

```
async def run(self) -> None
```

**Purpose**: Runs the whole-page curation pass for every subject large enough to need it. It asks the model which page-derived rows are redundant and retires the safe ones.

**Data flow**: It exits if no model is configured, gets eligible subjects, builds each subject’s page, asks _curate for suggested retirements, filters them through _retiring, and applies the resulting row retirements.

**Call relations**: This is the main driver for PagePass. It coordinates _subjects, _page, _curate, _retiring, and _apply.

*Call graph*: calls 5 internal fn (_apply, _curate, _page, _retiring, _subjects).


##### `PagePass._subjects`  (lines 1660–1676)

```
async def _subjects(self) -> tuple[str, ...]
```

**Purpose**: Finds subjects whose memory pages have enough live fact rows to be worth whole-page curation.

**Data flow**: It queries live unsuperseded and unretired fact counts by subject, keeps subjects meeting the minimum row count, and returns them in order.

**Call relations**: PagePass.run calls this first to decide which pages to inspect.

*Call graph*: called by 1 (run); 1 external calls (select).


##### `PagePass._page`  (lines 1678–1724)

```
async def _page(self, subject: str) -> tuple[_Band, ...]
```

**Purpose**: Builds the page payload for one subject: each readable band, its current section summary, and its current page-derived fact rows.

**Data flow**: It receives a subject, loops through that subject’s headings, queries live page-linked facts for each memory kind, fetches any standing section paragraph, assigns short row indexes, and returns bands.

**Call relations**: PagePass.run calls this before sending the page to _curate. It uses live_page_link to avoid stale source-derived rows and section_headings to label bands.

*Call graph*: calls 2 internal fn (live_page_link, section_headings); called by 1 (run); 3 external calls (__init__, __init__, select).


##### `PagePass._curate`  (lines 1726–1775)

```
async def _curate(self, model: ModelAccess, bands: tuple[_Band, ...]) -> CuratedPage
```

**Purpose**: Asks the model to read a whole memory page and name rows that should be retired because another row already carries the claim.

**Data flow**: It receives a model and page bands, builds a bounded JSON payload with headings, summaries, and numbered clipped rows, sends a forced tool-call request, validates returned retire entries, and returns a CuratedPage.

**Call relations**: PagePass.run calls this after _page builds the model payload. PagePass._retiring later checks whether the returned retirements are safe.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, dumps).


##### `PagePass._retiring`  (lines 1777–1788)

```
def _retiring(self, subject: str, bands: tuple[_Band, ...], retire: tuple[RetiredRow, ...]) -> frozenset[UUID]
```

**Purpose**: Turns model-selected row numbers into real memory item ids, but only after safety checks. If the model’s curation is too aggressive, it logs a warning and retires nothing for that subject.

**Data flow**: It receives a subject, bands, and requested retirements, calls admitted_curation, logs a refusal if needed, and otherwise returns the database ids whose row indexes were admitted.

**Call relations**: PagePass.run calls this between model curation and database update. It hands safe ids to PagePass._apply.

*Call graph*: calls 1 internal fn (admitted_curation); called by 1 (run); 1 external calls (warn).


##### `PagePass._apply`  (lines 1790–1806)

```
async def _apply(self, retiring: frozenset[UUID]) -> None
```

**Purpose**: Stamps admitted rows as retired and clears their embedding information so they stop appearing in recall/search.

**Data flow**: It receives memory item ids, opens a transaction, and updates matching live rows in the workspace with retired_at, cleared embedding fields, and a fresh updated_at timestamp.

**Call relations**: PagePass.run calls this as the final step for each subject with safe retirements.

*Call graph*: called by 1 (run); 1 external calls (update).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file is the heart of the memory feature. It solves a practical problem: new memories must be saved quickly, but making them searchable with text chunks and embeddings can be slow. So writes only save the memory row, and background indexers do the heavier search-preparation work later.

A memory here is a short statement tied to a workspace, a subject, and sometimes a source page. The file defines the database tables for memories, links from memories to source pages, and a small mirror of indexed pages. It also defines how memories are validated, listed for operators, recalled for a user query, and retired when a source page changes.

Recall works like asking several librarians at once. One librarian matches exact words, another matches meaning using an embedding (a list of numbers that represents text meaning), and a small “tail” check looks at newly saved rows that are not indexed yet. Their results are blended, then filtered for permissions, stale source-page revisions, retired rows, near duplicates, recency decay, and type diversity.

The two indexer classes keep search results honest. MemoryIndexer publishes or withdraws memory chunks depending on whether their source page is still current. PageIndexer does the same for source pages and marks old page-derived memories as needing a fresh indexing decision.

#### Function details

##### `recall_subjects`  (lines 176–177)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience description into the exact set of subjects that memory recall is allowed to search. A subject is the visibility label used to decide which memories a caller may see.

**Data flow**: It receives an Audience object → passes it to the shared audience helper → returns a frozen set of subject strings.

**Call relations**: This is a small adapter around the SDK audience helper. Other memory code can use it before recall so searches are scoped to the caller’s permitted subjects.

*Call graph*: 1 external calls (audience_subjects).


##### `clip_to_word`  (lines 180–189)

```
def clip_to_word(text: str, limit: int) -> str
```

**Purpose**: Shortens text to a character limit without leaving a chopped-off half word at the end. It adds an ellipsis inside the limit so readers can tell the text was shortened.

**Data flow**: It receives text and a maximum length → if the text already fits, it returns it unchanged; otherwise it cuts near the limit, backs up to the last space if possible, trims trailing punctuation, and adds “…” → returns the shortened string.

**Call relations**: This helper is meant for callers that choose to trim long generated memory text before creating a MemoryWrite. The MemoryWrite validator rejects overlong bodies rather than trimming them automatically.


##### `_granted_link`  (lines 192–200)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds a database permission check for page-derived memories. A memory learned from source pages is readable if the reader has access to at least one linked source.

**Data flow**: It receives a set of readable source IDs → creates an SQL EXISTS condition that looks for a matching memory_source link for the current memory row → returns that condition for use inside a larger database query.

**Call relations**: MemoryStore._untail_leg uses it when scanning not-yet-indexed memories, and MemoryStore._enrich uses it when reading recalled rows back from the database. In both places it is the fence that stops source-derived memories leaking to readers without a source grant.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 240–309)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Reads a bounded, newest-first listing of stored memories for an operator or explorer view. It includes both the raw memory data and live recall signals like age and decay weight.

**Data flow**: It receives a transaction opener and workspace ID → reads recent memory_item rows and their source links from the database → computes age, half-life, and decay multiplier against one shared current time → returns MemoryInventoryItem objects.

**Call relations**: It calls _aware, half_life_days, and decay_multiplier so the inventory view reports the same timing math that recall uses. It is separate from recall: it does not search or hide superseded rows, because it is meant to show the store’s contents.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 312–313)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has timezone information. If a timestamp is missing a timezone, it treats it as UTC.

**Data flow**: It receives a datetime → checks whether it already has timezone data → returns it unchanged or returns a UTC-tagged copy.

**Call relations**: inventory, decay_multiplier, MemoryStore._enrich, and MemoryStore.search_sources use it before comparing or returning timestamps. It keeps age calculations and API results consistent even when database drivers return timezone-naive values.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.body_is_within_budget`  (lines 340–346)

```
def body_is_within_budget(self) -> Self
```

**Purpose**: Rejects memory bodies that are too long to be stored as a single memory. The design expects a memory to be a compact statement, not a whole document.

**Data flow**: When a MemoryWrite is created, it reads its body length → if the body exceeds the configured character limit, it raises a validation error → otherwise the MemoryWrite is accepted unchanged.

**Call relations**: Pydantic, the validation library used by the model, calls this automatically after MemoryWrite fields are filled. MemoryStore.commit can then assume the body fits the memory budget.


##### `MemoryWrite.page_origin_is_complete`  (lines 349–357)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Ensures page-derived memories identify their source completely. If a memory claims to come from a page, it must include the page ID, page revision, and source ID together.

**Data flow**: When a MemoryWrite is created, it checks the three page-origin fields → if some are present but not all, it raises a validation error → otherwise it returns the MemoryWrite unchanged.

**Call relations**: Pydantic calls this automatically during MemoryWrite creation. Later code such as commit, recall, and indexing relies on this all-or-nothing rule to decide whether a memory is member-written or source-page-derived.


##### `_fuse`  (lines 387–410)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines ranked search hits from multiple search methods into one score per owning row. This lets word search and meaning search vote together instead of one method fully replacing the other.

**Data flow**: It receives several legs of Hit objects plus the vector leg → calculates reciprocal-rank fusion, where high-ranked hits in each leg add more credit → keeps each owner’s best chunk text and best cosine similarity → returns a map from owner ID to fused score, cosine score, and snippet text.

**Call relations**: fuse_hits and fuse_recall both call this shared core. It does not know whether owners are memory rows or page rows; it simply merges search evidence from the provided hits.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 413–426)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by blending lexical and vector hits. It also prevents meaningless queries with no word matches from returning arbitrary nearest-neighbor results unless they are semantically close enough.

**Data flow**: It receives lexical hits, vector hits, and a limit → calls _fuse → applies a cosine floor when there were no lexical matches → sorts by fused rank → returns Fused results up to the limit.

**Call relations**: MemoryStore.search_sources calls this after asking the index for page hits. It hands back page owner IDs and snippets that search_sources later verifies against page permissions and current page state.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 429–457)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending exact-word rank, semantic closeness, and a tail of not-yet-indexed memories. It gives recall a balanced first-pass relevance score before recency and cleanup rules are applied.

**Data flow**: It receives lexical hits, vector hits, tail hits, and a limit → calls _fuse → normalizes the rank score and blends it with cosine similarity → applies a minimum cosine only when no lexical leg matched at all → returns the top Fused memory candidates.

**Call relations**: MemoryStore.recall calls this after collecting its three search legs. The returned candidates are then read from the database by _enrich and finally narrowed by _shortlist.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 475–481)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Chooses how quickly a fact should fade in recall ranking. Only normal fact-class memories decay; other memory classes stay ranked by relevance alone.

**Data flow**: It receives an item class and memory kind → if the class is not fact, returns None → otherwise returns the configured half-life for that kind, falling back to the fact default.

**Call relations**: inventory uses it to show operators the decay setting, and decay_multiplier uses it to calculate the actual ranking multiplier.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 484–496)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates how much age and confidence should reduce a fact’s recall score. A newer or more confident fact keeps more of its relevance score.

**Data flow**: It receives item class, memory kind, confidence, the time the information is current as of, and the current time → gets the half-life → for decaying facts, computes confidence scaled by exponential age decay; for non-decaying items or missing dates, returns 1.0.

**Call relations**: decay_factor uses this for recalled items, and inventory uses it for the operator view. It calls _aware so timestamp comparisons are safe.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 499–502)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the shared decay calculation to a Recalled item. It is a convenience wrapper used while ranking recall results.

**Data flow**: It receives a Recalled item and current time → chooses the item’s as_of time, falling back to created_at → calls decay_multiplier → returns the numeric multiplier.

**Call relations**: MemoryStore._shortlist calls this when it re-scores recalled candidates by recency and confidence.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 510–512)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns a memory body into small three-word fingerprints used for duplicate detection. This makes it easy to compare whether two bodies say almost the same thing.

**Data flow**: It receives text → lowercases it, splits it into words, groups neighboring words into three-word phrases → returns those phrases as a set.

**Call relations**: drop_near_duplicates calls this for each candidate body. The resulting sets are compared with Jaccard overlap, a simple ratio of shared fingerprints to total fingerprints.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 515–541)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes recall candidates whose bodies are too similar to already-kept candidates. This stops repeated copies of the same fact from using up many recall slots.

**Data flow**: It receives ranked Recalled items and a keep count → walks candidates in order, builds body shingles for each, skips items that overlap too much with a kept item, and stops once enough distinct items are kept → returns the filtered tuple.

**Call relations**: MemoryStore._shortlist calls this after scoring by decay and before enforcing type diversity. It calls _body_shingles to perform the cheap text-similarity check.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 544–562)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from crowding out all other kinds of recall results. It keeps variety while still preserving rank order as much as possible.

**Data flow**: It receives ranked rows and a limit → admits only a capped number per item class at first → saves overflow rows for possible backfill → fills remaining slots from overflow if needed → returns up to the requested limit.

**Call relations**: MemoryStore._shortlist calls this as the final narrowing step. It works after duplicate removal so the final recall set is both varied and not repetitive.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 565–575)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Changes episodic memory hits into lightweight topic pointers instead of injecting their full body text. Episodic memories are treated as breadcrumbs to browse, not as direct context to quote.

**Data flow**: It receives a Recalled item and its position → if the item is not episodic, returns it unchanged; if it is episodic, returns a copied item with a short pointer body and recall_mode set to topic.

**Call relations**: MemoryStore.recall applies this to each shortlisted result before returning. It uses dataclasses.replace so the original Recalled object is not mutated.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 602–728)

```
async def commit(self, write: MemoryWrite) -> UUID
```

**Purpose**: Saves one memory row quickly without doing indexing work inline. It gives identical workspace/subject/class/body combinations the same stable ID so the same fact can be reasserted instead of duplicated exactly.

**Data flow**: It receives a MemoryWrite → derives a content-based UUID → inserts or updates memory_item, clearing indexing state when the page binding changed and reviving superseded rows when restated → records a memory_source link for page-derived writes → returns the memory ID.

**Call relations**: Callers that derive facts from pages or tools use this to persist each memory. The returned IDs let page derivation later call supersede_page_facts with the set of facts still supported by that page.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 730–851)

```
async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None
```

**Purpose**: Removes or re-points the links between one source page and the memories it used to support. This keeps page-derived memories from staying live after the page no longer backs them.

**Data flow**: It receives a page ID and either a kept set of memory IDs or None for a gone page → deletes stale memory_source links → deletes memory rows with no remaining links, or rebinds rows to a surviving link and marks them due for indexing again → deletes index chunks for fully deleted rows.

**Call relations**: The fact-deriving page workflow is its intended caller after it has committed the facts a page still supports. It hands deleted memory IDs to the index backend so their searchable chunks are removed.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 853–892)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Answers a memory search for a user query while respecting subjects, source permissions, stale-page checks, deduplication, recency decay, and diversity. This is the main read path for injecting useful memories into a later interaction.

**Data flow**: It receives a query, allowed subjects, limit, optional time window, and source reader → gets readable source IDs → asks _legs for indexed lexical/vector hits → asks _untail_leg for newly written unindexed hits → fuses them with fuse_recall → reads valid rows with _enrich → runs _shortlist in a worker thread → converts episodic items to topic pointers → returns Recalled items.

**Call relations**: This method wires together most of the recall helpers. It calls _source_ids, _legs, _untail_leg, fuse_recall, _enrich, _shortlist, and as_topic_pointer in sequence.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 894–909)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Turns a larger candidate pool into the final recall slots. It applies score decay, removes near duplicates, and keeps memory types from becoming too one-sided.

**Data flow**: It receives enriched recalled candidates, a limit, and current time → multiplies each score by decay_factor → sorts descending → calls drop_near_duplicates with an oversampled keep size → calls enforce_type_diversity → returns the final tuple.

**Call relations**: MemoryStore.recall runs this in a worker thread because it is CPU work with no await points. It calls decay_factor, drop_near_duplicates, and enforce_type_diversity.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 911–977)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches indexed source pages and returns matching snippets, not memory facts. It uses the same word-plus-meaning search style as recall but checks that each page is still current and readable.

**Data flow**: It receives a query, subjects, limit, optional time window, and source reader → gets page hits from _legs → fuses them with fuse_hits → reads matching mem_page rows → asks _readable_states for current readable page state → keeps only pages whose subject and revision still match → returns SourceMatch objects.

**Call relations**: This is the page-search counterpart to MemoryStore.recall. It calls _legs, fuse_hits, _readable_states, and _aware before returning verified source matches.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 979–985)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Gets the set of source IDs the current reader is allowed to read. Source grants are needed before page-derived memories can be safely returned.

**Data flow**: It receives a SourceReader → if no grant authority was wired into the store, raises an error → otherwise calls readable_source_ids and returns the readable source ID set.

**Call relations**: MemoryStore.recall calls this before searching the unindexed tail and enriching recalled rows. The result is later used by _granted_link-based database filters.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 987–999)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main index searches for a query: lexical search for words and vector search for meaning. It returns both result lists so a caller can fuse them differently depending on use.

**Data flow**: It receives a query, subjects, owner kind, and limit → embeds the query with _embed_query → always asks the index for lexical hits → asks for vector hits only if embedding succeeded → returns lexical and vector hit tuples.

**Call relations**: MemoryStore.recall calls this for memory_item owners, and MemoryStore.search_sources calls it for page owners. It delegates embedding failure handling to _embed_query.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 1001–1057)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Searches newly saved memories that have not yet been indexed. This makes a just-committed memory recallable immediately instead of waiting for the background indexer.

**Data flow**: It receives a query, subjects, limit, and readable source IDs → splits the query into terms → scans a bounded set of newest memory rows with no embedding digest, excluding superseded or retired rows and enforcing source grants → scores each row by term counts in the body → returns sorted Hit objects.

**Call relations**: MemoryStore.recall calls this as the third recall leg. It uses _granted_link when source-derived memories need permission checks, and its hits are later blended by fuse_recall.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 1059–1067)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Converts a query into an embedding vector for semantic search. If the query is blank or embedding fails, it safely returns no vector so recall can still use lexical search.

**Data flow**: It receives query text → returns an empty tuple for blank text → otherwise calls the embed backend → on success returns the first vector, and on failure logs a warning and returns an empty tuple.

**Call relations**: MemoryStore._legs calls this before vector search. This keeps embedding outages from breaking recall and source search completely.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 1069–1149)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused memory IDs from the index into full Recalled objects from the database, while applying final safety fences. It is where stale, superseded, retired, unauthorized, or out-of-window memories are dropped.

**Data flow**: It receives fused candidates, subjects, readable source IDs, and optional time bounds → reads matching memory_item rows with subject, supersession, retirement, time, and source-grant filters → checks page-derived rows against current page state and revision → returns Recalled objects in fused order.

**Call relations**: MemoryStore.recall calls this after fuse_recall. It uses _granted_link for source permissions, _aware for timestamps, and page_states to make sure page-derived memories still match the live page revision.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1151–1158)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads current page states through the source-aware permission path. It is used when source page search needs to prove a page is still readable and current.

**Data flow**: It receives page IDs and a SourceReader → returns an empty dict for no IDs → raises if no readable-page authority was wired → otherwise calls readable_page_states and returns its page-state map.

**Call relations**: MemoryStore.search_sources calls this after it has candidate page IDs from the index and mem_page mirror. The returned states decide which snippets can be shown.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1161–1174)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a MemoryStore from the extension context. It makes sure the required index and embedding backends are present before memory operations start.

**Data flow**: It receives an ExtensionContext → checks that ext.index and ext.embed are available → copies transaction, workspace ID, page-state readers, and source-grant readers into a new MemoryStore → returns it.

**Call relations**: This is the factory that wires the memory domain to the host extension runtime. Callers use the returned MemoryStore for commit, recall, supersession, and source search.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1198–1200)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing pass over due memory items. It claims a batch, then processes each item one at a time.

**Data flow**: It takes no explicit input beyond the MemoryIndexer’s configured backends → calls _claim_due to get rows needing indexing → calls _index_item for each claimed MemoryItem → returns nothing after the pass completes.

**Call relations**: A background scheduler calls this job. It is the simple driver that connects batch claiming to per-item indexing decisions.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1202–1238)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically reserves a bounded batch of memory rows that still need an indexing decision. The reservation prevents overlapping indexer runs from embedding the same row at the same time.

**Data flow**: It reads the current time and lease cutoff → selects rows whose embedding digest is missing and whose claim is absent or expired → marks selected rows as claimed → returns them as MemoryItem objects.

**Call relations**: MemoryIndexer.run calls this first. MemoryIndexer._index_item then processes the returned items, and _settle later clears each claim when the decision is complete.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1240–1277)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Decides whether one claimed memory should be published to the search index, withdrawn from it, or left for a later run. It protects recall from showing chunks tied to stale page revisions.

**Data flow**: It receives a MemoryItem → if retired or not publishable, deletes its index chunks and settles it → otherwise ensures chunks exist by calling chunk_embed_upsert if needed → rechecks the row’s current binding in the database → deletes chunks or returns if the binding changed → settles the item if still valid.

**Call relations**: MemoryIndexer.run calls this for each claimed item. It relies on _publishable for live-page checks and _settle to stamp the final digest and release the claim.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1279–1288)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Checks whether a memory is allowed to appear in the search index. Member-written memories are always publishable, but page-derived memories only are publishable while their source page still has the same subject and revision.

**Data flow**: It receives a subject, optional page ID, and optional revision → returns true immediately when there is no page ID → otherwise reads current page state → returns true only if the page exists and matches the expected subject and revision.

**Call relations**: MemoryIndexer._index_item calls this before and after indexing work. The double check avoids publishing stale chunks if a source page changes during the indexing pass.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1290–1314)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by the indexer. Settling means the row no longer appears due, whether its chunks were published or deliberately withheld.

**Data flow**: It receives the MemoryItem that was claimed → computes a SHA-256 digest of the body → updates the matching database row with that digest, clears embedding_claimed_at, and refreshes updated_at, but only if the row still matches the claimed binding and is still claimed → returns nothing.

**Call relations**: MemoryIndexer._index_item calls this at terminal points. The guarded update prevents an old indexing decision from overwriting a newer page binding or another run’s work.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1337–1339)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the memory extension’s page index. It is the batch-level entry for page indexing work.

**Data flow**: It receives a tuple of PageChange objects → loops through them in order → calls _apply for each change → returns when all changes have been processed.

**Call relations**: The outer page-change runner owns batching and cursor progress, then calls this method. PageIndexer.apply delegates all per-change decisions to _apply.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1341–1399)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Processes one source-page change by updating or deleting that page’s searchable chunks and mirror row. It also marks old facts from abandoned page revisions as due for withdrawal by the memory indexer.

**Data flow**: It receives a PageChange → reads current page state → calls _unsettle_left_behind_facts → for tombstones or stale changes, deletes page chunks and mem_page row when appropriate → for current live changes, chunks and embeds the page body → rechecks the page state → upserts the mem_page mirror row if still valid.

**Call relations**: PageIndexer.apply calls this for each change. It uses chunk_embed_upsert to publish page text, IndexScope to delete stale page chunks, and _unsettle_left_behind_facts to wake memory rows that depended on old page revisions.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1401–1429)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory rows as needing a fresh indexing decision when their source page has moved on or disappeared. This is how old fact chunks get withdrawn even if no replacement facts are produced.

**Data flow**: It receives a page ID and the page’s current state, if any → builds a database filter for memory rows created from that page that no longer match the live subject/revision, or all rows from the page if it is gone → clears their embedding digest and claim timestamp → returns nothing.

**Call relations**: PageIndexer._apply calls this before handling each page change. The MemoryIndexer later sees these rows as due and deletes or republishes chunks based on _publishable.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### External indexing and runtime contracts
Shared runtime interfaces define memory and chunk-index behavior, while Turbopuffer provides an alternate external vector and keyword search backend.

### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `jobs/serve indexing and search operations`

This extension is the bridge between UFO’s internal memory system and Turbopuffer’s HTTP API. UFO works with chunks of text, each with an optional embedding, which is a list of numbers that represents meaning. Turbopuffer stores those chunks in a workspace-specific namespace, like putting each workspace’s index cards in its own labeled drawer.

The file supports two kinds of search. Vector search finds chunks whose embeddings are close to a query embedding, which is useful for “find text with similar meaning.” Lexical search uses BM25, a keyword ranking method, which is useful for “find text with these words.” Both searches are scoped by owner kind and subject so results come from the right part of memory, not from unrelated stored data.

It also keeps the index tidy. Upsert writes new or changed chunks. Delete removes all chunks for a given owner. Prune removes only chunks that are no longer in a supplied keep-set, which matters when content is re-chunked and old pieces should not linger.

Authentication is done by reading a Turbopuffer API key from UFO’s credential system and sending it as a Bearer token on each request. The manifest at the bottom registers this backend so the rest of the system can select it with the configured index backend name.

#### Function details

##### `turbopuffer_id`  (lines 48–54)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: This turns UFO’s chunk digest into a document ID that Turbopuffer can store. Standard SHA-256 digests are shortened with URL-safe base64 so they fit comfortably within Turbopuffer’s ID limits.

**Data flow**: It receives a chunk digest string. If the string is not in the expected SHA-256 form, it leaves it alone; if it is, it converts the hexadecimal digest bytes into a shorter base64url string and returns that as the storage ID.

**Call relations**: When chunks are written, upsert_body calls this so each chunk gets the right Turbopuffer document ID. When chunks are deleted by TurbopufferIndex.delete or TurbopufferIndex.prune, those methods call it again so the IDs being deleted match the IDs that were originally stored.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 57–66)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: This reverses the shortened Turbopuffer document ID back into UFO’s normal chunk digest form. It keeps search results and exported rows speaking UFO’s usual language.

**Data flow**: It receives an ID read from Turbopuffer. If it looks like the shortened 43-character base64url form, it decodes it and returns a sha256-prefixed digest; if decoding fails or the ID is some other shape, it returns the original ID unchanged.

**Call relations**: TurbopufferIndex._scope_chunks uses this when rebuilding Chunk objects from rows it fetched for deletion or pruning. hit_from_row uses it when turning a Turbopuffer search row into a Hit that the rest of UFO can understand.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 69–85)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: This builds the JSON request body used to write a batch of chunks to Turbopuffer. It arranges the data in Turbopuffer’s expected column-style format and enables full-text search on the text field.

**Data flow**: It receives a tuple of Chunk objects. It pulls out their IDs, embeddings, owner information, subjects, order numbers, and text into parallel lists, then returns a dictionary ready to send as JSON to Turbopuffer.

**Call relations**: TurbopufferIndex.upsert calls this for each write batch. Inside, it calls turbopuffer_id so the IDs in the request match Turbopuffer’s storage requirements.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 88–104)

```
def bm25_query(text: str) -> str
```

**Purpose**: This cleans and shortens a keyword search query before sending it to Turbopuffer’s BM25 text search. It prevents empty-looking punctuation or one-letter queries from making every stored chunk appear equally relevant.

**Data flow**: It receives raw query text. It keeps only tokens that contain a run of at least two letters or digits, then checks the byte length; if the query is too long for Turbopuffer’s full-text limit, it clips it and avoids ending in the middle of a term when possible. It returns the cleaned query text, or an empty string if nothing useful remains.

**Call relations**: TurbopufferIndex.lexical calls this before running a keyword search. If it returns an empty string, lexical search stops early rather than asking Turbopuffer a meaningless query.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 107–111)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: This builds the filter used for normal searches. It limits results to one owner kind and to the allowed subjects, so search does not cross into unrelated memory.

**Data flow**: It receives an owner kind and a set of subjects. It returns a Turbopuffer filter expression saying the owner kind must match and the subject must be one of the supplied subjects.

**Call relations**: TurbopufferIndex._query calls this whenever lexical or vector search needs to ask Turbopuffer for ranked results within the proper recall scope.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 114–121)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: This builds the filter used when looking through all chunks belonging to one stored owner. It can also continue after a previous ID, which allows paging through many chunks.

**Data flow**: It receives an IndexScope, which names an owner kind and owner ID, plus an optional last-seen ID. It returns a Turbopuffer filter expression for that owner, adding an ID-greater-than condition when continuing a paged export.

**Call relations**: TurbopufferIndex.has_chunks uses this to check whether a scope has anything stored. TurbopufferIndex._scope_chunks uses it repeatedly to page through all chunks for delete and prune work.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 124–133)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: This converts one Turbopuffer result row into UFO’s Hit object. A Hit is the project’s standard way to describe a search result chunk and its relevance score.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It restores the chunk digest, copies owner and text fields, converts the ordinal to an integer, attaches the score, and returns a Hit.

**Call relations**: TurbopufferIndex.lexical and TurbopufferIndex.vector both call this after receiving rows from _query. It calls chunk_digest_from_id so the returned Hit carries the same digest format UFO originally wrote.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 136–142)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: This gives a vector search row a score where bigger means better. Turbopuffer may return cosine distance, where smaller is better, so this function turns it into a similarity-style score.

**Data flow**: It receives a result row, its position in the result list, and the total number of rows. If the row has a numeric distance, it returns one minus that distance; otherwise it falls back to a descending rank score based on position.

**Call relations**: TurbopufferIndex.vector calls this for each row returned by _query, then passes the score into hit_from_row. This keeps vector results compatible with the rest of UFO’s result-combining logic.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 156–167)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This writes chunks into Turbopuffer, updating existing documents with the same IDs when needed. It skips chunks without embeddings because this backend stores searchable vector documents.

**Data flow**: It receives a tuple of chunks. It filters out chunks with no embedding, gets authorization headers, splits the remaining chunks into batches, turns each batch into a JSON body with upsert_body, and posts it to the workspace namespace. It returns nothing, but Turbopuffer’s stored index is changed.

**Call relations**: The core index workflow calls this when new or changed chunks must be stored. It relies on _auth for the API key, _path for the namespace URL, and upsert_body for the request shape.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 169–177)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes every indexed chunk belonging to a given owner scope. It is used when a source object should no longer have any memory entries in the index.

**Data flow**: It receives an IndexScope. It gets authorization headers, fetches all chunks in that scope with _scope_chunks, converts their digests to Turbopuffer IDs, sends delete batches to Turbopuffer, and leaves that scope empty in the external index.

**Call relations**: Higher-level cleanup code calls this when an owner’s indexed content must be fully dropped. It uses _scope_chunks to discover what exists, turbopuffer_id to match stored IDs, _auth for credentials, and _path for the target namespace.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 179–189)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes only the old chunks in a scope that are not part of a supplied keep-set. It is useful after re-chunking, when some chunk IDs are still valid and others have become orphaned.

**Data flow**: It receives an IndexScope and a set of chunk digests to keep. It fetches all chunks for that scope, selects the ones whose digests are not in the keep-set, converts those to Turbopuffer IDs, and sends delete batches. The result is that only unwanted chunks are removed.

**Call relations**: Index maintenance code calls this after rewriting an owner’s chunk list. It follows the same support path as delete: _auth for the key, _scope_chunks for current contents, turbopuffer_id for delete IDs, and _path for the namespace.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 191–197)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This checks whether Turbopuffer currently has at least one chunk for a given owner scope. It avoids downloading everything when the caller only needs a yes-or-no answer.

**Data flow**: It receives an IndexScope. It sends a query for the first matching row, scoped by scope_filters; if the namespace is missing, it returns false, otherwise it checks whether any rows came back and returns true or false.

**Call relations**: The index workflow can call this before deciding whether there is stored content to work with. It uses _auth for authorization, _path for the namespace query endpoint, and scope_filters to target the right owner.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 199–208)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs keyword-based search over stored chunk text. It uses BM25, a ranking method that favors chunks containing important query terms.

**Data flow**: It receives query text, allowed subjects, an owner kind, and a result limit. It cleans the query with bm25_query, stops early if there is no useful text or no subjects, asks _query to rank by text BM25, then converts each returned row into a Hit with a rank-based score.

**Call relations**: Search orchestration calls this for the lexical leg of recall. It prepares the query with bm25_query, delegates the HTTP search to _query, and turns rows into project-standard results through hit_from_row.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 210–219)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs meaning-based search using an embedding vector. It finds chunks whose stored embeddings are nearest to the query embedding.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a result limit. If the embedding or subject set is empty it returns no results; otherwise it asks _query to run approximate nearest-neighbor search, scores each row with vector_score, discards non-positive scores, and returns Hits.

**Call relations**: Search orchestration calls this for the vector leg of recall. It delegates the HTTP query to _query, uses vector_score to normalize Turbopuffer’s distance or rank, and uses hit_from_row to return UFO’s standard Hit objects.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 221–234)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: This is the shared helper for both keyword and vector searches. It sends one scoped ranked query to Turbopuffer and returns the raw rows.

**Data flow**: It receives a rank instruction, owner kind, subject set, and limit. It builds a JSON body with ranking, result count, included attributes, and query_filters, posts it to the namespace query endpoint, treats a missing namespace as no results, and returns the rows from the response.

**Call relations**: TurbopufferIndex.lexical and TurbopufferIndex.vector call this instead of building HTTP requests themselves. It uses _auth for credentials, _path for the endpoint, and query_filters to keep results in the requested scope.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 236–264)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: This lists all chunks currently stored for one owner scope. It pages through Turbopuffer results so delete and prune can act on complete scope contents.

**Data flow**: It receives an IndexScope and already-prepared authorization headers. It repeatedly queries Turbopuffer ordered by ID, asking for one page at a time, converts each row into a lightweight Chunk without an embedding, and continues after the last ID until fewer than a full page comes back. It returns the collected chunks.

**Call relations**: TurbopufferIndex.delete calls this to find everything to remove. TurbopufferIndex.prune calls it to compare existing chunks against the keep-set. It uses _path for the endpoint, scope_filters for paging filters, and chunk_digest_from_id to restore UFO-style digests.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 266–268)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: This prepares the HTTP authorization header for Turbopuffer requests. It reads the API key from UFO’s credential store each time instead of baking it into the HTTP client.

**Data flow**: It reads the Turbopuffer API key from the configured credential slot. It returns a dictionary containing an Authorization header in Bearer-token form.

**Call relations**: All methods that send normal Turbopuffer requests call this: upsert, delete, prune, has_chunks, and _query. It is the small gatekeeper that supplies credentials before those methods use _path and the HTTP client.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 270–271)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: This builds the Turbopuffer API path for the current workspace namespace. The namespace keeps one workspace’s indexed chunks separate from another’s.

**Data flow**: It receives an optional suffix such as /query. It combines the fixed namespace prefix, the credential context’s workspace ID, and the suffix, then returns the resulting API path.

**Call relations**: Every method that talks to Turbopuffer calls this to aim requests at the right namespace: upsert, delete, prune, has_chunks, _query, and _scope_chunks.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 274–294)

```
def manifest() -> Manifest
```

**Purpose**: This tells UFO how to load this extension. It declares the needed Turbopuffer API key credential and registers the Turbopuffer index backend factory.

**Data flow**: It takes no input. It returns a Manifest containing the extension name and version, one credential slot for the API key, and an index backend specification that builds a TurbopufferIndex with the current credential access object and an httpx asynchronous HTTP client.

**Call relations**: The extension loading system calls this when discovering available capabilities. The returned manifest is what lets configuration select the turbopuffer backend and lets core code construct TurbopufferIndex at startup.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/runtime/indexing.py`

`domain_logic` · `cross-cutting indexing and retrieval`

This file solves a common search problem: large bodies of text are hard to search well unless they are broken into useful pieces, given stable identities, and sent to a search system in a predictable way. Think of it like cutting a long book into labeled index cards, then asking another service to file those cards and later find the best matches.

The file defines small value objects such as `Chunk`, `Hit`, and `IndexScope`. These are plain records that describe a piece of indexed text, a search result, or the owner whose indexed pieces are being changed. It also defines two promises, called protocols: `IndexBackend` is what a storage/search provider must offer, and `EmbedClient` is what a text-to-vector provider must offer. A vector is a list of numbers that represents the meaning of text for similarity search.

The main workflow is `chunk_embed_upsert`. It takes one body of text, splits it with `TextChunker`, asks an embedding service to turn each piece into vectors, saves the pieces through the index backend, and then removes old pieces that no longer belong after an edit. That last prune step matters: without it, deleted or changed text could still appear in search results as stale information.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the promised operation for adding new chunks or replacing existing chunks in the index. A backend implementation uses it when fresh text has been split and embedded and must become searchable.

**Data flow**: It receives a group of `Chunk` records, each containing text, ownership information, and usually an embedding. The backend stores them, replacing matching existing records when needed. Nothing is returned; the lasting result is that the index now contains those chunks.

**Call relations**: `chunk_embed_upsert` calls this after it has prepared chunks and received embeddings. The protocol itself does not store anything; it tells real backend implementations what method they must provide.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the promised operation for removing all indexed chunks for one owner, such as one memory item or one page. It is used when a whole indexed object should disappear from search.

**Data flow**: It receives an `IndexScope`, which names the kind of owner and the specific owner ID. The backend deletes every chunk in that scope. It returns nothing; the index is changed by removing those records.

**Call relations**: The skill creation extension's manifest indexing flow calls this when it needs to clear an indexed card. This file only defines the contract; an extension or backend supplies the actual deletion behavior.

*Call graph*: called by 1 (_index_card).


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the promised operation for cleaning up old chunks after re-indexing an owner. It keeps only the chunk digests that are still valid and removes the rest, preventing outdated text from appearing in search.

**Data flow**: It receives an `IndexScope` naming the owner and a set of chunk digests to keep. The backend compares what is stored for that owner against the keep-set, deletes anything not in the set, and returns nothing.

**Call relations**: `chunk_embed_upsert` calls this every time it finishes preparing the current desired chunks. This is the final cleanup step after any upsert, and it also handles the empty-body case by pruning everything for that owner.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the promised operation for asking whether an owner already has indexed chunks. A backend can use it to answer whether indexing work has already been done.

**Data flow**: It receives an `IndexScope` that identifies an owner. The backend checks its stored index and returns `true` or `false` depending on whether any chunks exist for that owner.

**Call relations**: No caller is shown in the provided graph, but it is part of the shared indexing contract. Backend implementations provide it so other parts of the system can avoid guessing about index state.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promised operation for normal text search, where words in the query are matched against words in indexed chunks. It is useful for exact terms, names, and phrases.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. The backend searches matching chunks and returns `Hit` records, each describing a matched chunk and its score.

**Call relations**: No caller is shown in the provided graph, but this method is part of the retrieval seam. A real backend supplies the search engine behavior while the core code can rely on this stable shape.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the promised operation for meaning-based search. Instead of matching exact words, it compares an embedding vector against stored chunk vectors to find text with similar meaning.

**Data flow**: It receives an embedding, a set of allowed subjects, an owner kind, and a limit. The backend finds nearby stored vectors and returns `Hit` records with scores and chunk details.

**Call relations**: The queue's shadow skill selection flow calls this after it has an embedding for the query. This protocol method lets that flow ask any compatible backend for semantic matches without knowing how the backend stores vectors.

*Call graph*: called by 1 (_shadow_skill_selection).


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the promised operation for converting text into embeddings, which are lists of numbers that capture the text's meaning for similarity search. Different services or models can implement this same method.

**Data flow**: It receives a tuple of text strings. The embedding provider turns each string into a tuple of floating-point numbers and returns the vectors in the same order as the input texts.

**Call relations**: `chunk_embed_upsert` calls this when indexing text chunks, and the queue's shadow skill selection flow calls it when preparing a search query. The protocol keeps the core code separate from any specific embedding service.

*Call graph*: called by 2 (chunk_embed_upsert, _shadow_skill_selection).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared indexing workflow for one piece of content. It splits the content into chunks, embeds those chunks, saves them, and removes old chunks that no longer match the current content.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner details, a subject, and the body text. First it asks the chunker to create chunks. If there are chunks, it embeds their text and creates updated chunk records that include the embeddings, then sends them to the index. Finally it prunes the owner's stored chunks so only the newly produced chunk digests remain.

**Call relations**: This function is the coordinator between the pure text-splitting logic and the outside indexing services. It calls `EmbedClient.embed`, then `IndexBackend.upsert`, and always calls `IndexBackend.prune` using an `IndexScope` so edits do not leave orphaned search records.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public entry point for turning one text body into labeled `Chunk` records. It gives each piece a stable digest so the index can recognize unchanged chunks across repeated runs.

**Data flow**: It receives raw text plus owner kind, owner ID, and subject. It asks `_slices` to split the text into pieces, then wraps each piece in a `Chunk` with an ordinal number and a digest made from the owner data and text. It returns all chunks as a tuple.

**Call relations**: `chunk_embed_upsert` uses this before embedding and storing content. Internally, it relies on `_slices` for the actual cutting and `_digest` for stable chunk identities.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This is the main text-cutting pipeline inside `TextChunker`. It decides whether text is already small enough, or whether it needs careful splitting, merging, overlap, and final size limits.

**Data flow**: It receives raw text. Blank text becomes an empty list. Short text is only trimmed and checked against the character cap. Longer text is recursively split, merged into useful sizes, given overlap for context, and finally capped by character length. It returns plain text slices.

**Call relations**: `TextChunker.chunk` calls this to get the text pieces it will wrap into `Chunk` records. `_slices` brings together `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars` as one ordered workflow.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates how large a text is in word-like units. It has special handling for Chinese, Japanese, and Korean text, where spaces are not always used between words.

**Data flow**: It receives text and removes whitespace to see how much real content exists. If the text is dense with CJK characters, it counts non-whitespace characters as the size measure. Otherwise it counts runs of non-whitespace text, roughly corresponding to words. It returns an integer count.

**Call relations**: `_slices`, `_recursive_split`, and `_greedy_merge` call this whenever they need to decide whether a piece is too large, small enough, or safe to merge.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This enforces a hard maximum character length for chunks. It is a safety net for cases where a chunk has too many characters even if the word count looks acceptable.

**Data flow**: It receives one text piece. If it fits within the character limit, it returns that piece. If it is too long, it cuts the text into overlapping character windows so no returned piece exceeds the maximum. Empty pieces are skipped.

**Call relations**: `_slices` calls this for short text and again after the main splitting and overlap steps. It is the last guardrail before text becomes an indexable chunk.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks long text into smaller pieces by trying natural boundaries first. It starts with broad breaks like paragraphs, then moves to lines, sentences, punctuation, and finally whitespace.

**Data flow**: It receives text and a delimiter level. At the current level, it tries to split on the configured delimiters. If that does not split the text, it moves to the next level. If a resulting piece is still too large, it recursively splits that piece more finely. It returns a list of smaller text pieces.

**Call relations**: `_slices` calls this when the full text is too large. It calls `_split_at_delimiters` for delimiter-based cutting, `_count_words` to test piece size, and `_split_on_whitespace` as the final fallback.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This cuts text at the earliest matching delimiter from a given set, while keeping the delimiter attached to the piece before it. This helps preserve punctuation and paragraph markers.

**Data flow**: It receives text and a tuple of delimiter strings. It repeatedly finds the next earliest delimiter, adds the text up through that delimiter as one piece, and continues with the remainder. It returns only non-blank pieces.

**Call relations**: `_recursive_split` uses this at each delimiter level. It is the simple cutting tool behind the more strategic recursive splitting process.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural delimiters are no longer available or useful. It groups text by whitespace-separated runs, and for unusual text with no useful spaces it cuts by raw character length.

**Data flow**: It receives text. If it can find word-like runs, it groups them into batches near the target word count and joins each batch back into a piece. If there are no usable words, or one extremely long run, it slices the raw text into fixed-size pieces. It returns non-blank pieces.

**Call relations**: `_recursive_split` calls this only after broader delimiter-based splitting has been exhausted. It ensures the chunker can always make progress, even on poorly formatted or space-free text.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This combines small neighboring pieces into larger, more useful chunks. Without it, splitting on paragraphs or punctuation could create many tiny fragments that are less helpful for search.

**Data flow**: It receives a list of pieces. Starting from the first piece, it keeps appending the next piece as long as the combined text stays under a relaxed size limit. When adding another piece would be too much, it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` and a rounded size limit to decide whether two adjacent pieces belong together.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds a bit of context from the previous chunk to each later chunk. The overlap helps search and embedding understand text that crosses a chunk boundary, like a sentence that depends on the paragraph before it.

**Data flow**: It receives a list of chunks. If there is only one chunk, or overlap is disabled, it returns the list unchanged. Otherwise, each chunk after the first is prefixed with trailing context from the previous chunk. It returns the context-enhanced chunks.

**Call relations**: `_slices` calls this after merging pieces. It uses `_trailing_context` on each neighboring pair, with `itertools.pairwise` supplying those pairs in order.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the exact text to copy from the end of one chunk into the next chunk. It tries to avoid starting the copied context in the middle of an old sentence when it can find a cleaner sentence boundary.

**Data flow**: It receives one chunk of text. If the chunk is too short, it returns no extra context. Otherwise it takes the last configured number of word-like runs. If there is a sentence boundary early enough in that trailing text, it starts after that boundary; otherwise it returns the whole trailing section.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building overlapped chunks. It supplies the small bridge of context that makes adjacent chunks less isolated.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable identifier for a chunk. The identifier changes if the owner, subject, position, or text changes, which lets the index tell old and current chunks apart.

**Data flow**: It receives owner kind, owner ID, subject, ordinal number, and chunk text. It joins those values with a separator, hashes the result with SHA-256, and returns the digest string with a `sha256:` prefix.

**Call relations**: `TextChunker.chunk` calls this once for every text slice it turns into a `Chunk`. Later, `chunk_embed_upsert` uses these digests as the keep-set for pruning stale indexed chunks.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `core/src/ufo/runtime/memory.py`

`data_model` · `cross-cutting`

This file is a small but important boundary between code that wants to recall past information and code that knows where that information is stored. Think of it like a library desk: callers do not need to know which shelf, database, or service holds the memory; they ask through the same desk every time.

The central data shape is a memory result: text to show, what kind of memory it is, and optionally a durable object reference so the full item can be opened later. A timestamp and subject can also travel with the result, so callers can judge how recent and relevant it is without fetching more data.

The `MemorySearchProvider` protocol describes what any memory backend must offer. A protocol is a promise about methods an object must have, rather than a concrete class with its own storage. Providers must support query-based search, browsing recent items for a set of readable subjects, and reporting which memory kinds can be listed.

`MemorySearch` is a thin wrapper around one chosen provider. It does not search by itself; it forwards requests. This keeps the rest of the runtime from depending on a specific memory implementation. Without this file, extensions and consumers would have to agree informally on method names and result formats, which would make memory recall fragile and hard to swap out.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This defines the search method that every memory provider must offer. A caller uses it to ask for memory items that match one or more query strings, limited to what a given source reader is allowed to read.

**Data flow**: It receives search text, a `SourceReader` that represents the caller’s readable sources, and optional start and end times. A real provider implementation uses those inputs to find matching memory items, then returns them as a tuple of `MemoryMatch` results.

**Call relations**: This is the contract that provider implementations fulfill. `MemorySearch.search` calls this method on the selected provider, so consumers can search memory without knowing which backend is doing the work.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This defines how a memory provider should return recent memory items without a search query. It is for browsing the latest readable items, optionally narrowed to certain kinds of memory.

**Data flow**: It receives a set of subjects the caller may read, a maximum number of items, optional memory-kind filters, and an optional cursor. The cursor is a bookmark for continuing a listing safely; the provider returns a `ListingPage` containing memory matches and any next-position information.

**Call relations**: This is the browse-side contract for providers. `MemorySearch.list_recent` forwards listing requests here, letting the selected provider decide how to fetch recent items while callers see one standard interface.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This defines how a provider announces which kinds of memory items can be listed. Callers can use this to offer filters that match what the provider actually supports.

**Data flow**: It takes no input besides the provider itself. A real provider returns a tuple of kind names, such as categories of memory entries it knows how to browse.

**Call relations**: This is called through `MemorySearch.listable_kinds`. It helps consumers build listing choices without guessing or hard-coding provider-specific memory categories.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This forwards a memory search request to the selected provider. It exists so the rest of the runtime talks to `MemorySearch` instead of directly depending on a specific memory backend.

**Data flow**: It receives a reader, query strings, and optional time bounds. It passes those values unchanged to the provider’s `search` method, then returns the provider’s tuple of `MemoryMatch` results.

**Call relations**: A consumer calls this when it wants recall by query. This method immediately hands the work to `MemorySearchProvider.search`, acting as a stable doorway to whichever provider was configured.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This forwards a request to browse recent memory items. It lets callers ask for recent memories through the same wrapper used for search.

**Data flow**: It receives readable subjects, a limit, optional kind filters, and an optional listing cursor. It sends those values to the provider’s `list_recent` method and returns the resulting page of memory matches.

**Call relations**: A caller uses this when it wants recent items rather than query matches. The method delegates to `MemorySearchProvider.list_recent`, keeping paging and storage details inside the provider.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This asks the selected provider which memory kinds it can list. It is useful for showing valid filters or deciding what browsing options are available.

**Data flow**: It takes no extra input. It calls the provider’s `listable_kinds` method and returns the provider’s tuple of supported kind names.

**Call relations**: Consumers call this through `MemorySearch` instead of reaching into the provider directly. The method passes the question to `MemorySearchProvider.listable_kinds` and returns the answer unchanged.


### Embedding provider
The OpenAI embedding extension turns text chunks into vectors that index backends can store and search semantically.

### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration, then active during embedding/indexing work`

This extension is the bridge between the project and OpenAI’s embedding API. An embedding is a list of numbers that represents the meaning of a piece of text, a bit like giving each text a location on a large “meaning map” so similar texts end up near each other. Without this file, the default setup would not know how to create those vectors for memory or search indexes.

The file does three main things. First, it defines fixed settings: the OpenAI model to use, the expected vector size, limits for request size, timeout, and retry count. Second, it prepares text safely before sending it to OpenAI. Very large requests are split into batches, and each individual text is clipped to a maximum length so the provider is not asked to accept an oversized payload. Third, it registers itself as the default embedding backend through a manifest, so the wider system can discover and build it.

A key detail is that the OpenAI API key is not required when the server starts. The key is read from the deploy environment only when an embedding call actually happens. This lets a local development server start without credentials, but if embeddings are requested without a key, the failure is immediate and clear.

#### Function details

##### `plan_embed_batches`  (lines 33–49)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for OpenAI by making sure each request stays within size limits. It clips overly long text items and groups the remaining text into batches that are small enough to send safely.

**Data flow**: It receives a tuple of text strings. For each string, it cuts it down to the maximum allowed item length, then adds it to the current batch until either the item count or total character count would be too large. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send.

**Call relations**: OpenAIEmbedClient.embed calls this before contacting OpenAI. It acts like a packing step before shipping: the embed client gives it all the text, and it returns neatly sized boxes that the API request can carry.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 63–75)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This asynchronous method sends text to OpenAI and returns one embedding vector for each input text. It is the main workhorse that turns human-readable text into searchable numeric form.

**Data flow**: It receives a tuple of text strings. It reads the OpenAI API key from the deploy environment, fails with a clear error if no key is present, builds an OpenAI async client, splits the text into safe batches, sends each batch to the embeddings API, sorts returned rows back into the provider’s reported order, and returns all vectors as tuples of floats. It does not store the vectors itself; it only produces them.

**Call relations**: The embedding backend core calls this when it needs vectors. Inside the method, it asks deploy_env for credentials, uses plan_embed_batches to keep requests within limits, then hands each batch to openai.AsyncOpenAI so the external OpenAI service can create the embeddings.

*Call graph*: calls 1 internal fn (plan_embed_batches); 2 external calls (AsyncOpenAI, deploy_env).


##### `build`  (lines 78–83)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client object that the system will use for the OpenAI backend. It deliberately does not check for an API key at build time, so startup can succeed even when embeddings are not yet needed.

**Data flow**: It receives an ExtensionContext, which represents the workspace or extension-loading context, but this backend does not need to read anything from it. It returns a new OpenAIEmbedClient configured with the default model.

**Call relations**: The manifest points to this function as the factory for the default embedding backend. When the core system resolves that backend at startup, it calls build, which simply constructs and returns OpenAIEmbedClient.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 86–92)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It tells the system the extension name and version, which deploy-time key it may need, and that it provides the default embedding backend.

**Data flow**: It takes no input. It creates an EmbedBackendSpec that names the backend and points to build as the way to construct it, then wraps that in a Manifest along with the extension metadata and required environment key name. The returned Manifest is what the extension loader reads.

**Call relations**: The extension-loading system calls manifest to discover what this file offers. The manifest hands off the backend construction details through EmbedBackendSpec, so later the system can call build when it needs the default embedding client.

*Call graph*: 2 external calls (__init__, __init__).


### Package metadata and explorer surface
Package metadata, shared event constants, and the read-only web explorer round out the memory extension for operators.

### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `cross-cutting`

This is a small package entry file. It does not define any functions or run any logic itself. Its main job is to identify this folder as the home of the memory extension and to document, in one place, the extension’s broad responsibilities.

The memory extension is described as providing durable facts, meaning pieces of information that are meant to survive beyond one short interaction. It can recall those facts when a user submits a prompt, using a `user_prompt_submit` hook. A hook is a planned connection point where the larger system lets an extension run at a specific moment. Here, that moment is when the user sends new text, so the extension can add relevant remembered context.

It also derives memory from page changes through a `page_change` hook. In plain terms, when the visible or active page changes, the extension may notice useful information and turn it into stored memory. Finally, it includes a memory-index job, which likely organizes stored memories so they can be found later, like creating an index at the back of a book.

Without this package marker and summary, the code may still exist elsewhere, but newcomers would have less guidance about the purpose of the memory extension as a whole.


### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension needs to report certain moments in a consistent, machine-readable way. This file defines the shared event name for a memory recall step that happens before a response is produced. Think of it like agreeing on the exact label for a folder: if every part of the system uses the same label, logs and event listeners can find the right information reliably.

It also defines two small safety limits. One caps how many recalled memory IDs should be attached to an event. This prevents an event from becoming too large or noisy if many memories were found. The other caps how much of an error class name should be recorded when recall fails. This keeps event data compact and predictable.

Nothing in this file runs by itself. It is a central reference point used by other memory-extension code when creating or reading structured events. Without it, different parts of the extension might spell the same event differently or include unbounded amounts of detail, making monitoring and debugging harder.


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the small web doorway into the Memory extension’s stored data. Its job is not to change memories, but to show them safely to an operator who has already been tied to a specific workspace. Think of it like a glass display case: it lets someone inspect what is inside the memory store without rearranging it.

At startup, the file looks for a bundled HTML file, `static/memory.html`, and keeps its text ready to serve. The `app_page` function returns that HTML page when the operator opens the surface. If the HTML file is missing, it fails clearly instead of serving a broken page.

The `memories` function powers the page’s data view. It creates an `ExtensionContext`, which is the extension’s way to open its own workspace-scoped database transaction. “Workspace-scoped” means reads are limited to the workspace selected by the operator session, so one workspace cannot accidentally see another workspace’s memories. It then asks the memory store for an inventory of all `memory_item` records in that workspace and returns them as JSON, a common plain-text format used by web pages to receive structured data.

At the bottom, `ROUTES` connects URLs to actions: serve the page, bind the operator session, and return the memory list.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function serves the memory explorer web page to the operator. It exists so the browser can load a self-contained HTML interface before asking for the memory data.

**Data flow**: It receives the surface context and the incoming web request, then checks the already-loaded HTML text. If the HTML page is present, it wraps that text in an HTML web response and sends it back; if the file was not found earlier, it raises an error so the missing page is noticed immediately.

**Call relations**: This function is used by the GET route for the surface’s main path. In the larger flow, the operator opens the memory surface, this function gives the browser the page shell, and that page can then call the separate data endpoint to fetch the actual memories.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns every memory item for the currently bound workspace as JSON. It gives the memory explorer page the data it needs to show what the system can recall from durable storage.

**Data flow**: It receives the surface context, including the workspace identifier, and the incoming request. It builds an extension context with a scoped store for the Memory extension and no declared credential access, then uses that context’s transaction to ask the store inventory for memory records in the current workspace. The returned memory objects are converted into JSON-friendly dictionaries and sent back as a JSON response.

**Call relations**: This function is used by the GET route for `api/memories`, usually after the browser has loaded the page served by `app_page`. It hands database reading off to `ufo_ext_memory.store.inventory`, then turns that result into the web response consumed by the memory explorer.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).
