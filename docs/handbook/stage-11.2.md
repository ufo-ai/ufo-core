# Research, search, recall, and knowledge lookup tools  `stage-11.2`

This stage is the system’s information desk. It does not change the workspace; it helps an agent look things up while it is working. Some tools search the public web, some fetch a specific page, and some recall stored memories from earlier user or conversation context.

The shared base is core/src/ufo/search.py. It defines a common “search and fetch” interface, meaning the rest of the code can ask for web results without caring which search company provides them. extensions/exa/ufo_ext_exa.py connects that interface to Exa, an external web search service. It uses an API key supplied by the workspace, while keeping that key away from the sandboxed agent code.

extensions/research/ufo_ext_research/tools.py is the toolbox the agent actually sees. It turns requests like “search the web,” “fetch this page,” or “find academic papers” into backend calls, then formats the results as readable text. core/src/ufo/memory.py does the same kind of unifying work for remembered information, so different memory stores can all be searched in one consistent way.

## Files in this stage

### Search provider integration
Exa is registered as an external web search and page-fetching backend for UFO research workflows.

### `extensions/exa/ufo_ext_exa.py`

`io_transport` · `request handling`

This file is the Exa-backed search engine for the project. Think of it as an adapter plug: the rest of the system asks for searches and page fetches in UFO's standard shape, and this file translates those requests into Exa's HTTP API format, then translates Exa's replies back into UFO's standard result objects.

It matters because the research tools do not talk to Exa directly. They go through a shared search-provider seam, which means a deployment can choose Exa as its backend without changing the tools themselves. The API key is read on the host side through `CredentialAccess`, which is a controlled way to access stored secrets. The comments make an important security point: the raw Exa key is not sent into the sandbox.

The main class, `ExaSearchProvider`, offers two actions: `search`, which calls Exa's `/search` endpoint, and `fetch`, which calls Exa's `/contents` endpoint to read one page. Helper methods build request bodies, check that responses contain a results list, and convert individual Exa items into UFO's `SearchHit` or `FetchedPage` objects. If Exa returns an error status or a malformed body, the file raises `ExaError` instead of pretending there were simply no results.

#### Function details

##### `ExaSearchProvider.search`  (lines 54–56)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs one web search through Exa and returns the results in UFO's standard search-results format. This is what the rest of the system calls when it wants search results without caring which search company is behind them.

**Data flow**: It receives a `SearchQuery`, which contains the search text and options such as result count, recency, domains, or vertical search type. It turns that query into an Exa request body, sends it to Exa, checks the returned payload for a usable results list, converts each result item into a `SearchHit`, and returns a `SearchResults` object.

**Call relations**: This is the public search entry point on the provider. During a research-tool turn, higher-level code calls it through the search-provider interface. It relies on `_search_body` to speak Exa's request language, `_post` to make the authenticated HTTP call, `_results` to validate the response, and `_hit` to translate each Exa result into UFO's internal form.

*Call graph*: calls 4 internal fn (_hit, _post, _search_body, _results); 1 external calls (__init__).


##### `ExaSearchProvider.fetch`  (lines 58–74)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches readable content for a single URL through Exa. It is used when the system already has a link and wants the page text, optionally with a summary focused on a prompt.

**Data flow**: It receives a `FetchRequest` containing a URL, an optional maximum character count, an optional prompt, and a force-refresh flag. It builds an Exa `/contents` request, caps the requested text length, optionally asks Exa for a summary, optionally requests a live crawl, sends the request, reads the first result if present, and returns a `FetchedPage` with URL, text, and optional summary.

**Call relations**: This is the provider's public page-fetch entry point, used by higher-level research flows after they decide a URL should be read. It hands the HTTP work to `_post`, asks `_results` to validate and extract Exa's result list, and uses `_opt_str` so optional summary data is only kept when it is truly text.

*Call graph*: calls 3 internal fn (_post, _opt_str, _results); 1 external calls (__init__).


##### `ExaSearchProvider._search_body`  (lines 77–91)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON body that Exa expects for a search request. It translates UFO's search options into Exa-specific fields.

**Data flow**: It receives a `SearchQuery`. For a normal search, it asks Exa for text snippets and highlights, may limit results to allowed domains, and may add a start date for recent-only searches. For a vertical search, such as academic or people search, it uses shorter text snippets and may set Exa's category field. It returns a dictionary ready to send as JSON.

