# Research and web search tools  `stage-11.2`

This stage is the system’s research desk. It is shared support used when an agent needs outside information during its work: searching the web, opening pages, looking in special areas like images or academic papers, and keeping track of sources for later citation.

At the center is `core/src/ufo/runtime/search.py`, which defines the common shape of a search request and a page-fetch request. This lets the rest of the system ask for information without caring which search company is actually used. `extensions/perplexity/ufo_ext_perplexity.py` is one bridge for that contract. It translates the system’s requests into Perplexity API calls, checks the answers, and converts them back into the project’s normal result format.

`extensions/research/ufo_ext_research/tools.py` exposes these abilities as tools an agent can safely use, while hiding service credentials. `delegation.py` adds a bigger “wide research” tool, like sending several assistants to research many subjects at once, then saving their combined results and progress. `observations.py` records the sources found, tied to the current workspace and conversation turn. `__init__.py` simply makes the research extension importable.

## Files in this stage

### Perplexity provider bridge
Perplexity is plugged in as an external search and page-fetching provider that returns results in the project’s standard format.

### `extensions/perplexity/ufo_ext_perplexity.py`

`io_transport` · `request handling and extension registration`

This file is the bridge between the project and Perplexity’s hosted search API. Without it, the rest of the system could ask for “search the web” or “fetch this page,” but it would not know how to speak Perplexity’s particular language, add the API key, or understand the response.

The main piece is `PerplexitySearchProvider`. Think of it like a travel adapter: the project has its own shape for search queries and fetched pages, while Perplexity expects a specific web request and returns a specific JSON reply. This provider converts between the two.

For searches, it builds a safe request body, adds optional filters like domains or date ranges, sends the request, validates the reply, and returns a list of `SearchHit` objects. For fetching a page, it asks Perplexity to search only within the requested page’s domain, then checks that the returned result really matches the requested URL. It trims the text to the requested size and can treat the result as a summary when an extraction prompt was supplied.

The file also defines limits, such as maximum query length and URL length, so bad or overly large requests fail early with a clear `PerplexityError`. Finally, `manifest()` announces this extension to the host system and declares the Perplexity API key credential it needs.

#### Function details

##### `PerplexitySearchProvider.search`  (lines 72–85)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Perplexity and returns the results in the project’s normal search-result shape. A caller uses this when it wants search hits without caring about Perplexity’s raw API format.

**Data flow**: It receives a `SearchQuery`, which contains the search text and optional filters. It turns that query into a Perplexity request body, sends it over the network, validates the returned data, then converts each Perplexity result into a `SearchHit`. The final output is a `SearchResults` object containing those hits.

**Call relations**: This is one of the provider’s public actions. It relies on `_search_body` to prepare the request, `_post` to contact Perplexity, and `_response` to make sure the reply has the expected shape before wrapping the results for the rest of the system.

*Call graph*: calls 3 internal fn (_post, _response, _search_body); 2 external calls (__init__, __init__).


##### `PerplexitySearchProvider.fetch`  (lines 87–131)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Extracts text for one specific web page URL using Perplexity. It is used when the system wants content from a known page, not just a general list of search results.

**Data flow**: It receives a `FetchRequest` with a URL, an optional extraction prompt, and an optional character limit. It first checks that the URL, prompt, and limits are reasonable. It then builds a Perplexity search restricted to the URL’s domain, sends it, validates the reply, and looks for a result whose canonical page matches the requested URL. If found, it trims the snippet to the allowed length and returns a `FetchedPage`; if not, it raises a `PerplexityError`.

**Call relations**: This is the provider’s public fetch path. It uses `_post` for the Perplexity call, `_response` for validation, and `_canonical_page` to compare URLs in a forgiving way, such as ignoring a trailing `.html` difference. It creates the final `FetchedPage` only after confirming Perplexity returned the requested page.

*Call graph*: calls 3 internal fn (_post, _response, _canonical_page); 3 external calls (__init__, __init__, urlsplit).


##### `PerplexitySearchProvider._search_body`  (lines 134–159)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON body that Perplexity expects for a search request. It also enforces search limits before any network call is made.

**Data flow**: It receives a `SearchQuery`. It checks the requested result count, caps it at the provider’s maximum, adds a plain-language qualifier for some search types, includes optional domain and date filters, and calculates token limits. It returns a dictionary ready to send as JSON to Perplexity, or raises `PerplexityError` if the query is too small or too long.

