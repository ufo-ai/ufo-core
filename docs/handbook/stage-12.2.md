# Durable memory extension  `stage-12.2`

This stage adds long-term memory to the system. It is shared behind-the-scenes support that helps agents remember useful facts across work, instead of relying only on the current conversation. The manifest is the wiring panel. It tells the wider system which memory tools exist, when automatic recall should run, which background jobs should start, how page changes should be noticed, and where the memory viewing page lives.

The store is the main filing cabinet and search desk. It saves memory records, finds the most relevant ones when an agent needs context, keeps links to source pages searchable, and does slower indexing work in the background so normal saves stay quick. The condenser is the cleaner. It looks at changed pages, pulls out facts worth keeping, and later combines related older facts into broader summaries so memory does not become a pile of duplicates. The surface is the read-only window for operators. It lets authorized people inspect stored memories for a workspace without changing them.

## Files in this stage

### Memory wiring and persistence
Declares the memory extension and connects durable recall, condensation, storage, search, and background indexing into the system.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup for registration, then active during tool calls, prompt submission hooks, page-change processing, scheduled jobs, and memory UI requests`

This file is like the front desk and schedule board for the memory feature. It tells the host application what the memory extension offers, when each part should run, and which helper should do the work.

The extension has two user-facing tools. One searches memory and related source pages, so the agent can look up facts before answering. The other writes a durable memory item, such as a preference or stable fact about the user. The file also adds an automatic recall hook that runs when a user submits a prompt. That hook tries to find relevant memories and injects them into the model's context before the model replies. Importantly, this recall is best-effort: if it is slow or fails, the user request is allowed to continue instead of being blocked.

The file also connects memory to source pages. When pages change, one listener indexes page text so it can be searched, while another listener asks a model to derive durable facts from those pages. Separate scheduled jobs index newly written memory items and consolidate older facts into broader summaries.

Finally, the `manifest` function packages all of this into a `Manifest`, which is the extension's contract with the host system.

#### Function details

##### `_date_bound`  (lines 146–157)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date string from the search tool into a real UTC time boundary. It exists so memory searches can be limited to a clear start and end window.

**Data flow**: It receives either no value or an ISO-style date or date-time string, plus a flag saying whether this is the end of the range. If there is no value, it returns no boundary. If there is a date, it parses it, assumes UTC when no time zone is given, and for an end date with no time included it moves the boundary to the next midnight so the named day is included. The result is a `datetime` value or `None`; a badly shaped date raises an error that the tool layer can report.

**Call relations**: The memory search tool calls this before searching. It converts the user's `start_date` and `end_date` fields into the time values that `MemorySearchService.search` can pass down to the memory store.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 166–214)

```
async def search(self, queries: tuple[str, ...], subjects: frozenset[str], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs the shared memory search workflow used by the tool and by other extensions. It searches both durable memory items and indexed source-page snippets, then returns a single set of readable matches.

**Data flow**: It receives focused query strings, a set of subjects whose memory is allowed to be searched, and optional start and end dates. It gets the memory store from the extension context, runs all memory-item searches and all source-page searches in parallel, then interleaves the results so each query gets a fair chance. It removes duplicates, caps the result count, wraps each hit as a `MemoryMatch`, and returns a tuple of matches with text, kind, reference, and creation time.

**Call relations**: The `memory_search_handler` builds this service when the agent uses the memory search tool. The manifest also registers it as the default memory search provider, so other parts of the system can use the same search behavior instead of inventing their own.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `match_line`  (lines 217–224)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one memory search result into a line of text the agent can read. It includes the snippet, the kind of result, and, when available, a reference that can be opened later.

**Data flow**: It receives one `MemoryMatch`. It starts with the result kind and text. If the match has an object reference, it appends that reference and the date if known. It returns one plain text bullet line.

**Call relations**: After `memory_search_handler` receives matches from `MemorySearchService.search`, it calls this helper for each match to build the final tool response shown to the agent.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 227–242)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the `memory_search` tool that the agent can call. It lets the agent look up stored facts and source-document snippets before answering a user.

**Data flow**: It receives the tool context and validated search arguments. It checks that the extension context is present, converts optional date strings into time bounds, and searches the readable subjects for the current conversation. If nothing matches, it returns a short 'No matching memory' message. Otherwise it formats each match into text and returns that as the tool result.

**Call relations**: The `manifest` function registers this as the handler for the `memory_search` tool. During a tool call, it relies on `_date_bound` for date parsing, `MemorySearchService.search` for the actual lookup, and `match_line` for the final readable output.

*Call graph*: calls 2 internal fn (_date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 245–259)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the `memory_update` tool that writes a durable memory item. The agent uses it when it learns a stable fact, preference, decision, event, or task-related memory worth keeping.

