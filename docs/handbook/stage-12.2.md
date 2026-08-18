# Memory extraction and searchable recall  `stage-12.2`

This stage is shared behind-the-scenes support for long-term memory. Before an agent answers a prompt, it can search past notes and bring back useful facts. After pages change, it can learn from them, clean up what it learned, and keep the search index fresh.

The manifest is the stage’s switchboard. It tells the system which memory tools exist, which automatic hooks run before prompts or after page changes, and which scheduled cleanup jobs should run later. The store is the main filing cabinet and search desk. It saves memories, searches them, indexes page text, and does background indexing so normal writes do not slow down. The condenser is the editor. It extracts clearer facts from raw page changes, merges related facts into summaries, and removes duplicates. The shared core memory model defines the common shape for memory search results, so other code can ask for memories without caring how they are stored. The objects file exposes memories as read-only items that can be opened by id. Events and package files provide common names, limits, and extension identity.

## Files in this stage

### Orchestration and cleanup pipeline
The extension manifest wires memory into agent tools, hooks, and jobs, while the condenser implements fact extraction, consolidation, and deduplication work.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, then active during tool calls, prompt submission hooks, page-change hooks, and scheduled background jobs`

This file is the front door for the memory feature. It tells the host system, “here are the memory tools, here is when to call them, and here are the background chores they need.” The two visible tools are `memory_search`, which lets the agent look up remembered facts and source-document snippets, and `memory_update`, which lets the agent store a durable fact such as a user preference or project detail.

It also adds automatic recall before the model answers a user. When a prompt arrives, the recall hook searches for relevant memories and injects a short “Relevant memory” note into the model’s context. This is deliberately best-effort: if recall is slow or broken, the user’s turn still continues. That is like a helpful assistant checking notes before a meeting, but not blocking the meeting if the notebook is unavailable.

The file also connects memory to source-page changes. One hook indexes changed pages so they can be searched. Another derives durable facts from those pages using a model. Finally, three scheduled jobs keep the memory store healthy: indexing new memory items, combining older related facts into summaries, and collapsing duplicate memories. The `manifest()` function bundles all of these declarations so the extension can be loaded by the platform.

#### Function details

##### `_date_bound`  (lines 158–169)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date string from the search tool into a timezone-aware UTC time boundary. It makes date-only end dates include the whole day, so a user asking through `2026-01-31` does not accidentally miss items from later that day.

**Data flow**: It receives either no value or a text date/date-time plus a flag saying whether this is the end of a range. If there is no value, it returns nothing. If there is text, it parses it, assumes UTC when no timezone is written, and for a date-only end bound moves it to the next midnight. The result is a `datetime` value used to filter memories by creation time.

**Call relations**: The memory search tool calls this before searching. Its output becomes the start and end time window passed into the search service, so malformed dates become ordinary tool errors that the agent can recover from.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 178–256)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs the main memory search workflow used by the tool and by other extensions. It searches both stored memory items and indexed source-page snippets, then merges the results so several focused queries all get a fair chance to contribute.

**Data flow**: It receives up to three query strings, a source reader that describes what subjects the requester may read, and optional start/end dates. It gets the memory store for the current extension context, runs recall searches and source searches in parallel, removes duplicates, and converts both kinds of hits into common `MemoryMatch` objects. It returns a tuple of matches, first remembered items and then source snippets, each with text, kind, reference, date, and subject.

**Call relations**: The search tool builds this service when a user or agent asks to search memory. Inside the service, parallel search calls are gathered together, then the service hands back normalized matches that the tool can render as readable lines.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 258–261)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory item classes can be browsed in listings. This lets callers filter memory by the same set of item classes that the store actually writes.

**Data flow**: It reads the allowed values from the `ItemClass` type definition and returns them as a tuple of strings. Nothing in storage is changed.

**Call relations**: This belongs to the memory search provider interface. Consumers that offer browsing or filtering can ask the service what kinds are valid instead of keeping a separate hard-coded list.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 263–312)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Returns a page of recent live memory items, newest first, without doing a keyword or similarity search. This supports browsing memory like a timeline.

**Data flow**: It receives readable subjects, a page size, optional item-kind filters, and an optional cursor that marks where the previous page ended. It builds a database query for non-superseded memory rows in the current workspace, applies the shared pagination helper, reads rows inside a transaction, and turns each row into a `MemoryMatch`. It returns one listing page with items and paging information.

**Call relations**: This is the browse side of the memory provider. Unlike `search`, it does not include source-page snippets; it only lists actual memory items so a memory browser does not become a raw page dump.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 315–322)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one memory search hit into a short line of text for the agent to read. It includes the snippet, its kind, and, when available, the object reference and date needed to open the full item.

**Data flow**: It receives a `MemoryMatch`. It creates a bullet line containing the kind and text. If the match has a reference, it appends that reference and the creation date if present. It returns the finished string.

**Call relations**: The memory search handler calls this for every returned match before putting the results into a tool response. It is the final presentation step after search has found and merged the matches.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 325–345)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the `memory_search` tool that an agent can call. It turns the tool arguments into a real search, then returns either a readable list of matches or a clear “No matching memory” message.

**Data flow**: It receives the tool context and validated search arguments: queries, optional dates, and a user-facing description. It checks that an extension context is present, parses the date bounds, builds a source reader from the tool context, runs `MemorySearchService.search`, formats each match with `match_line`, and returns a `ToolResult` containing text for the agent.

**Call relations**: The manifest registers this as the handler for the `memory_search` tool. It sits between the agent-facing tool definition and the deeper search service: arguments come in from the tool call, search work is delegated, and formatted text goes back to the agent.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 348–362)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the `memory_update` tool that stores a durable memory item. An agent uses it when it learns something persistent, such as a preference, role, project, or decision.

**Data flow**: It receives the tool context and validated memory-writing arguments. It checks that an extension context exists, chooses the subject from the current effective audience, creates a `MemoryWrite` record with the body, class, kind, confidence, and optional source reference, commits it to the memory store, and returns a confirmation message naming the subject.

**Call relations**: The manifest registers this as the handler for the side-effecting memory write tool. It is the tool-call entry into the storage layer: the agent supplies the fact, and this function hands the structured write to the store.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 365–447)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically looks up relevant memory when a user prompt is submitted and injects a short note into the model’s context. It is intentionally safe: if recall fails or times out, it returns nothing instead of blocking the user’s turn.

**Data flow**: It receives a hook context. It first confirms the event is a user prompt and that there is a turn to attach recall to. For certain internal machine-only root turns, it skips recall and logs that skip. Otherwise it computes the readable subjects, builds a source reader, searches memory using the prompt text under a soft timeout, filters out topic-only recalls, truncates long items, stops before a total text budget is exceeded, logs which memory IDs were actually injected, and returns an `InjectContext` containing the recall text. If there is an error, it logs the error class and returns nothing.

**Call relations**: The manifest attaches this to the `user_prompt_submit` hook. It runs before the model answers, calls into the store for recall, and hands the platform optional context text to place after the submitted message.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 450–459)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that turns committed memory items into searchable index chunks. This is what lets later searches find newly written memories efficiently.

**Data flow**: It receives an extension context. It verifies that both an index backend and an embedding backend are wired; embeddings are numeric representations of text used for similarity search. It then builds a `MemoryIndexer` with the index, embedding service, transaction function, text chunker, and page-state tracking, and runs it. The job updates indexing-related storage and index records.

**Call relations**: The manifest registers this as the `memory_index` scheduled job. Candidate workspace selection is provided separately by `_items_awaiting_index`, and when the scheduler chooses a workspace this function performs the actual indexing pass.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 462–478)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Indexes changed source pages when the platform reports page changes. This makes synced documents searchable as snippets alongside stored memory facts.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. If it is a page-change batch, it requires index and embedding backends, builds a `PageIndexer`, and applies the batch of page changes. The result is updated index chunks and mirror rows for those pages.

**Call relations**: The manifest registers this as one consumer of the `page_change` hook. The core runner owns the change cursor and delivers batches; this function performs the indexing work for each delivered batch and returns no hook output.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 481–492)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Turns changed source pages into durable fact memories. This lets important information from synced pages become recallable as memory, not just searchable as document text.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it returns nothing. For a real page-change batch, it requires a model backend, builds a `FactDeriver` using the memory store and model, and applies it to the changed pages. The deriver writes replacement facts and retires older page-derived facts that were replaced.

**Call relations**: The manifest registers this as another independent `page_change` consumer. It runs alongside page indexing but uses its own cursor path; if no model is available, it fails loudly so the system does not silently skip fact derivation for pages.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 495–503)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that combines older related fact memories into broader semantic summaries. This keeps memory useful by reducing clutter while preserving meaning.

**Data flow**: It receives an extension context. It requires an embedding backend, then builds a `MemoryConsolidator` with embedding access, database transaction access, the workspace ID, and the optional model. Running it finds suitable clusters of older facts, creates summary memories, and marks originals as superseded where appropriate.

**Call relations**: The manifest registers this as the `memory_consolidate` job. `_consolidatable_workspaces` helps the scheduler choose only workspaces that likely have enough aged facts to consolidate, and this function performs the consolidation pass.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 506–514)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that collapses duplicate memory items onto the newest copy. This prevents repeated writes from making recall noisy or misleading.

**Data flow**: It receives an extension context. It requires an embedding backend, builds a `MemoryDeduper` with embedding access, transaction access, workspace ID, and the store, then runs it. The deduper finds duplicate groups and marks older copies as superseded by the newest one.

**Call relations**: The manifest registers this as the `memory_dedup` job. `_dedupable_workspaces` narrows the scheduler’s work to workspaces with likely duplicate backlogs, and this function performs the actual cleanup.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 517–522)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces that have memory items not yet indexed. This keeps the indexing job from running where there is nothing to do.

**Data flow**: It creates a SQL query selecting distinct workspace IDs from memory rows whose embedding digest is missing. It returns the query object; it does not execute it itself.

**Call relations**: The manifest passes this query builder into `owner_candidates` for the memory indexing job. The scheduler uses the resulting candidate list to decide which workspace owners should receive an indexing run.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 525–541)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces that have enough old live facts to be worth consolidating. This avoids spending background job time on workspaces where consolidation cannot produce a useful cluster.

**Data flow**: It computes a cutoff time based on the current UTC time minus the minimum age for consolidation. It then builds a SQL query for workspaces with live, tool-written fact rows older than that cutoff, grouped by workspace, and only keeps groups with at least the required number of facts. It returns the query object without executing it.

**Call relations**: The manifest gives this to `owner_candidates` for the consolidation job. The scheduler uses it before calling `consolidate_memory`, so the heavier consolidation logic only runs for likely candidates.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 544–563)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces with old duplicate-looking memory groups. This helps the deduplication job run only where there is a backlog it can actually clean up.

**Data flow**: It computes a cutoff time based on the current UTC time minus the minimum duplicate age. It builds a SQL query for live, tool-written memory rows older than that cutoff, grouped by workspace, subject, and item class, and keeps groups with at least the required number of copies. It returns distinct workspace candidates as a query object.

**Call relations**: The manifest gives this to `owner_candidates` for the deduplication job. The scheduler uses the query to choose workspaces before invoking `dedup_memory`.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 566–647)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension declaration that the platform loads. It names the memory extension and registers its tools, object type, hooks, scheduled jobs, search provider, and user-facing surface routes.

**Data flow**: It takes no input. It constructs tool definitions for search and update, hook specifications for prompt recall and page changes, job specifications with schedules and candidate selectors, a memory search provider, and a surface specification for the memory UI routes. It returns one `Manifest` object containing all of those pieces.

**Call relations**: This is the file’s main registration point. At extension startup, the platform calls it to discover what the memory extension offers; all later tool calls, hooks, jobs, provider use, and surface routing flow from the objects declared here.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background maintenance`

