# Memory, knowledge, and page-change hooks  `stage-3.1.5`

This stage is shared behind-the-scenes support. It is about teaching the UFO platform what long-term knowledge features are available before the main work begins. Each file is a manifest, which is like a sign-up sheet that says, “Here are the tools and reactions this extension provides.”

The knowledge-graph manifest connects the app to graph search. A graph is a web of linked facts, like people, pages, and ideas connected by relationships. It also registers a prompt-time hook, which can add useful graph context before the assistant answers, and a page-change hook, which extracts new facts when synced pages change.

The memory manifest does the same for long-term memory. It declares tools, search, scheduled background jobs, a skill, and a small web surface so the system can store and retrieve remembered information over time.

The page-alerts manifest adds tools to create, list, and cancel watches on pages. Its change hook reacts when a watched page is updated, so the system can alert the user.

## Files in this stage

### Knowledge graph integration
Registers the knowledge-graph extension’s search tool, prompt context hook, and page-change extraction hook.

### `extensions/knowledge_graph/ufo_ext_knowledge_graph/manifest.py`

`orchestration` · `startup registration, then active during tool calls, prompt submission, and page-change processing`

The knowledge graph is a structured map of entities and relationships found in source pages. Instead of only searching text, it can answer questions like “who is connected to this company?” or “what topics are related to this person?” This file registers that capability with the larger system.

It defines the input shape for the `graph_search` tool, including the starting entity, how far to walk through the graph, and optional relationship types to include. When the tool runs, it looks up nearby graph relations and returns readable lines with citations back to the pages they came from.

It also adds two hooks. A hook is code the main system calls at a particular moment. The `user_prompt_submit` hook tries to add relevant graph facts to the user’s message before the model answers. It is deliberately best-effort: if graph lookup is slow or fails, the user’s turn still continues. The `page_change` hook runs after source pages change. It asks the graph extractor to turn changed page content into graph nodes and typed edges later, rather than slowing down the original page write.

Together, these pieces make the graph available for both background learning and live answering.

#### Function details

##### `graph_search_handler`  (lines 71–88)

```
async def graph_search_handler(ctx: ToolContext, args: GraphSearchInput) -> ToolResult
```

**Purpose**: Runs the `graph_search` tool. It starts from a named entity, follows nearby relationships in the stored graph, and returns a readable answer with source-page citations.

**Data flow**: It receives a tool context and validated search arguments. It reads the extension database transaction and workspace id from the context, converts any requested relationship filters into the graph’s allowed edge types, and asks `GraphStore` to traverse the graph from the given entity for the requested number of hops. The resulting subgraph is turned into text lines. If nothing is found, it returns a friendly “No graph relations found” message; otherwise it returns a text result headed with the searched entity.

**Call relations**: The system calls this function when the registered `graph_search` tool is used. Inside, it relies on `graph_subjects` to scope the search to the current audience member, `to_edge_type` to validate relationship filters, `GraphStore` to do the actual graph lookup, and `render_subgraph` to make the result readable. It wraps the final text in `TextContent` and `ToolResult` so the tool system can send it back.

*Call graph*: 6 external calls (__init__, __init__, __init__, graph_subjects, render_subgraph, to_edge_type).


##### `graph_context_hook`  (lines 91–110)