**Call relations**: This helper is called by `PerplexitySearchProvider.search` before contacting Perplexity. When date filters are present, it hands each date to `_api_date` so the date is formatted the way Perplexity’s API expects.

*Call graph*: calls 1 internal fn (_api_date); called by 1 (search); 1 external calls (__init__).


##### `PerplexitySearchProvider._response`  (lines 162–166)

```
def _response(payload: object) -> _PerplexitySearchResponse
```

**Purpose**: Checks that Perplexity’s reply has the result structure this provider needs. It protects the rest of the system from malformed or unexpected API responses.

**Data flow**: It receives the decoded response payload, usually a Python object made from JSON. It asks the `_PerplexitySearchResponse` data model to validate that the payload contains a `results` list with usable items. If validation succeeds, it returns the typed response object; if validation fails, it raises `PerplexityError`.

**Call relations**: Both `search` and `fetch` call this after `_post` returns data. It acts as the gate between outside API data and the project’s own trusted objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `PerplexitySearchProvider._post`  (lines 168–188)

```
async def _post(self, body: dict[str, Json]) -> object
```

**Purpose**: Sends a prepared request body to Perplexity’s `/search` endpoint and returns the decoded JSON reply. It is the one place in this file that actually performs the HTTP network call.

**Data flow**: It receives a dictionary that will be sent as JSON. It reads the Perplexity API key from the configured credential slot, creates an asynchronous HTTP client, sends a POST request with the API key in the authorization header, and checks the HTTP status. On success it returns the parsed JSON body; on an HTTP error or invalid JSON it raises `PerplexityError` with a clear message.

**Call relations**: `search` and `fetch` both hand their prepared request bodies to this function. It is the shared transport layer for the provider, while the higher-level methods decide what the request should mean and how to interpret the validated results.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_api_date`  (lines 191–192)

```
def _api_date(value: date) -> str
```

**Purpose**: Formats a Python date into the month/day/year text format expected by Perplexity’s API. It is a small compatibility helper.

**Data flow**: It receives a `date` value. It converts it into a string like `03/09/2026` and returns that string. It does not change anything else.

**Call relations**: `PerplexitySearchProvider._search_body` calls this when a search query includes start or end publication dates. That lets the search body include date filters in Perplexity’s preferred format.

*Call graph*: called by 1 (_search_body); 1 external calls (strftime).


##### `_canonical_page`  (lines 195–202)

```
def _canonical_page(value: str) -> tuple[str | None, str, str]
```

**Purpose**: Turns a URL into a simplified identity that can be compared with another URL. This helps decide whether Perplexity really returned the exact page that was requested.

**Data flow**: It receives a URL string. It splits the URL into parts, removes a trailing slash, and strips common page suffixes like `.html`, `.htm`, or `.txt` from the path. It returns a tuple containing the hostname, simplified path, and query string.

**Call relations**: `PerplexitySearchProvider.fetch` uses this on both the requested URL and Perplexity’s returned URLs. This makes the comparison a little more tolerant while still checking that the domain, page path, and query match.

*Call graph*: called by 1 (fetch); 1 external calls (urlsplit).


##### `manifest`  (lines 205–225)

```
def manifest() -> Manifest
```

**Purpose**: Registers this extension with the host system. It tells the system that there is a Perplexity search provider and that it needs a Perplexity API key credential.

**Data flow**: It takes no input. It creates a `Manifest` containing the extension name and version, declares the credential slot for the API key, and describes how to build a `PerplexitySearchProvider` when credentials are available. It returns that manifest to the extension loader.

**Call relations**: The host calls this during extension discovery or startup. The returned manifest is how the rest of the project learns that the `perplexity` backend exists and how to construct it.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Agent search tools and contract
Agent-facing research tools expose web, page, and category searches through the shared runtime search abstraction.

### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `request handling`

This file is the public face of the research extension. It defines three tools: one for normal web search, one for fetching the contents of a URL, and one for searching a specific kind of content such as videos, products, or academic papers. Think of it like a library reference desk: the agent asks a clear question, this file checks that the request is shaped correctly, then passes it to the search provider that actually knows how to look things up.

The input classes describe what each tool accepts and put guardrails around it. For example, web search can take up to five genuinely different queries, a result limit, publication-date filters, and allowed domains. Fetching requires a public HTTP or HTTPS URL and can optionally ask the provider to extract or summarize specific information.

The tool functions then call the current turn’s `SearchProvider`, which is the backend search service chosen by the host process. That matters because API keys stay on the host side; the sandboxed agent never receives them. Search results are converted into JSON text for the model to read, and observations can be recorded for later inspection. URL fetching also adds a clear warning: fetched pages come from the provider’s crawler session, not from the user’s workspace or login session. Without this file, the agent would not have a safe, consistent way to ask for current web information.

#### Function details

##### `_provider`  (lines 133–136)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function finds the search provider for the current tool call. If no provider was configured for this turn, it stops immediately with a clear error instead of pretending search is available.

**Data flow**: It receives the tool context, reads the `search_provider` value from it, and either returns that provider or raises an error. Nothing is changed; it is a gatekeeper that turns “maybe there is a provider” into “there definitely is one, or this call fails loudly.”

**Call relations**: The three tool handlers call this first, before doing any search or fetch work. That way `_search_web`, `_search_vertical`, and `_fetch_url` all share the same check and do not each need their own version of the missing-provider error.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 139–153)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search hits into a JSON string the model can read. It gives every result the same simple shape: URL, title, text snippet, publication date, and highlights, with an optional direct answer if the provider supplied one.

**Data flow**: It receives a list of search hits and maybe an answer. It copies the useful fields from each hit into plain dictionary objects, adds them under a `results` key, adds `answer` only when one exists, and returns the whole package as JSON text.

**Call relations**: After `_search_web` or `_search_vertical` gets raw provider results, they hand those results to this helper. The helper does the final packaging so both search tools return the same style of response.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 156–174)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_web` tool. It runs one web search for each requested query, combines the results, records them if observation logging is available, and returns them to the model.