**Data flow**: It receives the tool context and a validated memory write request. It checks that the extension context is present, decides the subject from the current conversation audience, builds a `MemoryWrite` record with the body, class, kind, confidence, and source reference, and commits it to the memory store. It returns a short confirmation naming the subject that received the memory.

**Call relations**: The `manifest` function registers this as the handler for the `memory_update` tool. When the tool runs, this function hands the actual database write to the memory store returned by `store_for`.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 262–292)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically finds memories relevant to a user's new prompt and injects them into the model's context. It is designed not to block the conversation if memory lookup fails.

**Data flow**: It receives a hook context. If the event is not a user prompt submission, it does nothing. Otherwise it computes which memory subjects may be searched, tries to recall matching items within a short timeout, logs which memories were injected or what kind of error happened, filters out topic-only recalls, and returns extra context text when there are usable memories. If lookup fails or times out, it returns `None` so the user's turn continues without memory.

**Call relations**: The `manifest` function registers this for the `user_prompt_submit` event. The wider system calls it before the model runs; it calls into the memory store for recall and returns an `InjectContext` only when it has safe, relevant memory to add.

*Call graph*: 5 external calls (__init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 295–304)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that turns committed memory items into searchable index chunks. Without this, newly written memories might exist in storage but not be findable by semantic search.

**Data flow**: It receives an extension context for a workspace. It checks that both the search index and embedding backend are available. Then it creates a `MemoryIndexer` with the index, embedding service, transaction support, text chunker, and page state storage, and runs it. The function returns nothing, but it updates indexing state and search data through the indexer.

**Call relations**: The `manifest` function registers this as the `memory_index` scheduled job. The job scheduler calls it for workspaces selected by `_items_awaiting_index`, and it delegates the detailed indexing work to `MemoryIndexer`.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 307–323)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to source-page changes by indexing page text and keeping a memory-side mirror of the page. This makes changed documents searchable through the memory system.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. If it is a page-change batch, it checks that indexing and embedding backends are available, builds a `PageIndexer`, and applies the delivered page changes. It returns no hook outcome, but the page index and mirror rows are updated.

**Call relations**: The `manifest` function registers this for `page_change` events. The core runner delivers batches of page changes, and this function hands those changes to `PageIndexer` so they become searchable source snippets.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 326–337)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds to source-page changes by deriving durable fact memories from changed pages. It turns document content into remembered facts that can later be recalled directly.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. If it is a page-change batch, it requires a model backend because fact derivation needs model reasoning. It creates a `FactDeriver` using the memory store and model, then applies it to the page changes. It returns no hook outcome, but it may write new facts and retire page-derived facts that were replaced.

**Call relations**: The `manifest` function registers this as a second `page_change` consumer, separate from `index_pages`. The page-change runner calls it with batches, and it delegates the model-based extraction work to `FactDeriver`.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 340–348)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that groups older related facts into higher-level summaries. This keeps memory useful as it grows, replacing clusters of small facts with broader semantic memories.

**Data flow**: It receives an extension context for a workspace. It checks that the embedding backend is available, then creates a `MemoryConsolidator` with embedding, transaction, workspace, and model information. It runs the consolidator. The function returns nothing, but the consolidator may create summary memories and mark original facts as superseded.

**Call relations**: The `manifest` function registers this as the `memory_consolidate` scheduled job. The scheduler uses `_consolidatable_workspaces` to choose likely workspaces, then calls this function to perform the consolidation pass.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 351–356)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces that have memory items not yet indexed. It helps the scheduler avoid running the indexing job where there is no indexing work to do.

**Data flow**: It takes no direct input. It builds a SQL query that selects distinct workspace IDs from memory items whose embedding digest is missing, which means they still need embedding and indexing. It returns the query object rather than executing it.

**Call relations**: The `manifest` function passes this query builder to `owner_candidates` for the `memory_index` job. The job system uses it to decide which workspace owners should receive an `index_memory` run.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 359–375)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces where memory consolidation is actually worthwhile. It looks for enough old, live facts to form a meaningful cluster.

**Data flow**: It takes no direct input. It computes an age cutoff based on the current UTC time, then builds a SQL query for workspaces with at least the required number of facts that are old enough, not page-derived, and not already superseded. It returns the query object rather than executing it.

**Call relations**: The `manifest` function passes this query builder to `owner_candidates` for the `memory_consolidate` job. The scheduler uses it before calling `consolidate_memory`, so consolidation is not attempted for workspaces with too few or too-new facts.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 378–454)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest, which is the host system's map of everything this memory extension provides. This is the central registration point for tools, hooks, jobs, objects, skills, search providers, and UI routes.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name and version, the `memory_search` and `memory_update` tool definitions, the memory object kind, hook registrations for prompt recall and page changes, scheduled job definitions, the memory skill folder, the default memory search provider, and the memory surface routes. It returns that manifest to the host application.