**Call relations**: `search` calls this before making the HTTP request. It is the small translation step between UFO's search-query model and Exa's API vocabulary, including time calculations for recency filters such as day, week, or month.

*Call graph*: called by 1 (search); 2 external calls (now, timedelta).


##### `ExaSearchProvider._hit`  (lines 94–104)

```
def _hit(item: dict[str, object]) -> SearchHit
```

**Purpose**: Converts one Exa search-result item into UFO's `SearchHit` shape. This keeps the rest of the system from needing to know Exa's field names.

**Data flow**: It receives one dictionary from Exa's results list. It reads the URL, title, text, published date, and highlights, safely falling back to empty strings or empty highlight lists when fields are missing or not the expected type. It returns a `SearchHit` object.

**Call relations**: `search` calls this once for each valid result returned by `_results`. It uses `_opt_str` for the published date so non-text values are not passed along as if they were valid strings.

*Call graph*: calls 1 internal fn (_opt_str); called by 1 (search); 1 external calls (__init__).


##### `ExaSearchProvider._post`  (lines 106–114)

```
async def _post(self, path: str, body: dict[str, Json]) -> object
```

**Purpose**: Sends an authenticated POST request to Exa and returns the decoded JSON response. It is the one place in this file that reads the Exa API key and talks over the network.

**Data flow**: It receives an API path, such as `/search` or `/contents`, and a JSON-ready request body. It asks `CredentialAccess` for the Exa API key, opens an async HTTP client pointed at `api.exa.ai`, sends the request with the key in the `x-api-key` header, and returns the parsed JSON body. If Exa reports an HTTP error, it raises `ExaError` with the status and response text.

**Call relations**: Both `search` and `fetch` depend on this method for the actual Exa call. Tests can inject a custom HTTP transport through the provider so this method can be exercised without making real network requests.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_results`  (lines 117–121)

```
def _results(payload: object) -> list[dict[str, object]]
```

**Purpose**: Pulls the `results` list out of an Exa response and verifies that it has the expected shape. This prevents bad or surprising API responses from being mistaken for valid empty results.

**Data flow**: It receives the decoded response payload from Exa. If the payload is a dictionary with a list under `results`, it keeps only the list items that are dictionaries and returns them. If there is no usable results list, it raises `ExaError`.

**Call relations**: `search` uses this before converting result items into search hits, and `fetch` uses it before reading the fetched page item. It acts like a checkpoint between raw external data and UFO's internal objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `_opt_str`  (lines 124–125)

```
def _opt_str(value: object) -> str | None
```

**Purpose**: Returns a value only if it is actually a string; otherwise it returns `None`. It is a small safety helper for optional text fields.

**Data flow**: It receives any value. If the value is text, it passes that text through unchanged. If the value is missing, numeric, structured, or any other non-string type, it returns `None`.

**Call relations**: `_hit` uses it for optional published dates, and `fetch` uses it for optional summaries. This keeps unexpected Exa response values from leaking into fields that the rest of the system expects to be text or absent.

*Call graph*: called by 2 (_hit, fetch).


##### `manifest`  (lines 128–141)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO extension system. It tells UFO the extension's name and version, what credential it needs, and how to build the Exa search provider.

**Data flow**: It takes no input. It creates a `Manifest` containing one credential slot named `exa_api_key` and one search-provider specification for the `exa` backend. The provider specification includes a builder function that receives credentials and returns an `ExaSearchProvider`.

**Call relations**: The extension loader calls this when discovering or registering the extension. The returned manifest is what lets a deployment choose `[research] search_provider = "exa"` and gives the runtime enough information to request the Exa key and construct the provider when needed.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Research tool surface
Agent-facing research tools translate search and fetch requests into backend calls and readable model responses.

### `extensions/research/ufo_ext_research/tools.py`

`io_transport` · `request handling`

This file is the bridge between an agent asking, “Can you look this up?” and the outside search service that can actually do it. Without it, the agent would have no standard way to run web searches, fetch public URLs, or ask for specialized searches like videos or shopping results.

The file first describes the shape of each tool’s input using Pydantic models, which are runtime-checked data forms. For example, `SearchWebInput` limits a call to at most five search queries, and `FetchUrlInput` requires a public HTTP or HTTPS URL. These checks help stop vague or unsafe requests before they reach the search provider.

The important safety boundary is that the real search provider runs on the host side, not inside the sandbox. That means API keys stay out of the workspace. Fetching also happens through the provider’s crawler, so any login/session identity reflected in fetched content belongs to that crawler, not to the user’s workspace. The file makes this explicit by adding a provenance warning to fetched pages.

At the bottom, `RESEARCH_TOOLS` publishes three tool definitions: `search_web`, `fetch_url`, and `search_vertical`. Each one names its input form and the async function that performs the work.

#### Function details

##### `_provider`  (lines 116–119)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function retrieves the search provider selected for the current tool call. If no provider is configured, it fails clearly instead of letting the agent believe a search was attempted.

**Data flow**: It receives the current `ToolContext`, which is the bundle of information available during a tool call. It reads `ctx.search_provider`; if that value exists, it returns it. If it is missing, it raises an error saying no search provider is configured for this turn.

**Call relations**: The three tool handlers call this first before doing any search or fetch work. It acts like checking that the library desk is staffed before sending someone to ask a research question.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 122–136)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search results into a simple JSON string that the model can read. It keeps the useful parts of each result: URL, title, snippet text, publication date, and highlights.

**Data flow**: It receives a list of search hits and an optional direct answer from the provider. It copies each hit into a plain dictionary, adds all dictionaries to a `results` list, optionally adds `answer`, and serializes the whole payload into JSON text.

**Call relations**: Both `_search_web` and `_search_vertical` use this after they receive results from the search provider. It is the common packing step that makes different search modes return the same easy-to-consume format.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 139–154)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_web` tool. It runs one web search for each requested query, combines the results, and returns them to the agent.

