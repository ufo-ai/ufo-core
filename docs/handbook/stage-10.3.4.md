# Cloud Browser and Web Research Providers  `stage-10.3.4`

This stage is shared behind-the-scenes support for agents that need the web. It does not run the main reasoning loop itself. Instead, it gives that loop safe “tool handles” for outside services, much like adding a phone book and a remote-controlled browser to a workspace.

The Browser Use extension connects the project to a hosted cloud browser. Through its browser_task and wide_browse tools, the system can ask an external service to open sites, click around, and gather results without launching a local browser on the user’s machine.

The Perplexity extension connects to Perplexity as a search and page-reading service. With a user-provided API key, it can run web searches or pull readable text from a specific web page.

The research package marker simply makes the research extension importable by Python. The research tools file is the main switchboard. It defines tools for web search, page fetching, and specialized searches like images or academic papers, then routes each request to the configured backend.

## Files in this stage

### Hosted Browser Automation
Cloud browser-task tools expose hosted web automation without requiring a local browser runtime.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This file is the bridge between UFO and Browser Use’s hosted browser agent. Instead of opening and driving a browser inside this project, it sends a task to Browser Use’s web API, waits for the hosted agent to finish, and brings back the result. Think of it like hiring an outside courier: UFO writes clear instructions, the courier does the trip, and UFO records what came back.

The file defines two user-facing tools. `browser_task` runs one full browser session, starting from a URL and following the user’s instructions. `wide_browse` reads many URLs or site names from a workspace file, runs many smaller browser jobs in parallel, and saves the combined results as JSON.

The `HostedRun` class contains the shared run lifecycle: get the API key securely, create or reattach to a Browser Use run, poll until it finishes, cancel it if it exceeds the time limit, read the final result, and optionally download output files. Downloaded files are checked carefully so they cannot escape the workspace, and large or missing files are reported instead of silently ignored.

The file also declares the extension manifest: its name, version, tools, prompt text, and the credential slot for the Browser Use API key. Without this file, UFO would not know how to offer these Browser Use-backed browser tools.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one complete Browser Use job from start to finish. It creates or resumes the hosted run, waits for it, handles timeouts, collects the final message, and optionally saves output files into the workspace.

**Data flow**: It receives the tool context, the task text, a time limit, and an optional duplicate-prevention key. It reads the Browser Use API key from the extension credentials, opens an HTTP client, starts or reattaches to a run, watches the run status, cancels it if needed, fetches the summary, downloads allowed output files, and returns a `RunOutcome` containing the status, text result or error, saved files, skipped files, and whether more files existed.

**Call relations**: This is the main engine used by both browser tools. `_browser_task` and the per-entity work inside `_wide_browse.visit` create a `HostedRun` and call this method. During the run it delegates to `_start`, `_watch`, `_status`, `_json`, and `_collect` so each part of the hosted-job lifecycle stays separate.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Starts a Browser Use run, or reuses an already-started run when an idempotency key is available. This prevents paying for duplicate work when the same tool call is retried.

**Data flow**: It receives an HTTP client, the extension store, the task text, and an optional deduplication key. If the key already points to a stored run, it validates and returns that run handle. Otherwise it sends a `POST /runs` request with the task, model, cost limit, and proxy country, extracts the returned run ID and workspace ID, stores them under the key if there is one, and returns a `StartedRun` record.

**Call relations**: `HostedRun.execute` calls this first after opening the API client. It uses `_json` to read the API response safely and `_text` to pull required string fields from that response.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Waits until a Browser Use run reaches a final status, such as completed, failed, stopped, or cancelled. It is the polling loop for the hosted job.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the current state, returns as soon as the state is terminal, and otherwise sleeps briefly before checking again.

**Call relations**: `HostedRun.execute` calls this while the run is inside its time budget. `_watch` relies on `_status` for each individual status read and uses a sleep between checks to avoid hammering the API.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run one time. It is also used right after a timeout to avoid cancelling a run that finished in the last few seconds.

**Data flow**: It receives an HTTP client and a run ID. It sends a `GET /runs/{id}/status` request, parses the JSON response, extracts the `status` string, and returns that status.