**Call relations**: The host loads this function during extension startup. The objects it returns tell the rest of the system when to call `memory_search_handler`, `memory_update_handler`, `recall_hook`, `index_pages`, `derive_facts`, `index_memory`, and `consolidate_memory`.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background consolidation`

The memory system can store many small pieces of information, but raw page text and repeated facts are not ideal for recall. This file acts like a careful editor. First, `FactDeriver` watches batches of changed source pages and asks a language model to pull out durable, standalone facts. It only replaces old page-derived facts after a new fact has actually been written, so a bad model reply does not accidentally erase useful memory. Deleted pages are treated differently: their facts are retired because there is no replacement page left to read from.

Second, `MemoryConsolidator` is a background cleaner for older facts. It looks for facts that are old enough, still active, and not tied directly to a source page. It groups them by subject, embeds their text into numeric vectors, then clusters facts whose vectors point in a similar direction. An embedding is a model-made number list that lets the code compare meanings mathematically. For each related cluster, it asks the model for one concise summary, writes that as a `semantic` memory item, and marks the original facts as superseded. Think of it like replacing several sticky notes about the same topic with one clearer note. Model calls are made before database write transactions where possible, so the database is not kept waiting on slow model work.

#### Function details

##### `FactDeriver.apply`  (lines 113–127)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the main entry for turning page changes into fact memories. It decides which changed pages are worth sending to the model, and it retires facts for pages that have disappeared.

**Data flow**: It receives a batch of page changes. It asks the memory store which pages are still live, retires facts for missing pages, filters out deleted or very short pages, then sends the remaining pages onward in small groups. After a group successfully produces replacement facts, it tells the store to supersede older facts for those pages.

**Call relations**: The page-change runner calls this method when it has delivered a batch of changes. `apply` splits the work into bounded chunks with `itertools.batched`, calls `FactDeriver._derive` for each chunk, and only then asks the store to retire replaced page facts.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 129–179)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> tuple[PageChange, ...]
```

**Purpose**: This function does the careful middle step between a page batch and committed fact memories. It makes sure each page is still at the same revision, parses the model's answer, writes valid facts, and reports which pages really got replacements.

**Data flow**: It receives a small group of page changes. It rechecks the current page state so it does not write facts for stale content, asks `_extract` for raw model output, parses that output with `_parse_facts`, filters out low-notability or mismatched facts, and commits each accepted fact as a `MemoryWrite`. It returns only the pages for which at least one fact was actually written.

**Call relations**: `FactDeriver.apply` calls this after filtering and batching pages. `_derive` calls `_extract` to talk to the model, `_parse_facts` to turn the model reply into validated fact objects, and creates `MemoryWrite` records that the store can commit.