**Data flow**: It receives the tool context and validated search arguments. It gets the configured provider, then loops over the requested queries. For each query, it builds a `SearchQuery` with the requested recency filter and allowed domains, asks the provider for results, appends the hits to one combined list, and keeps the first direct answer if the provider supplies one. It returns a `ToolResult` containing JSON text.

**Call relations**: When the agent calls `search_web`, the tool system routes the request here. This function depends on `_provider` to find the backend and on `_results_json` to format the combined response before wrapping it in `TextContent` and `ToolResult`.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


##### `_fetch_url`  (lines 157–176)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It fetches the contents of a public web page through the configured search provider’s crawler, optionally asking the provider to extract or summarize specific information.

**Data flow**: It receives the tool context and validated fetch arguments. It first gets the provider, then checks whether that provider supports URL fetching. If not, it returns an error message telling the agent to use another route. If fetching is supported, it builds a `FetchRequest`, sends it to the provider, then returns JSON containing the final URL, page text, a crawler provenance warning, and an optional summary.

**Call relations**: When the agent calls `fetch_url`, this function performs the tool’s work. It calls `_provider` for the backend and hands a `FetchRequest` to that backend. Unlike search results, it formats its own JSON because fetched pages include a special provenance warning that is specific to crawler-based fetching.

*Call graph*: calls 1 internal fn (_provider); 4 external calls (__init__, __init__, __init__, dumps).


##### `_search_vertical`  (lines 179–186)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It performs a search focused on one content type, such as images, people profiles, academic papers, videos, or shopping listings.

**Data flow**: It receives the tool context and validated vertical-search arguments. It gets the configured provider, creates a `SearchQuery` that includes both the user’s query and the chosen vertical, asks the provider for results, then returns those results as JSON text inside a `ToolResult`.

**Call relations**: When the agent calls `search_vertical`, the tool system routes the request here. This function follows the same basic path as `_search_web`: get the provider, ask it to search, then use `_results_json` to package the response. The main difference is that it sends one query with a specific content category attached.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


### Lookup abstractions
Shared interfaces define how memory recall and web search results are represented across backends.

### `core/src/ufo/memory.py`

`domain_logic` · `request handling`

This file is a small contract between code that wants to recall past information and extensions that know how to search that information. Without this shared contract, every memory provider would return results in its own format, and callers would need custom code for each one.

The central result shape is `MemoryMatch`, which represents one search hit. It includes the kind of memory, the text snippet to show or inject, and, when available, a durable object reference plus a creation time. In plain terms, the search result can say both “here is the useful text” and “here is the saved object you can open later.”

`MemorySearchProvider` is a protocol, meaning a formal promise about what a memory provider must be able to do. Any provider that has a compatible `search` method can plug in.

