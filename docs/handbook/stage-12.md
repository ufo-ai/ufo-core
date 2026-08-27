# Browser, Site, Research, and Document Workflows  `stage-12`

This stage is where the system does high-level “production” work during a turn. It is not just thinking or planning. It opens browsers, researches the web, builds small sites, and prepares documents that users can download or review.

The browser and hosted-site part works like a remote-controlled web desk. It can start Chrome, read what is on a page, click buttons, fill forms, take screenshots, watch downloads, and publish a built site after testing it. This lets the agent interact with real websites and check its own web apps.

The document and report part is a file workshop. It edits and reviews Word, PowerPoint, Excel, PDF, and report files. It can add comments, repair slide decks, refresh spreadsheet formulas, fill PDF forms, preview pages, and produce summaries.

The research files add web investigation. Search and fetch tools gather pages or specialized results. A wide-research tool runs many related searches in parallel and saves the combined findings as JSON, a structured text format. Source-tracking code remembers useful links and shows them later in a Sources panel. A small package marker file simply makes app-code extensions importable.

## Sub-stages

- [Browser Automation and Hosted Site Building](stage-12.1.md) `stage-12.1` — 30 files
- [Document, Office, PDF, and Report Production](stage-12.2.md) `stage-12.2` — 24 files

## Files in this stage

### Extension Package Marker
A minimal package initializer enables the application-code extension folder to be imported.

### `extensions/app_code/ufo_ext_app_code/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, which means code elsewhere can refer to modules inside `ufo_ext_app_code` using normal import paths. Think of it like a label on a drawer: the drawer may hold many useful tools, but this label mainly tells Python, “this drawer is part of the organized system.” Without this file, some Python environments or tooling might not recognize the folder as a package, which could make imports fail or behave inconsistently. Because the file is empty, it does not define settings, create objects, run startup code, or change behavior directly. Its value is structural: it helps the extension’s application-code area fit into Python’s module system.


### Research Workflows
Research tooling supports wide batch research, source tracking, and web or specialized search/fetch operations.

### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file solves a practical research problem: instead of asking one research agent to look up a long list of companies, people, or topics one by one, it spreads the work across several research subagents in parallel. Think of it like handing a stack of index cards to a small team, where each person researches one card and returns a short report.

The tool starts with an input file that contains one entity per line. It reads that file, trims blank space, removes empty lines, and deduplicates repeated entries so the same topic is not researched twice. It also enforces a maximum batch size, which prevents someone from accidentally starting an enormous number of research jobs.

For each entity, it fills the entity into a prompt template. If the caller supplied a schema file, meaning a requested shape for the returned data, that schema is added to the research prompt. The file then starts several child research runs through `ctx.spawn`, but limits how many run at once with a semaphore, which is a simple gate that only lets a fixed number of tasks through at the same time.

A key detail is crash recovery. Each child run gets a predictable deduplication key based on the parent call and entity name. If the parent is retried after a crash, already completed child work can be reused instead of repeated. Finally, all rows are written to `wide_research.json` and also returned to the caller.

#### Function details

##### `_read_lines`  (lines 41–54)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads the entity list from the sandbox and turns it into a clean list of unique names. It exists so the main tool can work with tidy input instead of raw file text.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for the shell, asks the sandbox to run `cat` on that file, and stops with a clear error if the file cannot be read. From the file contents, it strips whitespace, skips blank lines, removes duplicates while keeping the first occurrence, and returns the resulting list of entities.

**Call relations**: `_wide_research` calls this first because every later step depends on knowing which entities should be researched. Its use of shell quoting protects the file-reading command before the path is handed to the sandbox shell.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_wide_research`  (lines 57–84)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the main body of the `wide_research` tool. It reads the batch input, starts bounded parallel research jobs, collects their answers, writes a JSON output file, and returns a summary to the caller.

**Data flow**: It receives the tool context and validated input fields: the entities file, the prompt template, and the optional output schema file. It reads and checks the entity list, reads the schema file if available, creates a concurrency gate, and launches one research visit per entity. When all visits finish, it turns the rows into formatted JSON, writes them to `wide_research.json` in the sandbox, and returns a `ToolResult` containing the rows and output filename.

**Call relations**: This function is the handler registered on `WIDE_RESEARCH_TOOL`, so it runs when a caller invokes the tool. It relies on `_read_lines` to prepare the batch, uses a semaphore and `asyncio.gather` to run many nested `visit` tasks without letting too many run at once, and wraps the final JSON text in `TextContent` and `ToolResult` for the tool system.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_research.visit`  (lines 65–78)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: This nested helper performs the research work for one entity. It builds the exact prompt for that entity, starts one research subagent, and converts that subagent’s answer into one row of the final result table.

**Data flow**: It receives a single entity string from the surrounding `_wide_research` function. It waits for permission from the semaphore, replaces `{entity}` in the prompt template with the actual entity name, appends the output schema when one was read, and spawns a research-profile child task with a stable deduplication key. It returns a dictionary containing the entity name and the child task’s serialized result text, or an empty string if there was no output.