*Call graph*: calls 2 internal fn (_extract, _parse_facts); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 181–198)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> str
```

**Purpose**: This function makes one bounded language-model request to extract facts from a small group of pages. It keeps the request size under control by trimming page bodies before sending them.

**Data flow**: It receives page changes, builds a compact JSON payload containing each page id and shortened body text, wraps that payload in a model request with extraction instructions, and sends it through the configured model access object. It returns the raw text reply from the model.

**Call relations**: `FactDeriver._derive` calls this when it has confirmed that a page group is still current. `_extract` builds `Message` and `ModelRequest` objects and uses JSON formatting so the model sees the pages in a predictable shape.

*Call graph*: called by 1 (_derive); 3 external calls (__init__, __init__, dumps).


##### `MemoryConsolidator.run`  (lines 228–237)

```
async def run(self) -> None
```

**Purpose**: This is the top-level background job that turns clusters of old related facts into single semantic summaries. If no model is configured, it safely does nothing.

**Data flow**: It starts with no direct input beyond the consolidator's configured database, workspace, embedding client, and optional model. It reads candidate old facts, groups them by subject, skips subjects with too few facts, embeds their text, clusters similar facts, and consolidates large enough clusters. The result is new semantic memories and superseded old facts, if suitable clusters exist.

**Call relations**: A scheduler or periodic job calls `run`. It coordinates the whole process by calling `_aged_facts`, `_buckets`, `_embed`, `_clusters`, and `_consolidate` in order.

*Call graph*: calls 5 internal fn (_aged_facts, _buckets, _clusters, _consolidate, _embed).


##### `MemoryConsolidator._aged_facts`  (lines 239–269)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: This function finds old active fact memories that are eligible for consolidation. It deliberately ignores facts already superseded and facts that came directly from pages.

**Data flow**: It calculates a cutoff time, opens a database transaction, and selects a limited number of fact rows from the current workspace that are older than the cutoff and still active. It converts each database row into an `_AgedFact` object and returns them as a tuple.

**Call relations**: `MemoryConsolidator.run` calls this first to get the raw material for consolidation. It uses SQLAlchemy to build the database query and `_AgedFact` objects to carry the selected fields through later steps.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 271–280)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: This function groups candidate facts by subject so unrelated topics are not merged together. It also caps each subject bucket so one busy subject cannot make the job too large.

**Data flow**: It receives aged facts, builds groups keyed by their subject text, sorts each group by recency, keeps only the newest facts up to a fixed limit, and returns subject-and-facts pairs.

**Call relations**: `MemoryConsolidator.run` calls this after reading aged facts. The returned buckets decide which facts will be embedded and clustered together.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 282–286)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This function converts fact text into embeddings, which are number lists used to compare meaning. Similar meanings should produce vectors that point in similar directions.

**Data flow**: It receives a group of facts, trims each fact body to a safe size, sends the text list to the embedding client, and returns a dictionary from fact id to embedding vector.

**Call relations**: `MemoryConsolidator.run` calls this for each subject bucket that has enough facts. The vectors it returns are then used by `_clusters` to decide which facts belong together.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 288–307)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: This function groups facts that appear semantically similar based on their embeddings. It uses a simple newest-first approach, so newer facts become the heads of clusters.

**Data flow**: It receives facts and their embedding vectors. For each fact, newest first, it compares the fact's vector with the first fact in each existing cluster using cosine similarity, which measures whether two vectors point in the same direction. It either adds the fact to a matching cluster or starts a new one, then returns all clusters.

**Call relations**: `MemoryConsolidator.run` calls this after embeddings are available. `_clusters` calls `_cosine` for the similarity check, and its output tells `run` which clusters are large enough to send to `_consolidate`.

*Call graph*: calls 1 internal fn (_cosine); called by 1 (run).


##### `MemoryConsolidator._consolidate`  (lines 309–367)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: This function replaces one cluster of related facts with one semantic summary. It is careful to avoid writing a summary if the underlying facts changed while the model was working.

**Data flow**: It receives a model and a cluster of facts. It first asks `_summarize` for summary text; if there is none, it stops. Then it opens a database transaction, reloads the donor facts, checks that their bodies and confidence values still match what it expected, inserts a new semantic memory item, and marks the original facts as superseded by that new item.

**Call relations**: `MemoryConsolidator.run` calls this for each cluster that is large enough. `_consolidate` calls `_summarize`, uses `uuid4` to create the new summary id, and uses SQL insert, select, and update operations to make the replacement safely.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 369–378)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: This function asks the language model to write one concise statement that captures a cluster of related facts. It trims both the input facts and the returned summary to fixed limits.

**Data flow**: It receives a model and a fact cluster. It builds a compact JSON payload of shortened fact bodies, sends that with summarization instructions to the model, strips extra whitespace from the reply, cuts it to the maximum allowed length, and returns the summary text.

**Call relations**: `MemoryConsolidator._consolidate` calls this before opening the database write transaction. It builds `Message` and `ModelRequest` objects and sends them through `ModelAccess.complete`.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 381–382)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: This small helper defines how to order facts by age, with the fact id as a tie-breaker. It gives sorting code one consistent way to say which fact is more recent.

**Data flow**: It receives one `_AgedFact` and returns a pair made from its creation time and id. Sorting code can use that pair to order facts predictably.

**Call relations**: This helper supports the file's grouping and clustering flow, where facts are processed newest first. It does not call out to other project code.


##### `_cosine`  (lines 385–391)

```
def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This function measures how similar two embedding vectors are. A result near 1 means the vectors point in much the same direction, while 0 is returned if either vector has no length.

**Data flow**: It receives two tuples of numbers. It calculates each vector's length, returns 0 if comparison would be unsafe, otherwise divides their dot product by the product of their lengths. The output is a similarity score used for clustering.

**Call relations**: `MemoryConsolidator._clusters` calls this when deciding whether a fact should join an existing cluster. It uses `math.sqrt` for the vector-length calculation.

*Call graph*: called by 1 (_clusters); 1 external calls (sqrt).


##### `_parse_facts`  (lines 394–418)

```
def _parse_facts(text: str) -> tuple[ExtractedFact, ...]
```

**Purpose**: This function turns the model's raw extraction reply into validated fact objects. It is forgiving about individual bad facts, but strict when the reply does not contain a readable facts list at all.

**Data flow**: It receives raw text from the model. It looks for the first JSON object in that text, decodes it, checks that it contains a `facts` list, and validates each dictionary in that list as an `ExtractedFact`. Malformed individual entries are skipped, while a missing or unreadable list raises an error.

**Call relations**: `FactDeriver._derive` calls this after `_extract` returns a model reply. Its output decides which facts can be committed, and its errors cause the whole page group to settle nothing rather than risk deleting old facts based on an unreadable answer.

*Call graph*: called by 1 (_derive); 1 external calls (JSONDecoder).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file gives the memory extension a complete life cycle: write a memory now, make it searchable later, and safely remove or hide it when its source page changes. A memory is stored in the `memory_item` database table. The write path deliberately does not split text into chunks or create embeddings, which are number lists used for meaning-based search. Instead, background indexers do that work later, like a librarian cataloging new notes after they have been dropped in an inbox.