**Call relations**: `HostedRun._watch` calls this repeatedly during normal waiting. `HostedRun.execute` also calls it directly when a timeout occurs, so it can decide whether to cancel the remote run or accept the already-finished result.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies a completed run’s output files from Browser Use into the UFO workspace when file saving is enabled. It also reports files it could not fetch, rather than hiding them.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace ID. If output saving is disabled, it returns empty file lists. Otherwise it asks Browser Use for a limited file listing, checks that each listed item has a safe path and size, skips files that are too large or lack a usable URL, downloads acceptable files, writes them into the sandbox workspace, and returns saved files, skipped files, and whether more files were available than the listing limit.

**Call relations**: `HostedRun.execute` calls this after reading the run summary. `_collect` uses `_json` for the listing response, `contained_relative` to make sure paths stay inside the workspace, and `_download` to fetch each allowed file without sending the Browser Use API key to the file host.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a presigned HTTPS URL. It deliberately uses a plain client without the Browser Use API key, so the storage host does not receive UFO’s credential.

**Data flow**: It receives a URL string. It rejects non-HTTPS URLs, fetches the content with a short-timeout HTTP client, raises a clear `BrowserUseError` if the download fails, and returns the file bytes on success.

**Call relations**: `HostedRun._collect` calls this for each listed output file that is small enough and has a valid URL. It is the last step before `_collect` writes the bytes into the sandbox workspace.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a checked JSON object. It gives consistent, readable errors for bad status codes, invalid JSON, or JSON that is not an object.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises `BrowserUseError` with the path, status, and body. Otherwise it parses the body as JSON, confirms the result is a dictionary-like object, converts keys to strings, and returns that dictionary.

**Call relations**: This is the shared response reader for the Browser Use API path. `execute`, `_start`, `_status`, and `_collect` all call it before they trust data from the remote service.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Extracts one required string field from a parsed Browser Use response. It turns missing or wrongly typed fields into the same kind of clear Browser Use error used elsewhere.

**Data flow**: It receives a response dictionary and a field name. It looks up the field, checks that the value is a string, returns it if valid, and raises `BrowserUseError` if not.

**Call relations**: `HostedRun._start` uses this to read the created run’s ID and workspace ID. `HostedRun._status` uses it to read the current run status.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 344–378)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the `browser_task` tool: one hosted browser session for one user-described job. It packages the starting URL and task instructions, runs Browser Use, and returns a JSON summary to the caller.

**Data flow**: It receives the tool context and validated `BrowserTaskInput`. It builds a task string starting with the URL, creates a `HostedRun` using the higher-quality model and a larger cost limit, runs it with the requested timeout, and returns a `ToolResult`. On timeout it returns an error message; otherwise it returns JSON containing the result text, saved file paths, skipped file details, and whether additional files existed.

**Call relations**: This function is registered as the handler for the public `browser_task` tool in `BROWSER_USE_TOOLS`. It relies on `HostedRun.execute` for all communication with Browser Use and turns the outcome into the tool response format expected by UFO.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 381–388)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a file from the workspace through the sandbox. It quotes the path safely before passing it to the shell, because the path may come from a model or user.

**Data flow**: It receives the tool context and a path string. It runs `cat` inside the sandbox with the path shell-quoted, checks the exit code, raises a `ValueError` if reading failed, and returns the file contents as text on success.

**Call relations**: `_read_lines` uses this to load the entity list for batch browsing. `_wide_browse` also uses it directly to load the JSON schema text.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 391–399)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace file as a clean list of unique non-empty lines. This is used to turn a user-provided list of sites or URLs into batch jobs.

**Data flow**: It receives the tool context and a path. It reads the file through `_read_file`, splits the text into lines, trims spaces, ignores blank lines, removes duplicates while keeping first-seen order, and returns the resulting list.

**Call relations**: `_wide_browse` calls this at the start to get the entities it will visit. `_read_lines` delegates the actual sandbox file access to `_read_file`.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 402–448)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the `wide_browse` tool: many hosted browser visits run as a controlled batch. It is useful when the caller wants the same extraction task applied to many sites or URLs.

**Data flow**: It receives the tool context and validated `WideBrowseInput`. It reads and de-duplicates entities from a file, enforces the maximum entity count, reads the requested output schema, creates a cheaper `HostedRun` runner that does not save per-run files, and launches per-entity visits with limited parallelism. It gathers all rows, turns individual failures into error rows, writes the combined JSON to `wide_browse.json`, and returns both the rows and the output filename.