The memory system can collect many bits of text: synced pages, tool-written notes, repeated statements, and older facts. Without this file, those memories would pile up as raw fragments. Recall would either miss useful facts or return many stale and duplicate versions of the same idea.

The file has three main workers. FactDeriver watches page changes. When a page is still current and has enough text, it asks the language model to pull out durable standalone facts. It writes those facts to the memory store, then retires older facts from the same page revision only after replacements have really been saved.

MemoryConsolidator is a periodic cleanup job for old facts. It groups facts by subject, compares their embeddings (number lists that represent meaning), clusters related ones, asks the model to write one concise summary, saves that as a semantic memory, and marks the originals as superseded.

MemoryDeduper is a cheaper periodic cleanup job. It does not ask the model to rewrite anything. It finds near-identical live memories, keeps the newest copy, and marks the older copies as superseded. Together these workers act like librarians: one indexes new pages into facts, one writes summaries from older notes, and one removes duplicate cards from the catalog.

#### Function details

##### `FactDeriver.apply`  (lines 138–152)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Processes a batch of changed source pages and decides which ones need fact extraction or retirement. It is the public entry point for turning page updates into memory facts.

**Data flow**: It receives page change records. It asks the store which pages are still live, retires facts for pages that disappeared, filters out tombstones and tiny pages, splits the remaining pages into small batches, sends each batch onward for extraction, then retires old page facts only for pages where new facts were successfully written.