Recall combines several signals. It asks the index for plain word matches and meaning-based vector matches, blends their rankings with reciprocal-rank fusion, then reads the real memory rows back from the database. It also applies time decay to facts, so old time-sensitive facts slowly count less, and prevents one memory type from crowding out all others. Newly written but not-yet-indexed memories can still be found through a small direct database scan.

The file also mirrors source pages in `mem_page`, searches those page chunks, and checks that a page is still current before showing a result. This matters because stale chunks in an index could otherwise reveal old or deleted page content. The two indexer classes are the cleanup crew: one indexes memories, and one indexes source pages while invalidating facts tied to page revisions that have moved on.

#### Function details

##### `recall_subjects`  (lines 143–144)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience description into the exact set of subjects that memory recall is allowed to search. A subject is the visibility label used to keep one audience’s memories separate from another’s.

**Data flow**: It receives an `Audience` value → passes it to the shared audience helper → returns a frozen set of subject strings that can be used as a search filter.

**Call relations**: This is a small adapter around `ufo.sdk.audience.audience_subjects`. Callers use it before recall so `MemoryStore.recall` and source search only look at memories the audience is allowed to see.

*Call graph*: 1 external calls (audience_subjects).


##### `inventory`  (lines 184–253)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, newest-first list of stored memories for an operator or explorer view. Unlike recall, it is not answering a query; it shows what is in the memory store and what indexing or decay state each row is in.

**Data flow**: It receives a workspace-scoped transaction opener and a workspace id → reads recent `memory_item` rows and their linked source ids from the database → calculates age, half-life, and decay value using one shared current time → returns `MemoryInventoryItem` objects.

**Call relations**: This function calls `_aware`, `half_life_days`, and `decay_multiplier` so the inventory view reports the same aging math that recall uses. It stands beside `MemoryStore.recall`: recall finds useful memories for a query, while inventory explains what has been stored.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 256–257)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp has a timezone. This prevents age calculations from mixing timezone-aware and timezone-naive times, which Python treats differently.

**Data flow**: It receives a `datetime` → if it already has timezone information it returns it unchanged; otherwise it marks it as UTC → returns a safe timestamp for time math.

**Call relations**: Both `inventory` and `decay_multiplier` call this before subtracting dates. It is a small safety helper that keeps decay and age calculations consistent.