```
async def graph_context_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Adds relevant graph relations to a user prompt before the model answers. It is careful not to block the conversation if graph lookup is slow or broken.

**Data flow**: It receives a hook context. If the hook payload is not a submitted user prompt, it does nothing. For a real prompt, it opens a short timed window, builds a `GraphStore`, and asks for graph context related to the prompt text and the current audience member. It renders any returned relations as plain text and outputs an `InjectContext` object that can be added to the model’s context. If the lookup fails, times out, or returns no lines, it outputs nothing.

**Call relations**: The core hook system calls this during the `user_prompt_submit` event. This function calls `asyncio.timeout` so the graph lookup has a firm time budget, then uses `graph_subjects`, `GraphStore`, and `render_subgraph` to find and format useful relations. If successful, it hands the formatted text to `InjectContext`; if not, it quietly steps aside so the user turn can continue.

*Call graph*: 5 external calls (__init__, __init__, timeout, graph_subjects, render_subgraph).


##### `extract_graph`  (lines 113–125)

```
async def extract_graph(ctx: HookContext) -> HookOutcome
```

**Purpose**: Processes changed source pages and updates the knowledge graph from them. This keeps graph extraction out of the immediate page-write path, so saving or syncing pages does not have to wait for model-based extraction.

**Data flow**: It receives a hook context. If the payload is not a batch of page changes, it returns without doing anything. For a page-change batch, it creates a `GraphExtractor` using the current transaction, workspace id, and model access, then applies the extractor to the changed pages. It does not return user-facing content; its effect is to update graph data in storage.

**Call relations**: The core page-change runner calls this function for the extension’s `page_change` hook. This function is the bridge between delivered page-change batches and `GraphExtractor`, which performs the real work of deriving graph nodes and typed relationships from page content.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 128–152)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension declaration that the host system reads at startup. It tells the system the extension’s name, version, available tool, and hooks.

**Data flow**: It takes no input. It creates a `ToolDef` for `graph_search`, including its description, input model, and handler function. It also creates two `HookSpec` entries: one for adding graph context before a user prompt is answered, and one for extracting graph data from page changes. It returns a `Manifest` object containing all of that registration information.

**Call relations**: The extension loader calls this function when it discovers the knowledge-graph extension. The returned `Manifest` is how the larger UFO system learns to call `graph_search_handler` for the tool, `graph_context_hook` during prompt submission, and `extract_graph` during page-change processing.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Long-term memory integration
Declares the memory extension’s tools, hooks, scheduled jobs, search provider, skill, and web surface.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, prompt handling, page-change handling, scheduled jobs`

This file is like the memory extension’s registration desk and traffic controller. It tells the larger UFO system what memory can do, when it should be called, and which helper classes should do the work.

The extension gives the agent two tools. One searches stored memory and synced source pages. The other writes new durable facts, such as a user preference or project detail. It also adds a prompt hook: before the model answers a user, the hook tries to find relevant memories and quietly injects them into the model’s context. This is deliberately “best effort”: if recall is slow or broken, the user’s request still goes through.

The file also connects memory to page changes. When source pages are added or updated, one hook indexes them for search, while another tries to derive durable facts from them. Scheduled jobs do background maintenance: indexing newly written memory items and consolidating older facts into higher-level summaries.

Without this file, the memory code might exist, but the system would not know when to invoke it. The agent would not get memory tools, automatic recall, page indexing, fact derivation, or consolidation jobs.

#### Function details

##### `_date_bound`  (lines 148–159)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: This helper turns an optional date string into a concrete UTC time boundary for memory searches. It lets users say things like “from 2026-01-31” without needing to provide a full timestamp.

**Data flow**: It receives a string or nothing, plus a flag saying whether this is an end boundary. If there is no string, it returns nothing. If there is a string, it parses it as an ISO-style date or date-time, assumes UTC when no time zone is given, and for an end date written as just a date, moves the boundary to the next midnight so the whole day is included. The result is a datetime value or an error if the text is not a valid date.

**Call relations**: The memory search tool calls this before searching. It converts the user-facing start_date and end_date fields into the time window that the lower-level search service can understand.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 168–207)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the shared search workflow for memory. It searches both stored memory facts and indexed source-page snippets, then merges the results so several focused queries each get a fair chance to contribute.

**Data flow**: It receives one to three query strings, an optional member identity, and optional start and end times. It chooses the right subjects to search, such as the current member’s private memory plus shared memory, asks the store to recall memory items and search source snippets for every query in parallel, removes duplicates, and interleaves the results by query. It returns a tuple of MemoryMatch objects that describe what was found.

**Call relations**: The memory_search tool builds this service when a user or model asks to search memory. The manifest also registers this class as the extension’s memory search provider, so other extensions can use the same search behavior instead of inventing their own.