**Call relations**: `_wide_research` creates one `visit` task for each entity and runs them together through `asyncio.gather`. Each `visit` hands the actual investigation to the research subagent, while `_wide_research` later gathers all of these per-entity rows into the final JSON file.


### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `request handling and conversation slot read`

When the research extension searches the web or fetches a page, it needs a durable memory of which sources were used. Without this file, those links might disappear after the immediate tool call, and the conversation would lose an important audit trail: where the information came from.

The file defines a database table for source observations. Each saved source belongs to a workspace and conversation, and is keyed by a digest, which is a fixed-length fingerprint of the URL. That lets the system update the same URL instead of saving endless duplicates. It also records the turn that saw the source, its title, snippet, optional published date, rank, and timestamps.

Before saving, the code trims long titles, snippets, and dates to safe sizes, then validates each item as a conversation source. Invalid entries are skipped rather than breaking the whole save. It supports both PostgreSQL and SQLite database insert styles, so the same logic works in different environments.

The file also keeps the stored list from growing forever. After saving new sources, it deletes older extras beyond a fixed limit of 100 per conversation. Finally, it defines a conversation slot provider named “Sources” that can count and read the saved sources for display, marking the result as truncated if more sources exist than can be shown.

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: This small helper cuts a string down to a maximum length. It is used to keep stored source fields, like titles and snippets, within safe and predictable sizes.

**Data flow**: It receives a piece of text and a character limit. It returns the same text if it is already short enough, or only the first part of it if it is too long. It does not change anything outside itself.

**Call relations**: When sources are being saved, record_sources calls this helper before validating and storing titles, snippets, and dates. It acts like a simple pair of scissors before the data goes into the database.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: This is the main saving routine for remembered research sources. It records a batch of links for a particular conversation turn, updates existing entries for the same URL, and removes old excess entries so the conversation keeps only a reasonable source history.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and a tuple of RetrievedSource items. If the tuple is empty, it does nothing. Otherwise it opens a database transaction, trims and validates each source, creates a URL fingerprint with SHA-256, then inserts or updates the database row for that source. After saving, it selects the newest retained source IDs and deletes older rows beyond the configured limit.

**Call relations**: record_search_hits and record_fetched_page both turn their own input formats into RetrievedSource objects, then hand them to this function. Inside, record_sources uses the extension context to start a transaction, calls _bounded to shorten long fields, builds ConversationSource objects to validate the data, and uses SQLAlchemy select and delete statements to keep the saved list clean.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: This function saves sources that came from a search result list. It translates search hit objects into the common RetrievedSource shape used by the rest of this file.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a tuple of SearchHit objects. For each hit, it copies the URL, title, result text, and published date into a RetrievedSource. It then passes the whole converted tuple to record_sources, which does the actual database work.

**Call relations**: This function is a bridge between the search subsystem and the source-memory subsystem. When search results should be remembered for the conversation, callers use this function, and it hands the normalized source list off to record_sources.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: This function saves a single source that came from fetching a page directly. It lets a fetched web page appear in the same saved source list as search results.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a FetchedPage. It builds one RetrievedSource using the page URL as both the URL and title, and uses the page summary if available, otherwise the page text. It then sends that one-item tuple to record_sources for validation and storage.

**Call relations**: This is the fetched-page counterpart to record_search_hits. It does not write to the database itself; instead, it prepares the fetched page in the shared RetrievedSource format and relies on record_sources to save it consistently.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function counts how many saved sources exist for the current conversation, up to the display limit. It is used to summarize the “Sources” conversation slot without loading every source.

**Data flow**: It receives a ConversationSlotContext, which includes the extension context and conversation ID. It opens a database transaction, counts rows in the source observation table for the current workspace and conversation, then returns no value if the count is zero, or the count capped at 100 if sources exist.

**Call relations**: The SOURCES_SLOT provider uses this function as its summary step. When the user interface or conversation system wants a compact indication of available sources, this function queries the database and reports the capped count.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: This function reads the saved source list for display in the conversation’s “Sources” slot. It returns validated source objects plus a flag saying whether the visible list was cut short.

**Data flow**: It receives a ConversationSlotContext. It opens a database transaction, selects source rows for the current workspace and conversation, orders the newest observations first while preserving their rank, and asks for one more than the normal limit. It converts up to 100 rows into ConversationSource objects and wraps them in a SourcesSlotPayload. If the extra row exists, it marks the payload as truncated.

**Call relations**: The SOURCES_SLOT provider calls this when the full slot content is needed. It reads from the same table that record_sources writes to, turns database rows back into conversation source objects, and returns them in the format expected by the manifest slot system.

*Call graph*: 3 external calls (__init__, __init__, select).


### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `request handling`