*Call graph*: called by 2 (decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.page_origin_is_complete`  (lines 280–288)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Checks that page-derived memories include all required page origin fields together. A memory cannot say it came from a page unless it names the page, the page revision, and the source id.

**Data flow**: It reads the `MemoryWrite` object after validation → checks whether only some page-origin fields were filled in → returns the same object if complete, or raises an error if the origin is partial.

**Call relations**: This validator runs when a `MemoryWrite` is created. It protects `MemoryStore.commit`, which relies on page-derived writes having a complete binding to a specific source page revision.


##### `_fuse`  (lines 316–339)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines ranked search hits from multiple search methods into one best score per owning row. It is the shared scoring engine for both memory recall and source-page search.

**Data flow**: It receives one or more hit lists plus the vector hit list → ranks chunks inside each list, adds reciprocal-rank scores for chunks that appear in the lists, keeps the best chunk for each owner, and records the owner’s best vector similarity → returns a dictionary keyed by owner id with fused score, cosine score, and snippet text.

**Call relations**: `fuse_hits` and `fuse_recall` both call this. It is the central place where lexical matches and vector matches stop being separate lists and become a single candidate ranking.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 342–347)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by combining lexical and vector hits into one score per page. It uses only the fused rank score, which is enough for source snippets.

**Data flow**: It receives lexical hits, vector hits, and a limit → calls `_fuse`, sorts owners by fused rank score, trims to the limit → returns `Fused` results with owner id, score, and matched text.

**Call relations**: `MemoryStore.search_sources` calls this after it asks the index for page hits. The result is then checked against the page mirror before being returned to the caller.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 350–367)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending fused rank with meaning-based similarity. This gives a memory credit both for appearing high in search results and for being semantically close to the query.

**Data flow**: It receives lexical hits, vector hits, a tail hit list for not-yet-indexed memories, and a limit → calls `_fuse`, normalizes the fused rank, blends it with vector similarity, sorts, and trims → returns `Fused` memory candidates.

**Call relations**: `MemoryStore.recall` calls this after collecting indexed hits and tail hits. Its output is passed to `_enrich`, where candidate ids become full memory rows.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 385–391)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Chooses how quickly a fact should fade in ranking based on its memory kind. Non-fact memories do not decay here.

**Data flow**: It receives an item class and memory kind → returns no half-life for non-facts, or the configured number of days for fact-like kinds → falls back to the default fact half-life when the kind is unknown.

**Call relations**: `decay_multiplier` calls this to perform ranking decay, and `inventory` calls it to show the same half-life to operators.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 394–406)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates the multiplier that makes older facts count less during recall. Confidence also matters: a low-confidence fact starts with less weight.

**Data flow**: It receives item class, memory kind, confidence, the time the fact is current as of, and the current time → finds the half-life, computes age in days, and applies the decay formula → returns a number that will multiply the relevance score.

**Call relations**: `decay_factor` uses this during recall, and `inventory` uses it for display. It calls `_aware` and `half_life_days` so all time handling and half-life choice stay in one place.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 409–412)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the standard decay calculation to a recalled memory object. It is a convenience wrapper used while ranking recall results.

**Data flow**: It receives a `Recalled` item and the current time → chooses the best timestamp from `as_of` or `created_at` → calls `decay_multiplier` → returns the score multiplier for that item.

**Call relations**: `MemoryStore.recall` calls this after full memory rows have been loaded. The returned factor is multiplied into each item’s recall score before final sorting.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (recall).


##### `enforce_type_diversity`  (lines 415–433)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one class of memory from filling the whole recall result list. This helps the caller get a mix instead of, for example, only facts when episodic pointers are also relevant.

**Data flow**: It receives ranked recalled rows and a limit → keeps rows in order while capping how many of each item class can be admitted → backfills from overflow if needed → returns at most the requested number of rows.

**Call relations**: `MemoryStore.recall` calls this after decay-adjusted sorting. It is one of the last steps before episodic memories are rewritten into topic pointers.

*Call graph*: called by 1 (recall).


##### `as_topic_pointer`  (lines 436–446)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Turns an episodic memory result into a pointer rather than returning its full body. Episodic memory acts like a breadcrumb to explore, not text to automatically inject as context.

**Data flow**: It receives a recalled item and its position in the final list → if the item is not episodic, returns it unchanged; if it is episodic, replaces its body with a short topic label and marks the recall mode as `topic` → returns the adjusted item.

**Call relations**: `MemoryStore.recall` calls this on the final diversified list. It uses `dataclasses.replace` so the original result shape is kept while only the body and mode change.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 471–560)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Stores one memory write in the database without doing expensive indexing work inline. Repeated writes of the same workspace, subject, class, and body update the same row instead of creating duplicate recallable facts.

**Data flow**: It receives a `MemoryWrite` → creates a stable content-based id → inserts or updates the `memory_item` row, clearing the indexing digest if the page binding changed → if the memory came from a source page, inserts or updates the `memory_source` link → returns nothing but changes database state.

**Call relations**: This is the main write path on `MemoryStore`. It deliberately leaves `embedding_digest` empty when indexing is needed, so `MemoryIndexer._claim_due` can later pick the row up and build searchable chunks.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 562–678)

```
async def supersede_page_facts(self, page_id: UUID, revision: int | None) -> None
```

**Purpose**: Retires memory facts that were derived from an old or removed page revision. It is careful not to delete a fact if another source page still supports the same fact.

**Data flow**: It receives a page id and optionally the page’s current revision → finds stale `memory_source` links for that page → deletes those links, deletes memory rows with no remaining links, or repoints rows to a surviving link and clears their indexing digest → deletes index chunks for rows that were fully removed.

**Call relations**: This method is called by the fact-derivation flow when a page has been reprocessed or removed. It coordinates with the index backend by deleting chunks for removed rows and with `MemoryIndexer` by marking repointed rows as due for re-checking.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 680–708)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[Recalled, ...]
```

**Purpose**: Finds memories relevant to a query for the allowed subjects. It combines indexed search, a fallback scan for just-written items, row validation, time decay, diversity, and episodic pointer conversion.

**Data flow**: It receives a query, subject filter, result limit, and optional time window → gets lexical and vector index hits through `_legs`, gets unindexed tail hits through `_untail_leg`, fuses them with `fuse_recall`, loads valid memory rows with `_enrich`, applies decay with `decay_factor`, enforces type diversity, rewrites episodic items with `as_topic_pointer` → returns recalled memories.

**Call relations**: This is the main read path for memory. It orchestrates many helpers in this file so callers do not need to know whether a result came from the index, from the unindexed tail, or from a page-derived row that needed freshness checks.

*Call graph*: calls 7 internal fn (_enrich, _legs, _untail_leg, as_topic_pointer, decay_factor, enforce_type_diversity, fuse_recall); 2 external calls (replace, now).


##### `MemoryStore.search_sources`  (lines 710–768)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages and returns matching snippets. It uses the same index style as memory recall, but the owners are pages rather than memory items.

**Data flow**: It receives a query, subjects, limit, and optional time window → gets lexical and vector page hits through `_legs`, fuses them with `fuse_hits`, reads matching `mem_page` mirror rows, asks the core page state service whether each page is still current → returns `SourceMatch` results for pages that still match subject and revision.

**Call relations**: This method calls `_legs` and `fuse_hits`, then performs the freshness checks that protect against stale indexed page chunks. It depends on `PageIndexer` keeping the `mem_page` mirror up to date.

*Call graph*: calls 2 internal fn (_legs, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._legs`  (lines 770–778)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two normal search legs for a query: word-based search and vector-based search. A search leg is one route for finding candidate chunks.

**Data flow**: It receives a query, subject filter, owner kind, and limit → embeds the query with `_embed_query`, asks the index for lexical hits, and asks for vector hits only if embedding succeeded → returns both hit lists.

**Call relations**: `MemoryStore.recall` uses this for memory items, and `MemoryStore.search_sources` uses it for source pages. It hides the detail that vector search is skipped when the query cannot be embedded.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 780–823)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int) -> tuple[Hit, ...]
```

**Purpose**: Finds newly committed memories that have not been indexed yet. This keeps a fresh memory recallable before the background indexer has had a chance to process it.

**Data flow**: It receives a query, subject filter, and limit → splits the query into terms, scans a bounded number of newest unindexed memory rows in the database, counts term matches in each body, builds temporary `Hit` objects for matching rows, sorts by match count → returns a limited hit list.

**Call relations**: `MemoryStore.recall` calls this as a third leg alongside index hits. Once `MemoryIndexer` settles a row and fills its digest, that row leaves this tail scan and is served by the index instead.

*Call graph*: called by 1 (recall); 3 external calls (__init__, split, select).


##### `MemoryStore._embed_query`  (lines 825–833)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a text query into an embedding vector for meaning-based search. If embedding fails, recall can still continue using word-based search.

**Data flow**: It receives a query string → returns an empty tuple for blank text, otherwise asks the embedding backend for one vector → on success returns that vector, and on error logs a warning and returns no vector.

**Call relations**: `MemoryStore._legs` calls this before vector search. Its failure-tolerant behavior means `MemoryStore.recall` and `MemoryStore.search_sources` degrade gracefully instead of failing the whole request.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 835–909)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused memory candidate ids into full, safe-to-return memory rows. It drops superseded rows, rows outside the time window, and page-derived rows whose source page is no longer at the expected subject and revision.

**Data flow**: It receives fused candidates, allowed subjects, and optional time bounds → reads matching `memory_item` rows from the database → asks for current page states for page-derived rows → returns `Recalled` objects in fused order only for rows that pass all checks.

**Call relations**: `MemoryStore.recall` calls this after `fuse_recall`. This is the point where index candidates are checked against durable database truth before being shown.

*Call graph*: called by 1 (recall); 3 external calls (__init__, select, UUID).


##### `store_for`  (lines 912–923)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a `MemoryStore` from an extension context. It fails clearly if the required index or embedding backends were not connected.

**Data flow**: It receives an `ExtensionContext` → checks that `index` and `embed` are present → copies the transaction opener, workspace id, page-state reader, and backends into a new `MemoryStore` → returns that store.

**Call relations**: Other extension code uses this as the factory for the memory workflow. The resulting `MemoryStore` is what exposes `commit`, `recall`, `search_sources`, and page-fact retirement.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 945–947)