**Data flow**: It receives the tool context and validated web-search arguments. It gets the provider, builds a search request for each query using the requested result count, date filters, and allowed domains, then collects all returned hits into one list. It keeps the first provider-supplied answer it sees, optionally records the hits for the current conversation turn, converts everything to JSON text, and returns a tool result.

**Call relations**: When the agent uses the web search tool, the tool system calls this function. It relies on `_provider` to obtain the configured backend, uses `_results_json` to format the final response, and calls the observation recorder when extension state is present so the search activity can be saved outside the immediate reply.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 177–198)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It asks the search provider’s crawler to retrieve a public web page, then returns the page text, any summary, and a warning explaining that the content came from the crawler’s session, not the user’s workspace.

**Data flow**: It receives the tool context and validated fetch arguments. It gets the provider and first checks whether that provider can fetch pages at all. If not, it returns an error message telling the agent to use another route. If fetching is supported, it builds a fetch request from the URL, optional prompt, length limit, and cache-bypass flag, sends it to the provider, optionally records the fetched page, wraps the page content and provenance warning into JSON, and returns it as tool output.

**Call relations**: When the agent calls `fetch_url`, this function is the bridge to the provider’s crawler. It starts with `_provider`, hands a fetch request to the backend, may pass the fetched page to `record_fetched_page`, and then constructs the final `ToolResult` for the model.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 201–210)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It performs a search aimed at a specific content type, such as images, people profiles, academic papers, videos, or shopping results.

**Data flow**: It receives the tool context and the validated vertical-search arguments. It gets the provider, builds a search query using the requested vertical and the default number of results, sends it to the provider, optionally records the hits for the current turn, formats the hits and optional answer as JSON, and returns that JSON in a tool result.

**Call relations**: The tool system calls this when the agent chooses a specialized search instead of a general web search. Like `_search_web`, it uses `_provider` to reach the backend, uses `_results_json` to make the response consistent, and records hits when the extension observation system is available.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


### `core/src/ufo/runtime/search.py`

`data_model` · `startup and request handling`

This file is a boundary, or “seam,” between the core runtime and any real web search provider. The core project does not include a built-in search engine here. Instead, it defines the shapes of the messages that pass back and forth, and the promises a search provider must keep.

The small data classes describe the pieces of a search workflow. A SearchQuery is the question being asked, including limits such as date range, allowed websites, result count, and optional category. SearchResults contains ranked SearchHit entries, and may also include a direct answer from a provider that can summarize results. FetchRequest describes asking for the contents of one web page, and FetchedPage is the extracted text that comes back.

The SearchProvider protocol is the important doorway. A real provider, supplied by an extension, must offer search. It may also offer fetch, which is advertised through supports_fetch. This matters because API keys and network calls stay in the host process, not inside the sandboxed tool environment. In everyday terms, this file is like defining the plug shape for search providers: many devices can fit, but the wall socket stays the same.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells the rest of the system whether this search provider can fetch and extract the contents of a specific web page. Code can check it before trying to call fetch, instead of guessing and failing later.