**Call relations**: This function is registered as the handler for the public `wide_browse` tool. It uses `_read_lines` and `_read_file` for workspace inputs, creates the shared `HostedRun`, and uses its nested `visit` function as the per-entity worker passed into `asyncio.gather`.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 418–433)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs Browser Use for one entity inside a `wide_browse` batch. A failure here becomes that entity’s row, so one bad site or vendor hiccup does not throw away the whole batch.

**Data flow**: It receives one entity string from the outer `_wide_browse` function. It waits for the semaphore so only a limited number of browser jobs run at once, fills `{entity}` into the prompt template, appends the output schema if present, calls `HostedRun.execute` with a per-entity deduplication key, and returns a row containing the entity, run status, and result text.

**Call relations**: `_wide_browse` creates this nested helper and starts one copy for each entity through `asyncio.gather`. Each copy shares the same `HostedRun` configuration and semaphore from the outer function.


##### `manifest`  (lines 471–486)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO runtime. It tells the system the extension name and version, which tools it provides, what prompt section to add, and which credential it needs.

**Data flow**: It takes no inputs. It builds and returns a `Manifest` containing the two Browser Use-backed tool definitions, the browser prompt section loaded from the nearby markdown file, and a credential slot for the Browser Use API key.

**Call relations**: The extension loader calls this at startup or extension registration time. The returned manifest is how the rest of the system discovers `browser_task`, `wide_browse`, and the required API key.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Perplexity Web Access
The Perplexity provider supplies web search and page extraction through a user-provided API key.

### `extensions/perplexity/ufo_ext_perplexity.py`

`io_transport` · `plugin registration and search/fetch request handling`

This file is the bridge between the project’s general search interface and Perplexity’s hosted search API. Without it, code that asks for the “perplexity” search backend would have no way to turn a search request into an actual Perplexity web request, and no way to turn Perplexity’s reply back into the project’s normal search result objects.

The main class, PerplexitySearchProvider, has two public jobs. Its search method takes a normal SearchQuery, checks and reshapes it into the format Perplexity expects, sends it over HTTPS, validates the reply, and returns SearchResults made of SearchHit entries. Its fetch method is a more focused tool: given one exact URL, it asks Perplexity for information limited to that page’s domain, then checks whether the returned results really include the requested page before returning a FetchedPage.

The file is careful about limits. It caps query length, URL length, result count, token use, fetched text size, and error message size. These checks protect both the API and the caller from oversized requests or surprising costs. It also normalizes page URLs for fetch matching, so small differences like a trailing slash or “.html” suffix do not cause a missed match.

At the bottom, manifest registers the extension: it declares the needed Perplexity API key and tells the host how to build this provider.

#### Function details

##### `PerplexitySearchProvider.search`  (lines 72–85)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Perplexity and returns the results in the project’s standard search format. A caller uses this when it wants ordinary search hits such as URLs, titles, snippets, and publication dates.

**Data flow**: It receives a SearchQuery from the rest of the system. It first turns that query into a Perplexity request body, sends the body to Perplexity, checks that the reply has the expected shape, then converts each returned item into a SearchHit. The final output is a SearchResults object containing those hits.

**Call relations**: This is the main search entry on the provider. It relies on _search_body to translate the project’s query into Perplexity’s language, _post to do the network call, and _response to validate the reply before it creates the project-level result objects.

*Call graph*: calls 3 internal fn (_post, _response, _search_body); 2 external calls (__init__, __init__).


##### `PerplexitySearchProvider.fetch`  (lines 87–131)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Extracts text for one requested web page using Perplexity. It is stricter than a normal search because it must prove that Perplexity returned the exact page the caller asked for.

**Data flow**: It receives a FetchRequest containing a URL, an optional extraction prompt, and an optional maximum character count. It rejects invalid or oversized input, finds the URL’s domain, builds a Perplexity request limited to that domain, sends it, validates the response, and searches the returned items for the requested page after normalizing the URLs. If it finds the page, it trims the snippet to the requested size and returns a FetchedPage; if not, it raises a PerplexityError.

**Call relations**: This is the provider’s page-fetch path. It uses urlsplit to inspect the URL, _post to contact Perplexity, _response to validate the reply, and _canonical_page to compare URLs in a forgiving but controlled way before creating the final FetchedPage.

*Call graph*: calls 3 internal fn (_post, _response, _canonical_page); 3 external calls (__init__, __init__, urlsplit).