```
async def run(self) -> None
```

**Purpose**: Runs one indexing pass for due memory items. It is the background worker entry point for turning stored memories into searchable chunks.

**Data flow**: It starts with no direct input besides the indexer’s configured services → claims a batch of due rows with `_claim_due` → sends each claimed item to `_index_item` → returns after the batch is processed.

**Call relations**: This method ties together the two parts of memory indexing: safe claiming and per-item indexing. It is called by whatever scheduler or job runner drives the extension’s background derivations.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 949–984)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically reserves memory rows that need indexing so two workers do not embed the same row at the same time. The claim has a lease timeout so a crashed worker does not block the row forever.

**Data flow**: It reads the current time and computes a lease cutoff → selects rows whose embedding digest is empty and whose claim is absent or expired → marks selected rows as claimed in the database → returns them as `MemoryItem` objects.

**Call relations**: `MemoryIndexer.run` calls this before indexing. `_index_item` then processes only the returned rows, while overlapping workers skip rows already claimed.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 986–1023)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Indexes one claimed memory item, or withdraws its chunks if the item should not currently be publishable. Publishable means a page-derived memory still matches the live page subject and revision.

**Data flow**: It receives a claimed `MemoryItem` → checks publishability with `_publishable`; if not allowed, deletes its index chunks and settles the row → if allowed and chunks are missing, chunks and embeds the body into the index → rereads the row’s current page binding to catch races → settles only if the binding is still the one it processed.