*Call graph*: 5 external calls (__init__, gather, zip_longest, recall_subjects, store_for).


##### `memory_search_handler`  (lines 210–229)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: This is the tool handler for the agent-facing memory_search tool. It turns the tool arguments into a real memory search and formats the answer in a simple bullet list.

**Data flow**: It receives the tool context and structured search arguments. It checks that the extension context is available, parses the optional date bounds, calls MemorySearchService.search with the current audience member, and then turns the matches into text. If nothing matches, it returns a clear “No matching memory” result.

**Call relations**: The manifest registers this as the handler for the memory_search tool. When the model chooses that tool, the runtime calls this function; this function delegates the actual searching to MemorySearchService.search and wraps the result as tool output.

*Call graph*: calls 1 internal fn (_date_bound); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 232–250)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: This is the tool handler for saving a new durable memory item. It lets the agent record persistent facts, preferences, decisions, events, or tasks for later recall.

**Data flow**: It receives the tool context and the memory item to write. It decides whether the memory belongs to shared workspace memory or the current member’s private memory, builds a MemoryWrite record with the body, class, kind, confidence, and optional source reference, and commits it to the memory store. It returns a short confirmation saying where the memory was saved.

**Call relations**: The manifest registers this as the handler for the memory_update tool. When the model decides something should be remembered, the runtime calls this function, and it hands the write to the memory store.

*Call graph*: 5 external calls (__init__, __init__, __init__, member_subject, store_for).


##### `recall_hook`  (lines 253–283)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook automatically brings relevant stored memories into a user turn before the model answers. It is intentionally safe: memory recall is helpful, but it should never block the conversation.

**Data flow**: It receives a hook context and first checks that the event is a submitted user prompt. It searches memory for the prompt text using the current member and shared subjects, but only within a short timeout. If recall succeeds, it filters out topic-only matches, logs which memories were injected, and returns extra context text for the model. If recall fails or takes too long, it logs the problem and returns nothing.

**Call relations**: The manifest registers this for the user_prompt_submit event. The runtime calls it before model generation; the hook calls the memory store for recall and may hand back InjectContext so the model sees relevant memory in its prompt context.

*Call graph*: 5 external calls (__init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 286–291)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job indexes memory items that have been committed but not yet prepared for search. Indexing means turning text into chunks and embeddings, which are numeric representations used for similarity search.

**Data flow**: It receives the extension context. It checks that both the index backend and embedding backend are available, builds a MemoryIndexer with those services and a text chunker, and runs it. The output is not returned directly; the job updates backend storage so future memory searches can find these items.

**Call relations**: The manifest registers this as the memory_index job. The scheduler calls it for workspaces that have memory items awaiting indexing, and it delegates the real indexing work to MemoryIndexer.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 294–309)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook indexes changed source pages so their contents can be searched alongside explicit memory items. It keeps memory search connected to synced documents or pages.

**Data flow**: It receives a hook context and checks that the payload is a batch of page changes. It verifies that index and embedding services are available, builds a PageIndexer with the workspace identity and text chunker, and applies the page changes. It returns nothing because its effect is updating the index and mirror records.

**Call relations**: The manifest registers this as one consumer of page_change events. When the core runner delivers a batch of changed pages, this function passes the batch to PageIndexer so source snippets become searchable.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 312–320)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook tries to turn changed source pages into durable memory facts. It is the part that reads source-page updates and distills useful long-term information from them.

**Data flow**: It receives a hook context and ignores anything that is not a page-change batch. For real page changes, it builds a FactDeriver using the memory store and the configured model, then applies it to the changed pages. The result is stored memory items, if the deriver finds facts worth keeping.

**Call relations**: The manifest registers this as a second consumer of page_change events, separate from page indexing. When page changes arrive, this function hands them to FactDeriver so the system can learn durable facts from source content.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 323–331)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job compresses older related facts into higher-level memory summaries. It helps keep long-term memory useful instead of becoming a pile of small repeated details.