This file is the doorway between the agent and outside research sources. Without it, the agent would not have a standard way to ask for web results or page contents, and it might also expose the wrong credentials or misunderstand where fetched content came from.

The file defines three tool inputs using Pydantic models, which are structured forms that validate what the agent is allowed to send. For example, web search can accept several natural-language queries, date filters, and allowed domains; URL fetching requires an HTTP or HTTPS URL; vertical search limits the category to known choices such as image, people, academic, video, or shopping.

The main tool handlers then ask the turn's selected SearchProvider to do the real work. That provider lives on the host side, so API keys stay outside the sandbox. Search results are turned into JSON with titles, URLs, snippets, dates, and highlights. Fetching a URL also adds an important warning: the page was fetched by the provider's crawler, not from the user's workspace. In everyday terms, the crawler is like a separate visitor reading the page; any login or session context belongs to that visitor, not to the current workspace.

The file also records search and fetch observations when extension state is available, so later parts of the system can remember what was seen.

#### Function details

##### `_provider`  (lines 133–136)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function retrieves the search provider chosen for the current turn. It fails loudly if no provider is configured, because the research tools cannot work without a backend to answer searches or fetch pages.

**Data flow**: It receives the tool context, reads the context's search_provider field, and either returns that provider or raises an error. Nothing else is changed.

**Call relations**: The search, fetch, and vertical-search handlers all call this first. It is the shared checkpoint that prevents the rest of the tool flow from pretending research is possible when no search backend exists.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 139–153)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search results into a JSON string the model can easily read. It keeps only the useful public-facing parts of each result, such as URL, title, snippet text, publication date, and highlights.

**Data flow**: It receives a list of search hits and an optional direct answer. It builds a plain dictionary containing result entries, adds the answer if one exists, and serializes the whole thing into JSON text.

**Call relations**: Both normal web search and vertical search call this after the provider returns results. It is the final formatting step before those tools wrap the text in a ToolResult for the agent.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 156–174)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler for the general web search tool. It runs one provider search for each requested query, combines all the hits, optionally keeps the first direct answer, records what was found, and returns the results to the model.

**Data flow**: It receives the tool context and validated search arguments. It gets the provider, turns each query plus filters into a SearchQuery, waits for the provider's results, gathers all hits into one list, records them if observation storage is available, converts them to JSON, and returns that JSON as tool text.

**Call relations**: This function is invoked when the agent uses the search_web tool. It relies on _provider to find the backend, hands each query to the provider's search method, uses record_search_hits to save observations when possible, and uses _results_json to prepare the response.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 177–198)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler for the URL fetching tool. It asks the configured provider to retrieve a public web page, optionally using a prompt to extract specific information, and clearly labels the result as coming from the provider's crawler.

**Data flow**: It receives the tool context and validated fetch arguments. It gets the provider, checks whether that provider supports fetching, and if not returns an error message. If fetching is supported, it builds a fetch request from the URL, prompt, length limit, and cache-bypass flag, sends it to the provider, records the fetched page when possible, then returns JSON containing the URL, page text, provenance warning, and optional summary.

**Call relations**: This function is invoked when the agent uses the fetch_url tool. It calls _provider first, then either stops with a helpful unsupported-provider message or hands a FetchRequest to the provider. After a successful fetch, it passes the page to record_fetched_page before returning the response.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 201–210)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler for specialized searches, such as images, academic papers, videos, shopping results, or professional profiles. It packages the requested vertical into the search query so the provider knows what kind of content to look for.

**Data flow**: It receives the tool context and validated vertical-search arguments. It gets the provider, creates a SearchQuery with the user's query, a default result count, and the selected vertical, waits for results, records the hits if possible, formats them as JSON, and returns them as tool text.

**Call relations**: This function is invoked when the agent uses the search_vertical tool. It follows the same broad path as _search_web, but for one specialized query: get the provider, ask it to search, record the hits, and use _results_json to shape the final reply.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).

## 📊 State Registers Touched

- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-tool-catalog` — The current list of tools the agent may call, with their names, inputs, permissions, and implementations.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-artifact-blob-store` — The shared file storage for generated artifacts, downloads, document previews, screenshots, and other saved output bytes.
- `reg-search-indexes` — The shared searchable indexes and embeddings that let turns, research tools, and memory lookup find relevant text.
- `reg-hosted-site-store` — Saved hosted-site records, published bindings, homepage mappings, build metadata, and site preview state used by public routes and site tools.
- `reg-research-reference-store` — Conversation-scoped research findings, cited links, fetched-page metadata, and source-panel references created by browser or research workflows.
- `reg-turn-created-reference-store` — Saved references created by a turn, linking its work to newly produced artifacts, objects, sources, sites, or other records for later display, replay, and cleanup.
- `reg-content-provenance-trust-labels` — Visibility, provenance, and trust labels attached to messages, source content, and external text so prompt construction and policy checks can separate trusted instructions from untrusted content.