**Call relations**: A page-change runner calls this when it has delivered changed pages. This function organizes the batch and delegates real extraction to FactDeriver._derive, using small groups so one model request stays bounded.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 154–192)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> tuple[PageChange, ...]
```

**Purpose**: Extracts and commits facts for pages that are still exactly at the revision being processed. This protects the store from writing facts for a page that changed again while the job was running.

**Data flow**: It receives a small group of page changes. It rechecks the current page state, keeps only pages whose subject and revision still match, asks FactDeriver._extract for candidate facts, validates that each fact points to one of those pages and is notable enough, rechecks the page one more time, then writes each accepted fact as a MemoryWrite. It returns only the pages for which at least one fact landed.

**Call relations**: FactDeriver.apply calls this for each eligible group. It hands the model work to FactDeriver._extract and hands accepted facts to the memory store commit path.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 194–247)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: Asks the language model to read one small group of pages and return structured fact records. It uses a tool-style model call so the model returns fields the code can validate, rather than loose prose.

**Data flow**: It receives current page changes, builds a compact JSON payload containing page ids and truncated page bodies, creates a model request with a forced record_facts tool, waits for the model reply, finds the tool call, reads its facts list, validates each entry, drops invalid entries, and returns the valid extracted facts. If the model does not call the tool or gives no facts list, it raises an error so the caller does not retire anything.

**Call relations**: FactDeriver._derive calls this before writing replacements. It builds ModelRequest, Message, and ToolSchema objects and sends them through the configured model access object.

*Call graph*: called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `cosine`  (lines 250–258)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two embedding vectors are. An embedding is a list of numbers that represents meaning; a high cosine score means two pieces of text are semantically close.

**Data flow**: It receives two number tuples. It computes their dot product and their lengths, returns zero if either vector has no length, otherwise returns the cosine similarity score.

**Call relations**: Both clustering jobs rely on this shared helper. MemoryConsolidator._clusters uses it to find related facts, and MemoryDeduper._clusters uses it to find near-duplicate copies.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 288–297)

```
async def run(self) -> None
```

**Purpose**: Runs one consolidation pass over old facts. It turns clusters of related facts into single semantic summaries when a model is available.

**Data flow**: It starts with no direct input besides the configured workspace and services. If no model is configured, it exits. Otherwise it loads aged facts, groups them by subject, embeds each large enough group, clusters the facts in a worker thread, and consolidates each cluster that has enough members.

**Call relations**: A scheduler calls this periodically. It coordinates MemoryConsolidator._aged_facts, _buckets, _embed, _clusters, and _consolidate, keeping the heavier arithmetic off the async event loop with asyncio.to_thread.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 299–329)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: Finds old fact memories that are eligible to be summarized. It deliberately ignores page-derived facts and already-superseded facts.

**Data flow**: It reads the current time, subtracts the minimum age, queries the memory_item table for live workspace facts old enough to consolidate, limits the scan size, and returns lightweight _AgedFact records containing id, subject, body, confidence, and creation time.

**Call relations**: MemoryConsolidator.run calls this at the start of a pass. The result becomes the raw material that later steps bucket, embed, cluster, and summarize.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 331–340)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: Groups aged facts by subject so only facts about the same subject are considered together. This prevents unrelated memories from being merged just because their wording is similar.

**Data flow**: It receives aged facts, collects them into subject groups, sorts each group newest first, trims each group to a fixed maximum size, and returns subject-plus-facts buckets.

**Call relations**: MemoryConsolidator.run uses this after loading candidates. Each returned bucket is then embedded and clustered independently.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 342–346)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Turns fact text into embedding vectors for comparison. These vectors let the consolidator group facts by meaning rather than exact wording.

**Data flow**: It receives aged facts, truncates each body to a safe size, sends the texts to the embedding service, and returns a dictionary from fact id to its vector.

**Call relations**: MemoryConsolidator.run calls this for each subject bucket before clustering. The vectors are passed to MemoryConsolidator._clusters.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 348–369)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: Builds groups of related facts using embedding similarity. It uses a simple newest-first method: each fact joins the first close-enough cluster, or starts a new one.

**Data flow**: It receives facts and their embeddings. It sorts facts by recency, compares each fact vector to existing cluster heads with cosine, adds it to a close cluster when possible, otherwise creates a singleton cluster, and returns all clusters.

**Call relations**: MemoryConsolidator.run runs this in a worker thread because the repeated number comparisons can be CPU-heavy. It depends on the shared cosine helper.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 371–433)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: Replaces one cluster of old related facts with one new semantic summary. It also safely marks the original facts as superseded.

**Data flow**: It receives a model and a cluster. It asks MemoryConsolidator._summarize for summary text, creates a new summary id, opens a transaction, locks and rereads the original facts, checks they still match what was clustered, inserts the semantic summary, then updates the originals to point to that summary. If the originals changed meanwhile, it quietly stops.

**Call relations**: MemoryConsolidator.run calls this for each large enough cluster. It delegates wording to MemoryConsolidator._summarize and uses database insert and update statements to make the replacement atomically.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 435–445)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: Asks the language model to write one concise statement that captures several related facts. This is the only place the consolidator uses the model to create new text.

**Data flow**: It receives a model and a fact cluster, truncates each fact body, packs the facts into JSON, builds a model request with summary instructions, waits for completion, strips whitespace, limits the result length, and returns the summary string.

**Call relations**: MemoryConsolidator._consolidate calls this before opening its write transaction, so the database is not held open while waiting on the model.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 448–449)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: Provides a stable sorting key for facts based on when they were created. The id is included as a tie-breaker when two facts have the same timestamp.

**Data flow**: It receives an aged fact and returns a pair containing its creation time and id.

**Call relations**: The consolidation bucket and clustering logic use this helper when they need newest-first ordering.


##### `_Group.key`  (lines 469–470)

```
def key(self) -> tuple[str, str]
```

**Purpose**: Returns the identity of a duplicate-check group: its subject and memory item class. This is the value used to walk groups in a stable order.

**Data flow**: It reads the group object's subject and item_class fields and returns them as a tuple.

**Call relations**: MemoryDeduper.run uses this key to compare groups with the saved cursor and decide which group should be swept next.


##### `_Group.fingerprint`  (lines 473–474)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: Summarizes whether a group has changed since the last deduplication sweep. It records the live copy count and the latest update time.

**Data flow**: It reads the group's copy count and latest timestamp, converts the timestamp to text, and returns both values in a small JSON-friendly list.

**Call relations**: MemoryDeduper.run stores and compares this fingerprint in the scoped store. If the fingerprint is unchanged, the deduper can skip expensive embedding work.


##### `MemoryDeduper.run`  (lines 514–526)

```
async def run(self) -> None
```

**Purpose**: Runs one deduplication tick. It chooses one eligible group of live tool-written memories, skips it if unchanged, or collapses near-duplicates inside it.

**Data flow**: It loads duplicate candidate groups, reads the saved cursor, chooses the next group after that cursor with wraparound, saves the new cursor, checks the group's fingerprint, and either returns early or deduplicates the group and records the new fingerprint.

**Call relations**: A scheduler calls this periodically. It coordinates MemoryDeduper._groups, _cursor, and _dedup_group, while using the scoped store as its memory of where the sweep left off.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 528–540)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: Interprets the saved deduplication cursor. The cursor tells the next tick which group was visited last.

**Data flow**: It receives a stored JSON value. If there is no value, it returns an empty starting point. If the value is a two-string list, it returns it as a tuple. Anything else is treated as corruption and raises an error.

**Call relations**: MemoryDeduper.run calls this before selecting the next group. This keeps the group walk predictable and makes bad stored state visible instead of silently restarting.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 542–566)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: Finds groups that have enough live, old-enough tool-written rows to possibly contain duplicates. A group is defined by subject and item class.

**Data flow**: It queries the memory table for live rows in the workspace that were not created from pages, are not superseded, and are older than the safety delay. It groups them by subject and item class, keeps only groups with at least the minimum duplicate count, and returns _Group objects with counts and latest update times.

**Call relations**: MemoryDeduper.run calls this at the start of each tick. The returned list supplies both the cursor ordering and the fingerprints used to skip unchanged groups.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 568–573)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: Performs the actual duplicate cleanup inside one chosen group. It embeds the live copies, clusters near-matches, and collapses each duplicate cluster.

**Data flow**: It receives a group, loads its live copies, embeds their bodies, clusters them in a worker thread, and for every cluster with enough members calls MemoryDeduper._collapse to mark older copies as superseded.

**Call relations**: MemoryDeduper.run calls this only when a group's fingerprint says work is needed. It delegates reading to _live_copies, vector creation to _embed, comparison to _clusters, and database updates to _collapse.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 575–590)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: Loads the live memory rows for one duplicate candidate group. Rows are newest first, because the newest copy will win if duplicates are found.

**Data flow**: It receives a group, builds the shared live-group filters, queries ids and bodies from the memory table, orders by creation time and id descending, limits the result size, and returns _LiveCopy records.

**Call relations**: MemoryDeduper._dedup_group calls this before embedding. It uses MemoryDeduper._live_group so the read matches the same eligibility rules later used during collapse.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 592–599)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Creates embeddings for the copies being checked for duplication. It batches requests so the embedding service is not asked to process an unbounded amount at once.

**Data flow**: It receives live copies, splits them into fixed-size batches, truncates each body, sends each batch to the embedding client, and returns a dictionary from copy id to vector.

**Call relations**: MemoryDeduper._dedup_group calls this after loading copies. Its output is passed to MemoryDeduper._clusters.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 601–630)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: Groups near-duplicate copies by comparing their embeddings. Because copies are read newest first, the first item in each duplicate cluster is the one that will be kept.

**Data flow**: It receives copies and their vectors. For each copy, it compares the copy's vector with the head of each existing cluster using cosine similarity. If the score is high enough, it joins that cluster; otherwise it starts a new one. It returns all clusters.

**Call relations**: MemoryDeduper._dedup_group runs this in a worker thread to avoid blocking the async loop. It uses the shared cosine helper for each similarity check.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 632–660)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: Marks all but the newest copy in a duplicate cluster as superseded by the newest copy. It is careful not to create broken links if another process changed the same rows.

**Data flow**: It receives the group and a duplicate cluster. It treats the first copy as the head, opens a transaction, locks and rereads the cluster rows using the live-group filters, checks that the bodies still match what was embedded, and updates only the donor rows to point to the head. If anything changed, it stops without modifying them.

**Call relations**: MemoryDeduper._dedup_group calls this for each duplicate cluster. It uses MemoryDeduper._live_group for both the lock query and the update so it only touches rows that are still live and eligible.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 662–670)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: Builds the common database conditions for rows that count as live deduplication candidates in a specific group.

**Data flow**: It receives a group and returns filter expressions requiring the right workspace, subject, item class, no page origin, no superseded marker, and creation time older than the deduplication safety delay.

**Call relations**: MemoryDeduper._live_copies uses these filters when reading candidates, and MemoryDeduper._collapse uses the same filters when locking and updating them. This keeps the read and write rules aligned.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


### Shared recall contracts
These files define the common memory API, package identity, and event vocabulary used by the memory extension and the rest of the system.

### `core/src/ufo/memory.py`

`data_model` · `cross-cutting`

This file is a small but important meeting point between memory providers and memory consumers. A memory provider is any extension that can remember things and later find them again. A consumer is any part of the system that wants to search or browse those remembered things.

The file defines `MemoryMatch`, the common shape of one search result. It includes the result text, its kind, an optional durable object reference that can be opened later, an optional creation time for sorting or judging recency, and an optional subject. In plain terms, it is the “search result card” everyone agrees to use.

It also defines `MemorySearchProvider`, a protocol. A protocol is like a promise: any provider that has these methods can be used here, even if it is implemented elsewhere. Providers must support direct search, recent-item browsing, and reporting which item kinds can be listed.

Finally, `MemorySearch` is a thin wrapper around one chosen provider. It does not search by itself. Instead, it forwards requests to the selected provider. This keeps the rest of the code from depending on provider-specific details. Without this file, every memory extension could return different result shapes and require different calling code, making recall hard to plug in or swap out.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This defines the promise that a memory provider can search remembered material for one or more query strings. It also accepts a reader that describes what sources are allowed to be read, plus optional start and end times to narrow the search window.

**Data flow**: The caller supplies search queries, a source reader, and optional time bounds. A concrete provider implementation uses those inputs to look through its stored memory and returns a tuple of `MemoryMatch` results in the shared format. This protocol method itself has no body; it only states what real providers must provide.

**Call relations**: Code that wants recall can call this through `MemorySearch.search`. The wrapper passes the request on to whichever provider was selected, and that provider supplies the actual matching results.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This defines the promise that a memory provider can list recent memory items a given set of subjects is allowed to read. It is for browsing recent items, not searching by text similarity.

**Data flow**: The caller gives a set of readable subjects, a maximum number of items, optional item kinds to include, and an optional cursor. The provider uses those inputs to fetch the next page of recent memory matches and returns a `ListingPage`, which contains the results and paging information. The cursor is a bookmark-style position, used so new items arriving during reading do not cause rows to be skipped or repeated.

**Call relations**: Code that wants to browse memory can call this through `MemorySearch.list_recent`. The wrapper sends the request to the selected provider, which decides how to read and page through its stored items.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This defines the promise that a memory provider can say which kinds of memory items it can show in recent-item listings. Consumers use this to build safe filters instead of guessing provider-specific categories.

**Data flow**: There are no inputs besides the provider itself. A concrete provider returns a tuple of kind names, such as the categories of items it stores and can list. This protocol method only describes the required behavior.

**Call relations**: Code that wants to offer filtering can call this through `MemorySearch.listable_kinds`. The wrapper asks the selected provider for its supported kinds and returns them unchanged.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the public search doorway for a selected memory provider. It lets callers ask for remembered information without caring which provider is behind it.

**Data flow**: The caller gives a source reader, query strings, and optional start and end times. `MemorySearch` forwards all of that directly to its provider’s `search` method. The provider’s tuple of `MemoryMatch` results comes back unchanged to the caller.

**Call relations**: This method sits between memory consumers and the provider implementation. When a consumer asks `MemorySearch` to search, this method hands the work to `MemorySearchProvider.search` on the configured provider.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This is the public browsing doorway for recent memory items from a selected provider. It gives callers one consistent way to page through recent memories by subject and optional kind.

**Data flow**: The caller supplies readable subjects, a limit, optional kind filters, and an optional listing cursor. `MemorySearch` passes these values straight to the provider’s `list_recent` method. The resulting `ListingPage` of `MemoryMatch` objects is returned unchanged.

**Call relations**: This method connects browsing consumers to the chosen provider. It does not decide what is recent or visible; it delegates that decision to `MemorySearchProvider.list_recent`.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This exposes the selected provider’s list of memory item kinds. Callers use it to know which filters make sense for recent-memory browsing.

**Data flow**: The caller provides no extra data. `MemorySearch` asks its provider for the tuple of listable kind names and returns that tuple as-is.

**Call relations**: This method is the small forwarding step between consumers that need filter choices and the provider’s `MemorySearchProvider.listable_kinds` implementation.


### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `cross-cutting`

This is the front door for the memory extension package. It does not contain working code itself, but its short module note explains the extension’s role in the larger system. The memory extension is about keeping useful information beyond a single interaction, then bringing it back at the right time. In everyday terms, it is like a notebook the system can write facts into, search later, and update as pages or context change. The note names three important moments when this extension matters: when a user submits a prompt, when a page changes, and when a background job builds or refreshes the memory index. A “hook” means a planned connection point where the main application lets an extension run code at a specific event. So a user_prompt_submit hook would let memory recall happen just before or during prompt processing, while a page_change hook would let the extension derive or update memory from changed page content. The memory-index job likely prepares stored facts so they can be searched efficiently later. Without this package marker, Python would not treat this directory as an importable package in the usual way, and the rest of the extension’s files would not have this clear package-level description.


### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

The memory extension appears to emit a structured event when it recalls saved memories before preparing a response. This file is the small shared reference point for that event. Instead of writing the event name as a loose text string in many places, the project defines `MEMORY_RECALL_EVENT` here. That reduces mistakes such as typos, and it makes it easier to rename or find the event later.

The file also sets two safety limits. `MAX_RECALLED_MEMORY_IDS` caps how many recalled memory identifiers should be included with the event, so the event stays small and readable. `MAX_RECALL_ERROR_CLASS_CHARS` caps the length of an error class name, so unusually long error text cannot bloat or distort the event data. In everyday terms, this file is like a label sheet and size guide for one kind of package the memory extension sends to the rest of the system.

Without this file, different parts of the extension might use slightly different event names or include too much detail in event payloads, making logs and monitoring harder to trust.


### Memory access and storage
The read-only memory object surface exposes stored memories to callers, backed by the store that records, indexes, and searches memory content.

### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the doorway between the general object system and the memory extension’s stored memory rows. A memory item is like a note card the system has saved: it has text, a visibility audience, a type, confidence, and sometimes a link back to the page it was distilled from. Search can return a reference to one of these notes; this object type is what opens that reference and shows the full contents.

The important job here is safety and correctness. A caller should only see memories that belong to the subjects, or visibility audiences, they are allowed to read. If a memory came from a page, this file also checks that the source page is still readable at the same revision. That prevents a shared memory from staying visible after its source page is no longer valid for the reader.

Listing shows only live memories, meaning memories that have not been replaced by a newer one. Reading a specific id can still open an older replaced memory, because stale references may exist. In that case the detail includes a `superseded_by` link so the caller can follow it to the newer memory.

Writes are deliberately blocked here. New or changed memories must go through `memory_update`, and old memories are ended by being superseded rather than deleted.

#### Function details

##### `_require_ext`  (lines 60–63)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the memory object was given its extension context, which is the object that knows how to reach the memory store. Without it, reads would have no database/workspace context and could silently behave incorrectly.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it stops the operation by raising an error that says memory objects were dispatched without their required context.

**Call relations**: The public read paths call this first: `MemoryObjects.list`, `MemoryObjects.get`, `MemoryObjects.member_page`, and `MemoryObjects.member_detail`. It acts like checking you have the right key before trying to open the filing cabinet.

*Call graph*: called by 4 (get, list, member_detail, member_page).


##### `_row`  (lines 66–71)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str) -> ObjectRow
```