**Data flow**: It receives the extension context. It checks that the embedding backend is available, builds a MemoryConsolidator with embedding, transaction, workspace, and model access, and runs it. The job may create semantic summary memories and mark older facts as superseded.

**Call relations**: The manifest registers this as the memory_consolidate job. The scheduler calls it for workspaces that have enough older facts to consolidate, and it delegates clustering and summarizing to MemoryConsolidator.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 334–339)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This helper builds the database query that finds workspaces with memory items still missing embeddings. It is used to decide where the indexing job has useful work to do.

**Data flow**: It takes no input. It builds a SQL select statement that looks for distinct workspace IDs among memory items whose embedding digest is empty. It returns the query object, not the query results.

**Call relations**: The manifest passes this helper to owner_candidates when defining the memory_index job. The job system uses the query to choose candidate workspaces before running index_memory.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 342–357)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: This helper builds the database query that finds workspaces where memory consolidation is likely to be worthwhile. It avoids scheduling consolidation for workspaces with too few, too-new, or already-superseded facts.

**Data flow**: It takes no input. It calculates an age cutoff based on the current UTC time, then builds a SQL select statement for workspaces with enough live fact memories older than that cutoff. It returns the query object for the scheduler to use.

**Call relations**: The manifest passes this helper to owner_candidates when defining the memory_consolidate job. The job system uses it to decide which workspaces should run consolidate_memory on a scheduled tick.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 360–432)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the full declaration of the memory extension. It tells the host system which tools, hooks, jobs, skills, search provider, and surface routes this extension offers.

**Data flow**: It takes no input. It builds ToolDef records for memory search and update, HookSpec records for prompt and page-change events, JobSpec records for indexing and consolidation schedules, a skill spec, a memory search provider spec, and a surface spec for memory UI routes. It returns one Manifest object containing all of that registration information.

**Call relations**: The extension loader calls this during startup. The returned Manifest is how the rest of the system learns to call memory_search_handler, memory_update_handler, recall_hook, index_pages, derive_facts, index_memory, and consolidate_memory at the right times.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### Page alert integration
Registers page-alert chat tools and the hook that responds to synced page changes.

### `extensions/page_alerts/ufo_ext_page_alerts/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s public “menu.” When the platform loads the page-alerts extension, it needs to know two things: what a user can ask it to do in chat, and what background events it wants to hear about. Without this file, the platform would not know that page watching exists, so users could not start or stop watches and page changes would not trigger alerts.

The file gives the extension a name and version, then builds a Manifest. A manifest is like a registration form: it lists the extension’s tools and event hooks. The tools are chat-callable actions. One lets a conversation start watching synced workspace pages for a topic. Another lists existing watches. A third cancels a watch by name. Each tool includes a short user-facing description, an input model that says what information the tool expects, and a handler function that does the real work.

The file also registers a page_change hook. A hook is a callback the platform runs when a certain event happens. Here, when a page changes, the platform calls on_page_change so the extension can decide whether the changed page matches any watch and should alert the original conversation.

#### Function details

##### `manifest`  (lines 21–50)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the page-alerts extension declaration that the platform reads at startup. It tells the platform which chat tools this extension provides and which page-change event it wants to listen for.

**Data flow**: It starts with the extension name and version defined in this file, plus imported input models and handler functions from the alerts module. It wraps each chat action in a ToolDef, wraps the page-change listener in a HookSpec, and returns one Manifest containing all of that registration information. It does not perform page watching itself; it describes how the platform should connect requests and events to the real alert logic.

**Call relations**: When the platform discovers this extension, it calls manifest to learn what to register. Inside, manifest creates ToolDef objects for the three chat tools, creates a HookSpec for the page_change event, and hands them to Manifest so the platform can later route user tool calls to watch_pages, list_page_watches, or cancel_page_watch, and route page-change events to on_page_change.

*Call graph*: 3 external calls (__init__, __init__, __init__).