**Data flow**: The caller starts with a SearchProvider object. It reads this property and gets back a true-or-false answer. Nothing is changed; the result simply tells the caller whether page fetching is available.

**Call relations**: This file only defines the promise, not the real behavior. In the larger flow, a research tool or other caller checks supports_fetch before asking the provider to fetch a URL, so providers that only support search are not asked to do work they cannot do.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This method is the standard way to ask a provider to run a web search. It takes a structured SearchQuery and returns SearchResults, so callers do not need to know the provider’s private API format.

**Data flow**: The caller provides a SearchQuery containing the natural-language query and optional limits like dates, domains, result count, or category. The provider implementation sends that request to its own backend, translates the backend response into SearchResults, and returns ranked hits plus an optional direct answer.

**Call relations**: This protocol method is called through the selected provider during a turn when a tool needs web search. The real provider implementation does the network work and hands back SearchResults in the common shape defined in this file.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This method is the standard way to retrieve readable text from one web page, when the selected provider supports that feature. It can also request a focused extraction or summary using an optional prompt.

**Data flow**: The caller provides a FetchRequest with a URL and optional settings such as a prompt, maximum returned text length, and whether to bypass cached data. The provider implementation fetches or retrieves the page, extracts text, possibly summarizes it, and returns a FetchedPage.

**Call relations**: This method is meant to be called only after supports_fetch says fetching is available. In the larger research flow, a fetch tool uses the selected provider through this interface, while the actual provider takes care of its own API calls and credentials outside the sandbox.


### Research package setup
The research extension package is made importable before its orchestration and observation helpers are used.

### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as an importable package. This file is that marker for the `extensions/research/ufo_ext_research` package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label simply tells Python, “this drawer belongs to the project and can be opened by imports.” Because the file is empty, it does not set up shared state, expose shortcuts, or run startup code. Its importance is structural: without it, depending on the Python version and import style, code elsewhere might not be able to reliably import modules from this package.


### Research orchestration and source records
Wide research coordinates concurrent subagent investigations and records discovered web sources for later citation.

### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `tool request handling`

This file solves a practical batching problem: if a user wants the same kind of research done for many companies, people, or topics, doing them one by one is slow and fragile. `wide_research` acts like a foreman. It reads an input file with one entity per line, removes duplicates, then sends each entity to a separate research subagent with a customized prompt. It limits how many subagents run at once, so the system does not try to do too much work at the same time.

Each child is asked to write its full JSON result to a hidden workspace file. The parent then reads those files, turns each into a row, and writes a final `wide_research.json` summary. If a child fails to write valid JSON, the final output records an error for that entity instead of losing the whole batch.

A key detail is recovery. Because this tool changes workspace state, it uses a stable idempotency key, meaning the same call can be safely retried. It writes a recovery file as rows finish, so after a crash or retry, completed entities can be reused and only unfinished work needs to continue. Temporary child result files are registered for cleanup.

#### Function details

##### `_read_lines`  (lines 59–72)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads the user-provided entity list from the sandbox workspace and turns it into a clean list of unique, non-empty entries. Someone uses this so the rest of the tool can work with simple entity names instead of raw file text.

**Data flow**: It receives the tool context and a file path. It asks the sandbox shell to `cat` that path, safely quotes the path so shell metacharacters are treated as text, then splits the file into lines, trims whitespace, skips blanks, and removes duplicates while preserving first-seen order. It returns the cleaned list, or raises an error if the file cannot be read.