**Purpose**: This helper turns a full memory item into a compact row suitable for lists. It keeps the id, a short preview of the body, and the main filter fields a user might scan or filter by.

**Data flow**: It receives a memory name, full body text, subject, item class, and memory kind. It trims the body to a short summary and packages those values into an `ObjectRow`, which is the object system’s standard list-row shape.

**Call relations**: `MemoryObjects._page` uses it to build each row in a list page. `MemoryObjects.member_detail` also uses it to pair a detail view with the same kind of row that would appear in a member-facing list.

*Call graph*: called by 2 (_page, member_detail); 1 external calls (__init__).


##### `_member_reader`  (lines 74–82)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: This builds the reading identity used when a signed-in member views memories outside an active conversation turn. It answers the question: “whose eyes are we reading through?”

**Data flow**: It receives a member id. It looks up the current agent, treats the given member as the requester, computes the conversation audience for that member, turns that audience into readable subjects, and returns a `SourceReader` containing all of that.

**Call relations**: `MemoryObjects.member_page` and `MemoryObjects.member_detail` use this when the portal needs to show a member’s memory view. It hands that reader identity to the same lower-level read functions used by normal tool calls, so portal reads follow the same visibility rules.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 91–92)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the normal object-system list operation for memories during a tool call. It returns a page of live memories visible to the caller’s current reading audience.