##### `PerplexitySearchProvider._search_body`  (lines 134–159)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON body that Perplexity expects for a search request. It is the translator between the project’s SearchQuery object and Perplexity’s API fields.

**Data flow**: It receives a SearchQuery. It checks that at least one result was requested, caps the result count, adds extra words for certain search categories like academic or shopping, applies domain and date filters when present, and calculates safe token limits. It returns a plain dictionary ready to send as JSON, or raises PerplexityError if the query is too small or too long.

**Call relations**: The search method calls this before making the network request. When the query has date filters, this helper hands each date to _api_date so Perplexity receives dates in the format it expects.

*Call graph*: calls 1 internal fn (_api_date); called by 1 (search); 1 external calls (__init__).


##### `PerplexitySearchProvider._response`  (lines 162–166)

```
def _response(payload: object) -> _PerplexitySearchResponse
```

**Purpose**: Checks that Perplexity’s reply looks like a valid search response. This keeps malformed or unexpected API data from leaking into the rest of the project.

**Data flow**: It receives raw data decoded from JSON. It asks the Pydantic model, a validation tool that checks data shapes, to confirm that the data contains a results list with URL, title, and snippet fields. It returns a typed _PerplexitySearchResponse when valid, or raises PerplexityError when the data does not match.

**Call relations**: Both search and fetch call this immediately after _post returns. It acts like a border checkpoint between external API data and the project’s internal objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `PerplexitySearchProvider._post`  (lines 168–188)

```
async def _post(self, body: dict[str, Json]) -> object
```

**Purpose**: Sends one HTTPS POST request to Perplexity’s /search endpoint. It centralizes the API key, timeout, base URL, error handling, and JSON decoding in one place.

**Data flow**: It receives a dictionary body to send as JSON. It asks the credentials object for the Perplexity API key, opens an async HTTP client, posts the request with a Bearer authorization header, then checks the HTTP status. If Perplexity reports an error or returns non-JSON content, it raises PerplexityError; otherwise it returns the decoded JSON data.