**Call relations**: `MemoryIndexer.run` calls this for each claimed item. It calls `_publishable`, `chunk_embed_upsert`, and `_settle`, and it talks to the index backend to remove stale chunks when needed.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1025–1034)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Decides whether a memory body is allowed to appear in the search index. Tool-written memories are always allowed; page-derived memories are allowed only while their source page still matches the stored subject and revision.

**Data flow**: It receives a subject, optional page id, and optional revision → if there is no page id, returns true → otherwise reads the current page state and compares subject and revision → returns true only for an exact match.

**Call relations**: `MemoryIndexer._index_item` calls this before publishing and again after indexing work. These checks stop stale page-derived memories from occupying recall candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1036–1060)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by the indexer. It stores a digest of the body and clears the claim so the row no longer appears due.

**Data flow**: It receives the claimed `MemoryItem` → hashes the body text → updates the database row with the digest and clears `embedding_claimed_at`, but only if the row still has the same subject, body, page binding, source id, and an active claim → returns nothing.

**Call relations**: `MemoryIndexer._index_item` calls this after either publishing chunks or deciding the row should be withheld. The guarded update prevents an old indexing decision from settling a row that was changed underneath it.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1083–1085)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the memory extension’s page index. It is the batch-level entry point for page indexing.

**Data flow**: It receives a tuple of `PageChange` objects → processes each change one by one through `_apply` → returns after all changes have been applied.

**Call relations**: The core page-change runner owns the cursor and calls this with changes. `PageIndexer.apply` delegates the actual per-page logic to `_apply`.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1087–1145)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Indexes or removes one source page change, while checking that the page is still current before and after expensive embedding work. It also marks facts from old page revisions as needing re-checking.

**Data flow**: It receives one `PageChange` → reads the current page state → calls `_unsettle_left_behind_facts` → for tombstones or stale changes, deletes page chunks and mirror rows when appropriate → for current live pages, chunks and embeds the page body, checks the page state again, then upserts the `mem_page` mirror row → returns nothing but updates the index and database.

**Call relations**: `PageIndexer.apply` calls this for each change. It uses `chunk_embed_upsert` for live page content and cooperates with `MemoryIndexer` by clearing digests on facts tied to page revisions that are no longer current.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1147–1175)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory rows as due for the memory indexer when their source page has moved on or disappeared. This lets the memory indexer withdraw chunks for facts whose page revision is no longer live.

**Data flow**: It receives a page id and the page’s current state if any → builds a database condition for rows created from that page that no longer match the live subject and revision, or all rows from the page if it is gone → clears their embedding digest and claim fields → returns nothing.

**Call relations**: `PageIndexer._apply` calls this at the start of every page change. It does not delete the memory rows itself; it asks `MemoryIndexer` to revisit their publishability by making them due again.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### Memory exploration
Provides the operator-facing read-only interface for browsing stored memory records by workspace.

### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the small web doorway into the memory extension’s stored data. Its job is to show an operator what the system currently has in durable memory for one workspace, much like opening a read-only filing cabinet to inspect every folder inside.

The file serves two things. First, it returns a static HTML page, `memory.html`, which is the browser interface. Second, it exposes an API endpoint that returns memory records as JSON, a common plain data format used by web pages and services.

Access is tied to the shared operator session. In plain terms, the user must already be recognized as an operator, and the request must be scoped to one workspace. That workspace scope matters because the memory table belongs to the extension, and reads must only see rows for the selected workspace. The code relies on row-level security, meaning the database itself helps enforce “only show rows this workspace is allowed to see.”

The important behavior is that this surface is read-only. It does not create, update, or delete memories. It simply opens the extension’s own workspace-scoped store, asks for an inventory of memory items, and sends them back newest first so the web page can display exactly what recall would draw from.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the operator’s browser. It exists so the whole interface can be served as a single HTML file.

**Data flow**: It receives the surface context and the incoming web request, then checks whether the HTML file was loaded when the module started. If the file is missing, it raises an error so the problem is visible instead of returning a broken blank page. If the file is present, it wraps the HTML text in an HTTP response and sends it back to the browser.

**Call relations**: When an operator opens the memory surface with a GET request to the base path, the route table points the request here. This function does not fetch memory data itself; it only delivers the page that will later call the JSON API.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns all memory records for the currently bound workspace as JSON. It is the data endpoint used by the explorer page to fill in the list of memories.

**Data flow**: It receives the surface context, which includes the workspace id chosen for this operator session, and the incoming request. It creates an extension context for the memory extension with an empty declared credential set, then opens the extension’s scoped transaction. Through that transaction it asks the memory store for its inventory for the workspace. The resulting memory objects are converted into JSON-friendly dictionaries and returned in a JSON HTTP response.

**Call relations**: After the browser has loaded the explorer page, it calls the `api/memories` route, which leads here. This function hands the actual database reading to `ufo_ext_memory.store.inventory`, then turns the returned items into the web response the page can display.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).