`MemorySearch` is the small wrapper that makes searches conversation-aware. Given a conversation id, it looks up which member owns that conversation inside the current workspace, then asks the provider to search memory scoped to that member and optional time window. Like a receptionist checking which office you belong to before forwarding your request, it makes sure the provider gets the right context.

#### Function details

##### `MemorySearchProvider.search`  (lines 34–40)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This is the required search method for any memory provider. A provider uses it to return matching memories for one or more query strings, optionally limited to a member and a date range.

**Data flow**: It receives search text, an optional member id, and optional start and end times. The provider is expected to search its own memory store using those limits and return a tuple of `MemoryMatch` results. This protocol method does not implement the search itself; it describes the shape that real providers must follow.

**Call relations**: The rest of the memory system can call this method without caring which provider is behind it. `MemorySearch.search` resolves the conversation into a member id, then hands the actual searching work to this provider method.


##### `MemorySearch.search`  (lines 49–66)

```
async def search(self, conversation_id: UUID, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This searches memory for a specific conversation. It first finds the member connected to that conversation in the current workspace, then asks the configured memory provider to search using that member as the scope.

**Data flow**: It receives a conversation id, one or more query strings, and optional start and end times. It opens a workspace database transaction, reads the conversation table to find the matching member id for the current workspace, closes the transaction, and passes the queries, member id, and time limits to the provider. It returns the provider's `MemoryMatch` results without changing them.

**Call relations**: When a caller wants memory for a conversation, this method acts as the bridge between conversation identity and provider search. It uses `workspace_tx` to safely read from the workspace database, `ws_current` to make sure the lookup stays inside the active workspace, and `sqlalchemy.select` to build the database query. After that setup, it delegates the real recall work to `MemorySearchProvider.search`.

*Call graph*: 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/search.py`

`data_model` · `cross-cutting: defined at startup, used during research tool calls`

This file is a boundary, or “seam,” between the core system and any real web search service. The core project does not include a built-in search engine and does not keep search API keys itself. Instead, an extension supplies a SearchProvider, which is an object that knows how to talk to a chosen search backend.

The file defines simple frozen data shapes for the messages that cross this boundary. A SearchQuery says what to search for, how many results to return, and optional filters such as freshness or allowed domains. SearchResults returns ranked SearchHit items, and may also include a direct answer if the backend can produce one. FetchRequest asks for the text of a single web page, and FetchedPage returns the extracted text and maybe a summary.

The SearchProvider protocol is the contract every search backend must follow. Think of it like a standard plug shape: many different adapters can fit, but the core only needs to know the shape of the plug. A provider can say whether it supports fetching full pages. If it does not, callers are expected to check first; otherwise fetch should fail clearly with SearchUnsupported. This keeps web access outside the sandbox and keeps provider credentials on the host side.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 87–87)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether this search provider can fetch the contents of an individual web page, not just return search links. It lets tools avoid asking for a feature the chosen backend cannot provide.

**Data flow**: The caller reads the property from a provider instance. The provider answers with true or false. Nothing else is changed; the result is a simple capability flag used before deciding whether to request a page fetch.

**Call relations**: Research tools check this capability before calling fetch. If it is false, they can stop early or choose another path instead of triggering the provider’s fail-loud SearchUnsupported behavior.


##### `SearchProvider.search`  (lines 89–89)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This asynchronous method is the standard way to ask a provider to run a web search. It hides the details of the actual outside search service behind one shared request-and-result shape.

**Data flow**: A caller gives it a SearchQuery containing the search text, result count, and optional filters. The provider sends that request to its backend in whatever way it supports, then returns SearchResults containing matching hits and possibly a direct answer.

**Call relations**: The research extension’s tools call this through the turn’s tool context when they need web information. Concrete provider implementations do the real network work, while core code only relies on this protocol.


##### `SearchProvider.fetch`  (lines 91–91)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This asynchronous method is the standard way to ask a provider to retrieve and extract readable text from one URL. It is only valid for providers whose supports_fetch property says they can do this.

**Data flow**: A caller gives it a FetchRequest with a URL and optional instructions such as a summary prompt, maximum text length, or a request to bypass cache. The provider retrieves or extracts the page content and returns a FetchedPage with the URL, text, and maybe a summary. If the provider does not support fetching, it should raise SearchUnsupported.

**Call relations**: The fetch_url research tool is expected to check supports_fetch before reaching this method. Concrete backend providers implement the actual page retrieval, while this protocol keeps the core system independent of any one search or fetch service.