**Data flow**: It receives a tool context and a list query. It pulls the extension context and the caller’s `SourceReader` from the tool context, then passes both to `_page`; the result is an `ObjectPage` containing matching memory rows.

**Call relations**: The object framework calls this when someone lists memory objects. It does only the doorway work, then delegates the real database lookup and visibility filtering to `MemoryObjects._page`.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 94–107)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists live memories for a signed-in member outside a turn, such as in a portal or member-facing view. It uses the member’s own readable subjects rather than letting an admin or another user see private memory by default.

**Data flow**: It receives an optional extension context, a member id, an admin flag, and a list query. It requires the extension context, builds a member-specific reader with `_member_reader`, and asks `_page` to produce the visible list. The admin flag is accepted but does not expand memory visibility here.

**Call relations**: Portal-style code calls this when it needs a page of memory rows for a member. It shares the same `_page` machinery as `MemoryObjects.list`, which keeps member-page reads and in-turn reads consistent.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 109–127)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: This opens one memory item for a signed-in member outside a turn. It returns both the compact row and the full detail when the member is allowed to see the item.

**Data flow**: It receives an optional extension context, a memory id string, a member id, and an admin flag. It builds the member’s reader, asks `_item` for the full memory detail, and returns `None` if nothing visible is found. If a detail exists, it creates a matching row from the detail’s text and fields, then wraps row and detail together in a `MemberObject`.

**Call relations**: Member-facing views call this when opening a specific memory. It relies on `_item` for the careful id parsing, database read, source-page visibility check, and link building; it adds only the member-view wrapper around that result.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 1 external calls (__init__).


##### `MemoryObjects.get`  (lines 129–130)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This is the normal object-system read operation for a single memory id during a tool call. It returns the full memory detail if the caller is allowed to see that memory.

**Data flow**: It receives a tool context and the memory name, which should be a UUID string. It extracts the extension context and current reader from the tool context, then passes them to `_item`. The output is an `ObjectDetail` with the memory’s contents and links, or `None` if the id is invalid or not visible.

**Call relations**: The object framework calls this when a memory reference is opened. It delegates the real work to `MemoryObjects._item`, so all single-item reads use the same permission and source-page checks.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 132–184)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the main list-building routine. It finds live memory rows in the database, removes any whose source page is no longer readable in the right way, and formats the result as an object page.

**Data flow**: It receives the extension context, a reader identity, and a list query. It reads the reader’s allowed subjects, queries the `memory_item` table for non-superseded memories in the current workspace and those subjects, ordered newest first and capped at a fixed maximum. For memories derived from pages, it asks the extension context for the currently readable state of those pages, then keeps only rows whose source page still matches the memory’s subject and revision. Finally it converts the surviving rows into compact rows and applies the requested paging/filtering through `object_page`.