**Call relations**: Both search and fetch depend on this for the actual network trip. It is the only method here that directly talks to Perplexity over HTTP, so the higher-level methods can focus on building requests and interpreting results.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_api_date`  (lines 191–192)

```
def _api_date(value: date) -> str
```

**Purpose**: Formats a Python date in the style Perplexity expects for date filters. It turns an internal date value into a month/day/year string.

**Data flow**: It receives a date object. It formats that date as text using the pattern MM/DD/YYYY. It returns that string without changing anything else.

**Call relations**: The _search_body helper calls this when a search query includes start or end publication dates, so those filters can be included in the outgoing Perplexity request.

*Call graph*: called by 1 (_search_body); 1 external calls (strftime).


##### `_canonical_page`  (lines 195–202)

```
def _canonical_page(value: str) -> tuple[str | None, str, str]
```

**Purpose**: Creates a simplified version of a URL for comparing whether two links point to the same page. It smooths over small differences such as trailing slashes and common page file suffixes.

**Data flow**: It receives a URL string. It breaks the URL into parts, removes a trailing slash from the path, strips one common suffix such as .html, .htm, or .txt, and returns a tuple containing the hostname, cleaned path, and query string. It does not fetch the URL or check whether it exists.

**Call relations**: The fetch method uses this on both the caller’s requested URL and Perplexity’s returned URLs. That lets fetch decide whether Perplexity really returned the requested page before it creates a FetchedPage.

*Call graph*: called by 1 (fetch); 1 external calls (urlsplit).


##### `manifest`  (lines 205–225)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host application. It says what the extension is called, what credential it needs, and how to create the Perplexity search provider.

**Data flow**: It takes no input. It builds a Manifest containing the extension name and version, a CredentialSlot for the user’s Perplexity API key, and a SearchProviderSpec that constructs PerplexitySearchProvider when the host asks for the Perplexity backend. It returns that Manifest to the extension loader.

**Call relations**: The host calls this during extension discovery or startup. The returned manifest is what connects the backend name “perplexity” to this provider class and tells the credential system that a Perplexity API key is required.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Research Tool Surface
The research package exposes agent-facing search and page-fetch tools backed by configured providers.

### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder with an `__init__.py` file is treated as an importable package, which means other code can refer to modules inside `extensions/research/ufo_ext_research` using normal Python import paths. Think of it like putting a label on a folder so Python knows the folder is part of the program, not just a random directory. Because the file is empty, it does not set up configuration, create objects, run startup logic, or expose helper functions. Its value is structural: without it, some Python environments or tooling may not recognize this directory as a package, and imports from this research extension could fail or behave inconsistently.


### `extensions/research/ufo_ext_research/tools.py`

`orchestration` · `request handling during research tool calls`

This file exists so the agent can safely ask for outside information without knowing how the search service is implemented or where its secret keys live. The actual search provider runs on the host side, so sensitive credentials are not exposed inside the agent’s workspace.

It defines three public tools. `search_web` accepts up to five clear search questions, sends each one to the selected search provider, merges the returned results, and gives the agent a JSON reply with URLs, titles, snippets, dates, and optional answer text. `fetch_url` asks the provider to retrieve a public HTTP or HTTPS page. If the provider cannot fetch pages, it returns a clear error telling the agent to use another route, such as browser tools or `curl`. When a page is fetched, the response includes an explicit warning that the page came through the provider’s crawler, not through the user’s own session. This matters because any login or account context in that content belongs to the crawler, not the workspace. `search_vertical` is for focused searches, such as images, videos, shopping, people, or academic papers.

The input classes use Pydantic, a validation library, to check tool arguments before the backend is called. The handlers also record search results and fetched pages when an extension context is available, so the system can keep observations for the current conversation turn.

#### Function details

##### `_provider`  (lines 133–136)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This helper finds the search provider configured for the current turn. If no provider exists, it stops immediately with a clear error, because the research tools cannot work without a backend to answer them.

**Data flow**: It receives the current tool context, checks whether that context contains a search provider, and returns it if present. If the context has no provider, it raises an error instead of letting later code fail in a confusing way.

**Call relations**: The three tool handlers call this first, before doing any search or fetch work. It acts like a front-desk check: `_search_web`, `_fetch_url`, and `_search_vertical` all ask it, “Which search service should I use for this turn?”

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 139–153)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This helper turns search results into the simple JSON text that the agent receives. It keeps the output shape consistent for both normal web search and vertical search.

**Data flow**: It receives a list of search hits and an optional answer string. It copies the useful fields from each hit, such as URL, title, snippet text, publication date, and highlights, then packages them into a JSON string. If an answer is available, it includes that too.

**Call relations**: `_search_web` and `_search_vertical` call this after the provider returns results. It is the final formatting step before those handlers wrap the JSON text in a tool result for the agent.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 156–174)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_web` tool. It lets the agent run one or more general web searches and receive a combined set of current, factual results.

**Data flow**: It receives the tool context and validated search arguments. It gets the configured provider, sends each query as a `SearchQuery` with options such as result count, date filters, and allowed domains, then gathers all returned hits into one list. If observation recording is available, it stores the hits for the conversation turn. Finally, it returns a tool result containing JSON text with the combined search results and the first available provider answer.

**Call relations**: When the agent calls `search_web`, this function drives the flow. It first asks `_provider` for the backend, then builds provider search requests, optionally records the results through `record_search_hits`, and hands the final list to `_results_json` so the agent gets a readable JSON response.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 177–198)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It asks the search provider to retrieve a public web page and return its content, optionally guided by a prompt.

**Data flow**: It receives the tool context and validated fetch arguments, including the URL, optional extraction prompt, optional maximum length, and whether to bypass cache. It gets the provider and first checks whether that provider supports fetching. If not, it returns an error message. If fetching is supported, it sends a fetch request, optionally records the fetched page, and returns JSON containing the page URL, text, optional summary, and a warning about crawler provenance.

**Call relations**: When the agent calls `fetch_url`, this function is the gatekeeper. It uses `_provider` to find the backend, creates the fetch request, records the fetched page through `record_fetched_page` when possible, and wraps the final JSON in a tool result. Its special unsupported-provider path prevents the agent from assuming every search backend can read full pages.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 201–210)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It performs a focused search in a specific content category, such as images, people, academic papers, videos, or shopping results.

**Data flow**: It receives the tool context and validated vertical-search arguments. It gets the configured provider, sends one search query with the requested vertical attached, records the returned hits if observation recording is available, and returns the results as JSON text.

**Call relations**: When the agent needs a specialized kind of result rather than ordinary web pages, this function runs the request. It relies on `_provider` to choose the backend, uses `record_search_hits` to save observations for the turn, and uses `_results_json` to produce the same output format as normal web search.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).