**Call relations**: `_wide_research` calls this near the start, before any subagents are created. The cleaned list it returns becomes the work queue that `_WideResearch.run` later fans out across child research tasks.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_WideResearch.run`  (lines 91–101)

```
async def run(self) -> ToolResult
```

**Purpose**: Runs the full batch after setup is complete. It starts one visit task per entity, collects all rows, writes both the recovery file and the final output file, and returns a tool response pointing to the result.

**Data flow**: It starts with a prepared `_WideResearch` object containing the context, entities, file paths, schema text, and recovery state. It registers cleanup for temporary child result files, runs `_visit` for every entity concurrently, builds a `WideResearchFile` from the returned rows, saves a recovery copy, writes `wide_research.json`, and returns a `ToolResult` containing the JSON summary plus the output filename.

**Call relations**: `_wide_research` creates the `_WideResearch` instance and calls `run` as the final step. Inside the run, it calls `_visit` for each entity and `_install_recovery` after all rows are gathered, then wraps the result in the SDK response objects.

*Call graph*: calls 2 internal fn (_install_recovery, _visit); 5 external calls (__init__, __init__, __init__, gather, dumps).


##### `_WideResearch._remove_result_files`  (lines 103–110)

```
async def _remove_result_files(self) -> None
```

**Purpose**: Deletes the hidden per-entity result files that child research agents wrote. This keeps the workspace from accumulating temporary files after the batch is done.

**Data flow**: It reads the set of persisted result paths from the `_WideResearch` object. If there are none, it does nothing. Otherwise it safely quotes each path, runs a sandbox shell command to remove them, and raises an error if deletion fails.

**Call relations**: `run` registers this function with the context cleanup system, so it is called later by the tool framework during cleanup rather than as part of the main result-building path. It complements `_visit`, which creates and records those child result paths as rows finish.

*Call graph*: 1 external calls (quote).


##### `_WideResearch._install_recovery`  (lines 112–122)

```
async def _install_recovery(self, rows: tuple[WideResearchRow, ...]) -> None
```

**Purpose**: Writes the current aggregate progress to the recovery file in the workspace. This is what lets a later retry reuse completed rows instead of starting the whole batch over.

**Data flow**: It receives a tuple of completed rows. It wraps them in a `WideResearchFile`, serializes that to nicely formatted JSON, writes it to a temporary staging path, then moves the staging file into the real recovery path. The move makes the update act like replacing the old recovery snapshot with a complete new one.

**Call relations**: `_save_row` calls this each time an entity finishes, so progress is saved incrementally. `run` also calls it after all visits complete, ensuring the final recovery file matches the final row set.

*Call graph*: called by 2 (_save_row, run); 3 external calls (__init__, dumps, quote).


##### `_WideResearch._save_row`  (lines 124–134)

```
async def _save_row(self, row: WideResearchRow) -> None
```

**Purpose**: Records one entity's finished row and immediately saves the updated batch progress. It prevents two concurrent entity tasks from writing the recovery file at the same time.

**Data flow**: It receives a `WideResearchRow` containing either a result or an error. It takes an async lock, adds the row to the in-memory completed-row map, rebuilds the completed rows in the original entity order, writes that snapshot through `_install_recovery`, and records the child result file path for later cleanup.

**Call relations**: `_visit` calls this whenever an entity has reached a final state. Because many `_visit` calls run in parallel, `_save_row` is the safe checkpointing doorway that serializes progress updates before handing file writing to `_install_recovery`.

*Call graph*: calls 1 internal fn (_install_recovery); called by 1 (_visit).


##### `_WideResearch._visit`  (lines 136–183)

```
async def _visit(self, entity: str) -> WideResearchRow
```

**Purpose**: Performs the research workflow for one entity. It either reuses a recovered row, or asks a research subagent to do the work, reads the JSON the child wrote, and turns it into a row for the final output.

**Data flow**: It receives an entity name from `run`. It waits for the shared semaphore, which is a counter that limits how many visits run at once. If the entity was recovered from a previous attempt, it returns that saved row. Otherwise it builds a deterministic child id, fills `{entity}` into the prompt template, adds instructions to write JSON to a known result path, and spawns the research profile. After the child finishes, it reads the result file. Valid JSON becomes a success row; a missing file or invalid JSON becomes an error row. In either case it saves the row and returns it.

**Call relations**: `run` starts `_visit` for each entity through `asyncio.gather`, so many visits happen in parallel. `_visit` hands completed or failed rows to `_save_row`, and it uses the research subagent output only to add helpful error context when the expected result file cannot be read.

*Call graph*: calls 1 internal fn (_save_row); called by 1 (run); 4 external calls (__init__, model_validate, loads, quote).


##### `_wide_research`  (lines 186–241)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the tool handler called by the SDK when someone invokes `wide_research`. It validates and prepares everything the batch runner needs, including entity lists, recovery state, schema text, concurrency limits, and result file paths.

**Data flow**: It receives the tool context and validated input arguments. It checks that an idempotency key exists, reads and limits the entity list, hashes the call key into a stable call id, removes stale recovery files from older turns, tries to load a matching recovery file for this call, reads the optional output schema file, creates a semaphore for bounded parallel work, builds deterministic hidden result paths for each entity, and constructs `_WideResearch`. It then calls `run` and returns that result.

**Call relations**: The `WIDE_RESEARCH_TOOL` definition points to `_wide_research` as its handler, so this function is the bridge from the external tool call into the internal batch machinery. It calls `_read_lines` for input cleanup, builds the `_WideResearch` worker object, and then delegates the actual fan-out and aggregation to `_WideResearch.run`.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, Lock, Semaphore, sha256, loads, quote).


### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `during research result recording and conversation source display`

When the research extension searches the web or fetches a page, the system needs a durable memory of what sources were used. Without this file, those links, titles, snippets, and dates would disappear after the immediate search result was processed, and the conversation could not later show a reliable “Sources” panel.

The file defines a database table for source observations. Each saved source belongs to one workspace and one conversation, and it is identified by a digest, which is a short fixed-length fingerprint made from the URL. That lets the code update the same source if it appears again instead of creating duplicates.

Before saving, the code trims long titles, snippets, and dates to safe sizes and validates them as conversation sources. Invalid source records are skipped rather than breaking the whole save. It also keeps only the newest 100 sources per conversation, like keeping the most recent pages in a neat reading list and throwing away older overflow.

The file also exposes a conversation slot provider named “Sources”. A slot provider is a small plug-in point that tells the wider app how to summarize and read a piece of conversation-related content. Here, it can report how many sources exist and return the actual source list for display.

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: This small helper shortens a string to a maximum allowed length. It is used before saving source details so overly long titles, snippets, or dates do not get stored or shown in full.

**Data flow**: It receives some text and a numeric limit. It returns the same text cut off at that many characters, leaving shorter text unchanged.

**Call relations**: When record_sources prepares source data for storage, it calls _bounded to make each user-facing field fit the expected size before validation and saving.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: This is the main saving routine for retrieved sources. It validates and stores sources for a conversation, updates existing records for repeated URLs, and trims the stored list so only the most recent sources are kept.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and a tuple of retrieved sources. If the tuple is empty, it does nothing. Otherwise it opens a database transaction, trims and validates each source, creates a URL fingerprint with SHA-256, inserts or updates the database row, then deletes older rows beyond the source limit. Its visible result is no returned value; the lasting effect is updated source records in the database.

**Call relations**: record_search_hits and record_fetched_page both hand normalized source information to this function. Inside, it uses the extension context to open a transaction, uses _bounded to keep text fields safe, builds validated ConversationSource objects, and uses SQLAlchemy select and delete statements to keep the database list current and limited.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: This function saves sources that came from search results. It converts search-hit objects into the simpler RetrievedSource shape used by the rest of this file.

**Data flow**: It receives the extension context, conversation ID, turn ID, and search hits. For each hit, it takes the URL, title, result text, and published date, wraps them as RetrievedSource objects, and passes the full tuple onward. It returns nothing; the database changes happen through record_sources.

**Call relations**: This is the bridge from the search subsystem to the source-observation store. When search results are available, it packages them and calls record_sources, which performs validation, database writing, and cleanup.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: This function saves a source when the research tool fetched a specific web page. It turns that page into a single RetrievedSource so it can be recorded just like a search result.

**Data flow**: It receives the extension context, conversation ID, turn ID, and fetched page. It uses the page URL as both the URL and title, chooses the page summary if present or the full text otherwise as the snippet, leaves the published date empty, and sends that one source to record_sources. It returns nothing; the lasting effect is the saved page source.

**Call relations**: This is the bridge from page fetching to the shared source-saving path. After creating one RetrievedSource, it calls record_sources so fetched pages and search hits are stored with the same validation and deduplication rules.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function counts how many saved sources a conversation has, up to the display limit. It is used to summarize the “Sources” slot without loading every source record.

**Data flow**: It receives a conversation slot context, which includes the extension context and conversation ID. It opens a database transaction, counts matching source rows for the current workspace and conversation, and returns either no count if there are none or the count capped at the source limit.

**Call relations**: The SOURCES_SLOT provider uses this function when the wider conversation system asks for a short summary of the Sources slot. It reads from the same table populated by record_sources.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: This function reads the saved sources for display in the conversation’s “Sources” slot. It returns source objects in newest-first order and notes whether there were more than the allowed display limit.

**Data flow**: It receives a conversation slot context. It opens a database transaction, selects source rows for the current workspace and conversation, orders them by most recently updated and then by rank, and reads one extra row beyond the limit to detect overflow. It turns the rows into ConversationSource objects and returns a SourcesSlotPayload containing the visible sources plus a truncated flag.

**Call relations**: The SOURCES_SLOT provider uses this function when the wider conversation system needs the full Sources content. It reads records written by record_sources and packages them into the payload type expected by the conversation UI or API.

*Call graph*: 3 external calls (__init__, __init__, select).