**Call relations**: `MemoryObjects.list` and `MemoryObjects.member_page` both hand off to this function. It in turn uses the extension transaction for the database read, `readable_page_states` for source-page safety, `_row` for formatting, and `object_page` for the object system’s standard page result.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 186–252)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This is the main single-memory reader. It opens a memory by id, checks that the reader may see it, and returns the full stored text plus useful links to its source or replacement.

**Data flow**: It receives the extension context, a reader identity, and a name string. First it tries to parse the name as a UUID; if that fails, there is no valid memory to read. It queries the `memory_item` table for that id in the current workspace and in one of the reader’s allowed subjects. If the memory came from a page, it also checks that the source page is still readable, has the same subject, and is still at the recorded revision. Then it builds links: `created_from` points to the source page, and `superseded_by` points to a replacement memory if one exists. The result is an `ObjectDetail` containing a `MemorySpec`, timestamps, and links, or `None` if any check fails.

**Call relations**: `MemoryObjects.get` uses this for normal object reads, and `MemoryObjects.member_detail` uses it for member portal reads. It calls into the database through the extension transaction, asks `readable_page_states` for source-page permission checks, and constructs the object-system detail objects that callers receive.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 254–261)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no editable status for memory objects. Since memories are not updated through the generic object apply flow, there is no generation or pending state to return here.

**Data flow**: It receives the tool context, memory name, and an optional expected generation value. It ignores them and returns `None`, meaning there is no status information available through this object handler.

**Call relations**: The object framework may ask for status as part of its generic object protocol. This implementation intentionally does not hand off to anything else, matching the file’s rule that memory writes happen through `memory_update`, not through normal object editing.


##### `MemoryObjects.apply`  (lines 263–272)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This blocks attempts to create or change a memory through the generic object apply operation. It protects the design rule that memories are recorded only through the dedicated `memory_update` path.

**Data flow**: It receives the tool context, target name, proposed memory spec, optional old spec, and optional expected generation. Instead of writing anything, it raises `VerbNotSupported` with a message explaining that memories are not applied this way.

**Call relations**: The object framework may call this when someone tries to apply a new object state. This function stops that route immediately and does not call the database, preserving `memory_update` as the single write path.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 274–281)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This blocks attempts to delete a memory through the generic object delete operation. Memories are ended by being superseded during consolidation, not by being removed directly.

**Data flow**: It receives the tool context, target name, and optional expected generation. It does not look up or change the memory. It raises `VerbNotSupported` with a message explaining that memories cannot be deleted and that superseding is the supported way they stop being used.

**Call relations**: The object framework may call this when someone requests deletion. This function refuses the request immediately, matching the rest of the file’s read-only behavior and avoiding any cleanup path that the memory index does not support.

*Call graph*: 1 external calls (__init__).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file gives the project a durable “memory” layer. A memory can be a fact, preference, decision, event, task, or broader topic, and it may come from a direct tool write or from a synced source page. The important design choice is that saving a memory does not immediately split it into search chunks or create embeddings, which are numeric fingerprints used for meaning-based search. Instead, writes simply store a row and mark it as needing indexing. A background indexer later claims due rows, checks that page-derived memories still match the current page revision, and then publishes searchable chunks.

Recall works like asking a librarian for the most useful notes. It searches in two ways: lexical search, which matches words, and vector search, which matches meaning. It blends those rankings, reads the real memory rows back from the database, checks permissions for source-derived memories, applies time decay for facts, removes near-duplicates, keeps a mix of memory types, and turns episodic memories into topic pointers rather than injecting full text.

The file also indexes source pages themselves. It mirrors current page state, removes stale page chunks, and makes facts from old page revisions due for re-checking. Without this file, memories could be saved but not safely recalled, stale source material could leak into results, and indexing work could block normal writes.

#### Function details

##### `recall_subjects`  (lines 151–152)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience into the set of subjects that memory recall is allowed to search. A subject is the visibility label used to keep one audience’s memories separate from another’s.

**Data flow**: It receives an audience object → asks the shared audience helper to expand it into subject strings → returns those subjects as a frozen set.

**Call relations**: Recall setup uses this as the small bridge between audience rules and the memory store’s subject filter. It delegates the actual audience logic to the shared SDK helper rather than duplicating it here.

*Call graph*: 1 external calls (audience_subjects).


##### `_granted_link`  (lines 155–163)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds the database permission test for source-derived memories. A memory learned from source pages is readable if the reader has access to at least one source that contributed to it.

**Data flow**: It receives a set of readable source IDs → creates a database EXISTS condition against the memory-source link table → returns that condition so a larger query can include it.

**Call relations**: MemoryStore._enrich uses it when reading recalled memories, and MemoryStore._untail_leg uses it for newly written but not-yet-indexed memories. It is the shared guardrail that stops source-derived facts from being shown to readers without a matching source grant.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 203–272)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Lists the newest stored memories in a workspace for an operator or explorer view. It shows not only the text, but also where each memory came from and how recall would currently weight it.

**Data flow**: It receives a transaction opener and workspace ID → reads recent memory rows and their source links from the database → calculates age, half-life, and decay weight → returns MemoryInventoryItem objects.

**Call relations**: This is separate from recall because it is an inspection tool, not a query search. It uses _aware, half_life_days, and decay_multiplier so the operator view reports the same time-decay math that recall uses.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 275–276)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has timezone information. The code treats missing timezone data as UTC, the shared project time standard.

**Data flow**: It receives a datetime → if it already has timezone information it leaves it alone, otherwise it marks it as UTC → returns the timezone-aware datetime.

**Call relations**: Inventory, recall enrichment, source search, and decay calculation all use this before comparing times. It prevents subtle age calculations from being wrong because one timestamp is timezone-aware and another is not.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.page_origin_is_complete`  (lines 299–307)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Checks that page-derived memory writes include all required page-origin fields. A memory cannot safely point to a page unless it has the page ID, revision, and source ID together.

**Data flow**: It reads the fields on a MemoryWrite object → checks whether only some page-origin fields were provided → either returns the object unchanged or raises a validation error.

**Call relations**: This runs during MemoryWrite validation before MemoryStore.commit stores anything. It protects later indexing and permission checks, which rely on page-derived rows having a complete origin.


##### `_fuse`  (lines 335–358)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines multiple search result lists into one score per owning row. It lets word-based and meaning-based search contribute to the same final candidate list.

**Data flow**: It receives search legs made of chunk hits → ranks chunks inside each leg, sums reciprocal-rank scores, remembers the best snippet per owner, and records the best vector score → returns a map from owner ID to combined signals.

**Call relations**: fuse_hits and fuse_recall both use this as their shared ranking core. It stays generic so page search and memory recall can blend search legs in slightly different ways without duplicating the mechanics.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 361–366)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page matches by combining lexical and vector hits. It is used when searching source pages rather than recalling stored memory facts.

**Data flow**: It receives word-search hits, meaning-search hits, and a limit → calls _fuse → sorts by the fused rank score → returns Fused results with owner IDs, scores, and snippets.

**Call relations**: MemoryStore.search_sources calls this after fetching both search legs. It hands back a compact ranked list that search_sources can check against the current page mirror and reader permissions.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 369–386)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending fused search rank with semantic closeness. It also includes a special tail leg so brand-new unindexed memories can still be found by words.

**Data flow**: It receives lexical hits, vector hits, tail hits, and a limit → calls _fuse → normalizes the rank score, mixes it with the raw vector score, sorts results → returns Fused memory candidates.

**Call relations**: MemoryStore.recall calls this after gathering all candidate legs. Its output is not final yet; recall then reads real rows back, applies permission checks, decay, duplicate removal, and type diversity.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 404–410)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Returns how quickly a fact should lose recall strength with age. Non-fact memory classes do not decay here.

**Data flow**: It receives an item class and memory kind → if the item is a fact, looks up the configured half-life for that kind → returns a number of days or None.

**Call relations**: decay_multiplier uses this for ranking, and inventory uses it for display. Keeping this rule in one place makes the explorer and recall agree.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 413–425)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates the time-and-confidence weight applied to fact recall scores. Older or lower-confidence facts count less, while non-facts keep their full relevance score.

**Data flow**: It receives class, kind, confidence, source time, and current time → finds the half-life, computes age in days, and applies the decay formula → returns a multiplier such as 1.0 or a smaller value.

**Call relations**: decay_factor wraps this for recalled items, and inventory calls it to show live recall weight. It relies on _aware so date comparisons are consistent.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 428–431)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Gets the decay multiplier for one recalled memory item. It chooses the best timestamp to age from: the memory’s as-of time when present, otherwise its created time.

**Data flow**: It receives a Recalled item and current time → extracts class, kind, confidence, and timestamp → passes them to decay_multiplier → returns the score multiplier.

**Call relations**: MemoryStore._shortlist uses this while finalizing recall results. It is the small adapter between the Recalled data shape and the shared decay math.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (_shortlist).


##### `_body_shingles`  (lines 439–441)

```
def _body_shingles(body: str) -> frozenset[str]
```

**Purpose**: Turns memory text into small three-word fingerprints used for duplicate detection. This helps spot two bodies that say almost the same thing without needing machine learning.

**Data flow**: It receives body text → lowercases it, splits it into words, builds overlapping three-word phrases → returns those phrases as a set.

**Call relations**: drop_near_duplicates calls this for each candidate it considers. It is the cheap text-comparison building block for protecting recall slots from repeated facts.

*Call graph*: called by 1 (drop_near_duplicates); 1 external calls (split).


##### `drop_near_duplicates`  (lines 444–470)

```
def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]
```

**Purpose**: Removes recall candidates that are very similar to an already-kept item. This keeps the assistant from injecting the same fact several times in slightly different wording.

**Data flow**: It receives ranked recalled items and a keep count → builds word-shingle sets for candidate bodies → skips candidates whose overlap is too high with kept items → returns distinct-looking items.

**Call relations**: MemoryStore._shortlist calls this after decay ranking and before type diversity. It uses _body_shingles and stops once it has enough usable items, so it does not spend work on candidates that cannot fit.

*Call graph*: calls 1 internal fn (_body_shingles); called by 1 (_shortlist).


##### `enforce_type_diversity`  (lines 473–491)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from filling all recall slots. For example, facts should not crowd out every episodic or semantic result when the caller asked for a limited set.

**Data flow**: It receives ranked rows and a limit → keeps rows in order while capping each item class → backfills from overflow if there is still room → returns at most the requested number.

**Call relations**: MemoryStore._shortlist uses this as the last filtering step. It preserves ranking as much as possible while making the final recall set more balanced.

*Call graph*: called by 1 (_shortlist).


##### `as_topic_pointer`  (lines 494–504)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Turns episodic memory hits into short topic pointers instead of full recalled text. Episodic memories act like breadcrumbs to browse, not direct context to inject.

**Data flow**: It receives a recalled item and its position → if the item is episodic, replaces the body with a topic label and marks recall_mode as topic; otherwise leaves it unchanged → returns the item.

**Call relations**: MemoryStore.recall applies this to the final shortlist. It is intentionally after ranking, so episodic memories can be discovered but are presented safely.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 531–645)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Saves one memory to the database without doing expensive indexing work. It uses a content-based ID so the exact same memory body for the same workspace, subject, and class updates one row instead of creating duplicates.

**Data flow**: It receives a MemoryWrite → computes the memory ID → inserts or updates the memory row, clears indexing state when the page binding changed, revives retired rows when restated, and records a source-page link when present → changes the database and returns nothing.

**Call relations**: This is the write path callers use to add memory. It deliberately leaves embeddings to MemoryIndexer, while supersede_page_facts and the deduper can later retire or rebind page-derived memories.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 647–763)

```
async def supersede_page_facts(self, page_id: UUID, revision: int | None) -> None
```

**Purpose**: Retires memory-source links for facts that an old page revision produced. If a memory was learned from several pages, it removes only the stale page’s link and keeps the memory alive through the remaining links.

**Data flow**: It receives a page ID and optional current revision → finds stale links, locks affected memory rows, deletes stale links, re-points rows to surviving links when possible, deletes rows with no links left, and removes their index chunks → updates the database and index.

**Call relations**: The page fact derivation flow calls this when a page has changed or gone away. It cooperates with MemoryStore.commit, which creates links, and with the index backend, which must forget memory rows that no longer exist.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 765–804)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Finds the best memories for a query and reader. It combines search results, applies permissions and freshness checks, ranks by relevance and decay, removes duplicates, and returns the final recalled items.

**Data flow**: It receives query text, allowed subjects, a limit, optional time window, and a source reader → gets readable source IDs, runs indexed search legs plus the unindexed tail, fuses candidates, enriches them from database rows, shortlists them in a worker thread, and rewrites episodic hits as topic pointers → returns Recalled items.

**Call relations**: This is the main read path for memory recall. It coordinates _source_ids, _legs, _untail_leg, fuse_recall, _enrich, _shortlist, and as_topic_pointer into one end-to-end recall pipeline.

*Call graph*: calls 6 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, fuse_recall); 2 external calls (to_thread, now).


##### `MemoryStore._shortlist`  (lines 806–821)

```
def _shortlist(self, enriched: tuple[Recalled, ...], limit: int, now: datetime) -> tuple[Recalled, ...]
```

**Purpose**: Turns an enriched candidate pool into the final limited recall list. It applies time decay, duplicate protection, and type diversity.

**Data flow**: It receives recalled candidates, a limit, and current time → multiplies each score by its decay factor, sorts candidates, drops near-duplicates, enforces class diversity → returns the final shortlist.

**Call relations**: MemoryStore.recall runs this in a worker thread because it is CPU work rather than database or network waiting. It ties together decay_factor, drop_near_duplicates, and enforce_type_diversity.

*Call graph*: calls 3 internal fn (decay_factor, drop_near_duplicates, enforce_type_diversity); 1 external calls (replace).


##### `MemoryStore.search_sources`  (lines 823–889)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages directly instead of stored memory facts. It returns snippets from page chunks only when the page is still current and readable.

**Data flow**: It receives query text, subjects, limit, optional time window, and a source reader → runs lexical and vector page search, fuses hits, reads matching page mirror rows, verifies readable current page states, and builds SourceMatch results → returns matches up to the limit.

**Call relations**: This is the source-page counterpart to MemoryStore.recall. It uses _legs for searching, fuse_hits for ranking, and _readable_states to confirm the reader may still see each page.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 891–897)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Asks the permission system which source IDs the reader can access. Source-derived memory recall cannot be safely filtered without this information.

**Data flow**: It receives a source reader → checks that a readable-source provider is wired in → asks it for the reader’s source IDs → returns the frozen set of IDs or raises an error if unavailable.

**Call relations**: MemoryStore.recall calls this before any database read that might include source-derived memory. The returned IDs feed _untail_leg and _enrich through the grant fence.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 899–911)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main index searches for a query: word matching and meaning matching. If embedding the query fails, it still returns lexical results.

**Data flow**: It receives query text, subjects, owner kind, and limit → embeds the query when possible, asks the index for lexical hits, asks for vector hits only when an embedding exists → returns both hit lists.

**Call relations**: MemoryStore.recall uses this for memory items, and MemoryStore.search_sources uses it for pages. It delegates query embedding to _embed_query so failures are contained.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 913–968)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Searches newly saved memories that have not yet been indexed. This makes a just-committed fact recallable before the background indexer has processed it.

**Data flow**: It receives query text, subjects, limit, and readable source IDs → splits the query into terms, scans a bounded set of newest unindexed eligible memory rows, counts term matches in each body, and builds Hit objects → returns the best tail hits.

**Call relations**: MemoryStore.recall adds this as a third search leg before fuse_recall. It uses _granted_link so unindexed source-derived rows obey the same source permissions as indexed rows.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 970–978)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Creates a meaning vector for a recall or search query. If the embedding service fails, it logs the problem and lets recall continue with word search only.

**Data flow**: It receives query text → returns an empty tuple for blank text, otherwise asks the embedding client for one vector → returns that vector, or an empty tuple on failure.

**Call relations**: MemoryStore._legs calls this before vector search. Its failure-tolerant behavior keeps memory recall available even when semantic search is temporarily unavailable.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 980–1059)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused index candidates into real recalled memory rows. It also removes candidates the reader should not see, that are retired, outside the time window, or tied to a stale page revision.

**Data flow**: It receives fused hits, subjects, readable source IDs, and optional dates → reads matching memory rows from the database with permission and supersession filters, checks current page states for page-derived rows, and builds Recalled objects in fused order → returns readable recalled items.

**Call relations**: MemoryStore.recall calls this after fuse_recall. It is the safety checkpoint between the index, which only knows candidates, and the final recall output, which must obey database truth and permissions.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 1061–1068)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Gets current page states that the reader is allowed to access. It is used to confirm source-page search results are still valid and readable.

**Data flow**: It receives page IDs and a source reader → returns an empty dictionary for no IDs, otherwise checks that readable page-state access is wired and calls it → returns page state information or raises an error if unavailable.

**Call relations**: MemoryStore.search_sources calls this after finding page candidates. It supplies the final current-state check before SourceMatch results are returned.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 1071–1084)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a MemoryStore from an extension context. It fails clearly if the required index or embedding backend has not been connected.

**Data flow**: It receives an ExtensionContext → verifies index and embed clients exist → copies the transaction opener, workspace ID, page-state readers, and permission readers into a MemoryStore → returns the store.

**Call relations**: Extension wiring code uses this as the factory for the memory workflow. It keeps callers from constructing a half-working MemoryStore that would fail later during recall or indexing.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1106–1108)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing pass over due memory items. It claims a batch and indexes each claimed item one by one.

**Data flow**: It starts with no direct input beyond the indexer’s configured database, index, embedder, and chunker → calls _claim_due to get due rows → passes each row to _index_item → changes index and database state as each item settles.

**Call relations**: A background job or scheduler calls this periodically. It is the simple driver that connects claiming work to doing the indexing work.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1110–1145)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically reserves memory rows that still need indexing. The claim prevents overlapping indexer runs from embedding the same body at the same time.

**Data flow**: It reads current time and a lease cutoff → selects rows with no embedding digest and no active claim, locks them where supported, stamps their claim time → returns MemoryItem objects for the claimed rows.

**Call relations**: MemoryIndexer.run calls this at the start of an indexing pass. The rows it returns are handed to _index_item, and the lease rule lets abandoned claims become eligible again later.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1147–1184)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Indexes one claimed memory item if it is still safe to publish. Page-derived memories are only published when their source page still has the same subject and revision.

**Data flow**: It receives a claimed MemoryItem → checks publishability, deletes old chunks and settles if not publishable, otherwise upserts chunks and embeddings when missing, rechecks the row’s current binding, and finally settles if nothing changed → updates the index and possibly the database.

**Call relations**: MemoryIndexer.run calls this for each claimed row. It uses _publishable for safety checks, chunk_embed_upsert for chunking and embeddings, and _settle to mark a completed decision.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1186–1195)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Decides whether a memory body may be placed in the search index. Direct memories are always publishable, but page-derived memories must still match the live page.

**Data flow**: It receives subject, page ID, and revision → returns true immediately for non-page memories, otherwise asks for the current page state and compares subject and revision → returns true or false.

**Call relations**: MemoryIndexer._index_item calls this before publishing and again after possible database changes. This prevents stale page-derived facts from taking up recall candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1197–1221)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory item as decided by the indexer. Settling stores a digest of the body and clears the claim so the row no longer appears due.

**Data flow**: It receives the MemoryItem that was claimed → computes a SHA-256 digest of the body, updates the row only if its binding and claim still match → writes the digest, clears the claim, and updates the timestamp.

**Call relations**: MemoryIndexer._index_item calls this after publishing a valid body or withholding an invalid one. The guarded update ensures a stale indexing run cannot settle a row that another process has already rebound.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1244–1246)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a batch of source-page changes to the memory extension’s page index. It processes each change in order.

**Data flow**: It receives page-change records → loops through them → passes each to _apply → returns after all changes have been reflected or skipped.

**Call relations**: The core page-change runner calls this with delivered batches. It delegates the detailed tombstone, staleness, and indexing rules to PageIndexer._apply.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1248–1306)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Updates searchable page chunks and the local page mirror for one page change. It removes stale page data and only indexes a payload if it still matches the current page state.

**Data flow**: It receives a PageChange → reads current page state, marks left-behind facts due, handles tombstones and stale changes by deleting chunks and mirror rows, otherwise chunks and embeds the page body, rechecks current state, and upserts the mem_page mirror → updates index and database.

**Call relations**: PageIndexer.apply calls this for every change. It uses _unsettle_left_behind_facts so memory facts tied to old page revisions are rechecked by MemoryIndexer rather than staying searchable forever.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1308–1336)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks facts from old page revisions as needing the memory indexer again. This is how page movement causes stale page-derived memory chunks to be withdrawn safely.

**Data flow**: It receives a page ID and the page’s current state, if any → finds memory rows derived from that page that no longer match the live subject and revision, or all of them if the page is gone → clears their embedding digest and claim → leaves the rows themselves in place.

**Call relations**: PageIndexer._apply calls this before handling each page change. It hands the actual publish-or-withdraw decision to MemoryIndexer, keeping one owner for memory-item chunks.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).
