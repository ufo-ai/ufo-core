# Connectors, source syncing, search, and memory retrieval  `stage-14`

This stage is shared support for the agent’s main work and for background syncing. It is how the system reaches outside knowledge, brings that knowledge inside, and finds it again later. Connector action execution is the “front desk” for live actions in apps like Slack or GitHub. It lists available tools, checks what they need, runs them through brokers such as Composio or Pipedream, and keeps secret credentials away from the agent.

Source ingestion, indexing, and recall is the “library team.” It connects to outside sources, copies records into internal pages, notices updates and deletes, splits text into searchable pieces, and stores indexes for later recall. The source tools let agents register these sources, choose what to sync, and set wake-ups when content changes.

Search and memory files define common shapes so the rest of the system can ask for web results or remembered information without caring which provider is underneath. Perplexity and research tools provide web search and page fetching. Turbopuffer stores and searches memory chunks. Enrichment providers add person and company details from live data or safe recordings. Package marker files simply make these modules importable.

## Sub-stages

- [Connector action execution](stage-14.1.md) `stage-14.1` — 12 files
- [Source ingestion, indexing, and recall](stage-14.2.md) `stage-14.2` — 73 files

## Files in this stage

### Runtime Contracts
Shared runtime interfaces define how search, memory, and source packages are addressed by the rest of the system.

### `core/src/ufo/runtime/search.py`

`data_model` · `cross-cutting`

This file is a boundary, or “seam,” between the core runtime and any real web search backend. The core system does not contain a built-in search engine and does not keep API keys itself. Instead, an outside extension provides a SearchProvider, and the rest of the system talks to it through the simple types defined here.

The file describes the messages that pass across that boundary. A SearchQuery says what to look for, how many results to return, and any limits such as publication dates or allowed domains. SearchResults returns ranked SearchHit items, and may also include a direct answer if the backend can produce one. A FetchRequest asks for the text of one URL, possibly with an extraction prompt or length limit. A FetchedPage is the returned page text and optional summary.

The SearchProvider protocol is like a contract. Any backend that wants to plug in must offer a way to search, may offer a way to fetch pages, and must say whether fetching is supported. This matters because tools can depend on one stable interface while different deployments choose different search services safely, including services that require private keys.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This tells callers whether the chosen search backend can fetch and extract the contents of a specific web page. It prevents tools from asking for page fetching when the backend only supports search results.

**Data flow**: The caller reads this property from a SearchProvider. The provider returns a true-or-false answer. Nothing else is changed; the answer is used to decide whether it is safe to call fetch.

**Call relations**: In the bigger flow, a research tool checks this before trying to fetch a URL. If the provider says fetching is supported, the tool can continue to SearchProvider.fetch; if not, it must stop or report that fetching is unavailable.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This asks the selected backend to run a web search from a SearchQuery. It is the main way the core runtime gets search results without knowing the details of the search service.

**Data flow**: A caller provides a SearchQuery containing the search wording, result count, and optional filters such as date range or allowed domains. The provider sends that request to its own backend service and turns the response into SearchResults. The result comes back as ranked hits, and sometimes also a direct answer.

**Call relations**: During a turn, research tools call this method through the tool context when they need web information. The concrete provider implementation does the outside API work, then hands normalized SearchResults back to the tool so the rest of the system does not need backend-specific code.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This asks the selected backend to retrieve and extract text from one web page. It is used when search results are not enough and the system needs the actual page content.

**Data flow**: A caller provides a FetchRequest with a URL and optional instructions, such as a summary prompt, maximum text length, or whether to bypass a cache. The provider fetches or extracts the page through its backend and returns a FetchedPage with the URL, text, and optional summary.

**Call relations**: This is called only after the caller has confirmed that SearchProvider.supports_fetch is true. In the research flow, a fetch tool uses it to turn a chosen URL into readable page text that can then be used by the rest of the turn.


### `core/src/ufo/runtime/memory.py`

`data_model` · `cross-cutting during memory search and recent-memory browsing`

This file is a contract between code that wants to recall past information and extensions that know how to find that information. Think of it like a standard library card catalog form: every library may store books differently, but the search desk returns results in the same format.

The main result type is `MemoryMatch`, a small record for one found item. It contains the kind of memory, the text snippet to show, and, when available, a durable object reference that can later be opened. It can also carry a creation time and subject, which help users judge whether a memory is relevant without opening it.

`MemorySearchProvider` is a protocol, meaning a promise about methods an object must provide. Any memory extension that wants to participate must support searching by query text, listing recent readable items, and reporting which item kinds can be listed.

`MemorySearch` is a thin front door around one chosen provider. It does not search by itself. Instead, it forwards requests to the provider. This keeps the rest of the runtime simple: callers use one stable interface, while the actual memory implementation can vary behind it.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Defines how a memory provider must search for relevant memories from one or more query strings. A caller uses this when it wants recall-style results, filtered by what the current reader is allowed to see and optionally by time.

**Data flow**: The inputs are query texts, a `SourceReader` that represents the readable sources or permissions, and optional start and end times. An implementing provider uses those to look through its memory store and returns a tuple of `MemoryMatch` records. This protocol method itself does not contain the search; it states what real providers must return.

**Call relations**: Consumers can call this through `MemorySearch.search`, which simply passes the request onward. Concrete memory extensions supply the actual implementation, so this method is the agreed doorway between the runtime and whichever memory backend is installed.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Defines how a memory provider must list recent memory items that a set of subjects may read. This is for browsing recent memories, not for searching by similarity or query text.

**Data flow**: The inputs are a set of readable subjects, a maximum number of items, an optional filter for memory kinds, and an optional cursor. The cursor is a bookmark for paging through results without relying on row numbers, which helps avoid repeated or skipped items if new memories are added while someone is browsing. The output is a `ListingPage` containing `MemoryMatch` items and paging information.

**Call relations**: Consumers can reach this through `MemorySearch.list_recent`, which forwards the same inputs to the provider. The real provider decides how to read its storage, but it must obey this shared paging and filtering shape.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Defines how a provider reports the closed set of memory item kinds it can show in recent-memory browsing. This lets user interfaces or callers offer filters that match what the provider actually stores.

**Data flow**: There are no inputs. A concrete provider returns a tuple of kind names, such as categories of memory items. The protocol method only defines the required answer; the actual list comes from the provider implementation.

**Call relations**: Consumers can ask for this through `MemorySearch.listable_kinds`. It supports the filtering flow used by `list_recent`, because callers need to know which kind filters are valid before asking for a recent listing.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Provides the runtime’s simple search entry point for memory recall. It accepts the caller’s query and access context, then asks the selected provider to do the real search.

**Data flow**: The function receives a `SourceReader`, query strings, and optional start and end times. It passes those values unchanged to `self.provider.search`. The result from the provider, a tuple of `MemoryMatch` records, is returned directly to the caller.

**Call relations**: This is the forwarding layer between ordinary runtime consumers and the installed `MemorySearchProvider`. It does not interpret or reshape results; its job is to keep callers pointed at one stable interface while the provider performs the backend-specific work.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Provides the runtime’s simple entry point for browsing recent memory items. It asks the selected provider for the newest readable memories, with optional kind filtering and paging.

**Data flow**: The function receives readable subjects, a result limit, optional memory kinds, and an optional listing cursor. It sends those values unchanged to `self.provider.list_recent`. The returned `ListingPage` is passed straight back to the caller.

**Call relations**: This wraps `MemorySearchProvider.list_recent` so callers do not need to know which provider is installed. It fits into recent-memory browsing flows, where a caller may request one page, keep the cursor, and later ask for the next page.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Provides the runtime’s simple way to ask which memory kinds can be listed. Callers use it to build valid filters before requesting recent memory pages.

**Data flow**: The function takes no extra input beyond the selected provider stored on `MemorySearch`. It calls `self.provider.listable_kinds` and returns the provider’s tuple of kind names unchanged.

**Call relations**: This is a small pass-through to the provider’s kind list. It supports callers that want to filter `MemorySearch.list_recent` without guessing which memory categories exist.


### `core/src/ufo/runtime/sources/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. That means code elsewhere can refer to modules inside `ufo.runtime.sources` using normal import paths.

There is no code here because the package does not need any setup work when it is imported. Its value is structural: it helps organize the project into meaningful areas. In this case, the folder name suggests that nearby files likely deal with “sources” used by the UFO runtime, but this file itself does not define those sources or change how they behave.

Without this file, depending on the Python version and packaging setup, imports from this folder could be less reliable or fail in some environments. Think of it like a label on a drawer: it does not contain the tools, but it tells the rest of the system that the drawer exists and can be opened.


### Web Research
Perplexity provides the external search transport, while research tools expose safe search and fetch capabilities to agents.

### `extensions/perplexity/ufo_ext_perplexity.py`

`io_transport` · `extension discovery and search/fetch request handling`

This extension is the bridge between the project’s generic search interface and Perplexity’s online search API. Without it, the system could still ask for “search results” in a general way, but it would not know how to turn that request into the exact HTTP call Perplexity expects, or how to turn Perplexity’s reply back into the project’s standard result objects.

The main class, `PerplexitySearchProvider`, has two user-facing jobs. `search` asks Perplexity for web results and returns a clean list of hits, each with a URL, title, snippet, and optional date. `fetch` is a more targeted operation: it tries to extract text for one exact URL by asking Perplexity to search only that page’s domain, then checking that the returned page really matches the requested one.

The file also protects Perplexity and the host app from bad requests. It limits overly long queries, prompts, URLs, and fetched text sizes. It validates that API responses have the shape the rest of the system expects. If Perplexity refuses a request, returns broken JSON, or fails to return the requested page, the file raises a clear `PerplexityError`.

At the bottom, `manifest` advertises this extension to the host system. Think of it like a sign-up sheet: it says what credential is needed and how to build the provider when the Perplexity backend is selected.

#### Function details

##### `PerplexitySearchProvider.search`  (lines 72–85)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a normal web search through Perplexity and returns the results in the project’s standard search-result format. Someone would use this when they want a list of relevant pages, not the contents of one exact page.

**Data flow**: It receives a `SearchQuery`, which contains the search text plus options like result count, vertical, allowed domains, and date limits. It turns that into a Perplexity request body, sends it over the network, validates the returned data, then converts each Perplexity result into a `SearchHit`. The final output is a `SearchResults` object containing those hits.

**Call relations**: This is one of the provider’s main entry points for the host search system. It relies on `_search_body` to translate the project’s query into Perplexity’s expected format, `_post` to make the HTTP request, and `_response` to check that Perplexity’s reply is usable before building the public result objects.

*Call graph*: calls 3 internal fn (_post, _response, _search_body); 2 external calls (__init__, __init__).


##### `PerplexitySearchProvider.fetch`  (lines 87–131)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Tries to retrieve text for one specific web page using Perplexity. It is used when the system already has a URL and wants a short extracted page text or a prompt-guided summary.

**Data flow**: It receives a `FetchRequest` with a URL, optional extraction prompt, and optional character limit. It first checks that the URL, prompt, and limits are safe and reasonable. It then extracts the URL’s domain, builds a Perplexity search restricted to that domain, sends the request, validates the response, and looks for a result whose canonical page matches the requested URL. If found, it trims the snippet to the requested size and returns a `FetchedPage`; if not, it raises a `PerplexityError`.

**Call relations**: This is the provider’s main entry point for exact-page extraction. It calls `_post` to talk to Perplexity, `_response` to validate the reply, and `_canonical_page` to compare URLs in a forgiving way, such as ignoring a trailing slash or common page suffix. It hands the final page text back as a `FetchedPage` for the rest of the system to use.

*Call graph*: calls 3 internal fn (_post, _response, _canonical_page); 3 external calls (__init__, __init__, urlsplit).


##### `PerplexitySearchProvider._search_body`  (lines 134–159)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON request body that Perplexity expects for a search. It translates the project’s general search options into Perplexity-specific fields.

**Data flow**: It receives a `SearchQuery`. It checks that the requested result count is at least one, caps the count to Perplexity’s supported maximum, adds plain-language qualifiers for certain verticals like academic or video, applies optional domain and date filters, and calculates token limits. It returns a dictionary ready to send as JSON in the API request, or raises `PerplexityError` if the query is too long or invalid.

**Call relations**: The `search` method calls this before any network request is made. When date filters are present, this helper asks `_api_date` to format them the way Perplexity’s API expects.

*Call graph*: calls 1 internal fn (_api_date); called by 1 (search); 1 external calls (__init__).


##### `PerplexitySearchProvider._response`  (lines 162–166)

```
def _response(payload: object) -> _PerplexitySearchResponse
```

**Purpose**: Checks that Perplexity’s reply has the expected shape before the rest of the code trusts it. This prevents malformed API responses from quietly causing confusing errors later.

**Data flow**: It receives raw decoded JSON data from Perplexity. It asks the Pydantic model, a data validator, to confirm that the response contains a `results` list with items that have fields like URL, title, and snippet. If validation succeeds, it returns a structured `_PerplexitySearchResponse`; if validation fails, it raises `PerplexityError`.

**Call relations**: Both `search` and `fetch` call this after `_post` returns raw API data. It acts like a border checkpoint between outside data and the project’s trusted internal objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `PerplexitySearchProvider._post`  (lines 168–188)

```
async def _post(self, body: dict[str, Json]) -> object
```

**Purpose**: Sends one HTTP POST request to Perplexity’s `/search` endpoint. It is the single place in this file that actually talks to the Perplexity service over the network.

**Data flow**: It receives a JSON-ready request body. It reads the Perplexity API key from the configured credential slot, creates an asynchronous HTTP client, sends the body with a bearer authorization header, and waits for the response. If Perplexity returns an error status or invalid JSON, it raises `PerplexityError`; otherwise it returns the decoded JSON payload.

**Call relations**: `search` and `fetch` both call this after they have built their request bodies. It uses `httpx.AsyncClient`, the HTTP client library, to perform the network call, then gives raw response data back to the caller for validation by `_response`.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_api_date`  (lines 191–192)

```
def _api_date(value: date) -> str
```

**Purpose**: Formats a Python date into the date string Perplexity’s API expects. It keeps date formatting in one small helper instead of repeating it in the request-building code.

**Data flow**: It receives a `date` value. It formats that value as month/day/year, such as `03/15/2024`, and returns the resulting string.

**Call relations**: `_search_body` calls this when the user’s search query includes start or end publication dates. The formatted strings are then placed into Perplexity’s date filter fields.

*Call graph*: called by 1 (_search_body); 1 external calls (strftime).


##### `_canonical_page`  (lines 195–202)

```
def _canonical_page(value: str) -> tuple[str | None, str, str]
```

**Purpose**: Turns a URL into a simpler comparison key so two slightly different-looking URLs can still be recognized as the same page. For example, it treats some common page suffixes and trailing slashes as unimportant.

**Data flow**: It receives a URL string. It splits the URL into parts, removes a trailing slash from the path, strips common suffixes like `.html`, `.htm`, or `.txt`, and returns a tuple containing the hostname, simplified path, and query string.

**Call relations**: `fetch` uses this helper when checking whether Perplexity returned the exact page that was requested. It compares the canonical form of the requested URL with the canonical form of each returned result URL.

*Call graph*: called by 1 (fetch); 1 external calls (urlsplit).


##### `manifest`  (lines 205–225)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system so it can be discovered and used. It declares the needed Perplexity API key and tells the system how to create a `PerplexitySearchProvider`.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name and version, one credential slot for the Perplexity API key, and one search provider specification for the Perplexity backend. The returned manifest is what the host reads during extension setup.

**Call relations**: This function is called by the extension-loading machinery rather than by search requests directly. It wires together `CredentialSlot`, `SearchProviderSpec`, and the provider constructor so later, when the Perplexity backend is chosen, the host can build the provider with access to credentials.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/tools.py`

`io_transport` · `request handling`

This file exists so the agent can ask for outside information without directly knowing how search engines, crawlers, or API keys work. It defines three tools: `search_web` for general web search, `fetch_url` for reading a public web page, and `search_vertical` for targeted searches such as images, videos, people, shopping, or academic papers.

The file first describes the shape of each tool’s input using Pydantic models, which are validation rules that check the agent’s arguments before anything is sent onward. For example, web search is limited to a small number of queries, result counts have bounds, and fetches must be URLs rather than local files.

When a tool runs, it looks up the current turn’s `SearchProvider`, meaning the search service chosen by the host process. This matters because secrets such as search API keys stay outside the sandbox. The tool then converts the request into the provider’s format, waits for results, records observations when extension storage is available, and returns plain JSON text to the model.

A key safety detail is fetch provenance. Fetched pages come from the search provider’s crawler session, not from the user’s workspace. So if a page appears logged in or personalized, that identity belongs to the crawler, not the user.

#### Function details

##### `_provider`  (lines 133–136)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This helper finds the search backend selected for the current tool call. It fails loudly if no backend was configured, because searching or fetching cannot work without one.

**Data flow**: It receives the tool context, reads `ctx.search_provider`, and either returns that provider or raises an error. Before this function, the caller has only the general context; after it, the caller has the concrete search service to use.

**Call relations**: The three tool handlers call this at the start of their work. `_search_web`, `_fetch_url`, and `_search_vertical` all rely on it so they do not have to repeat the same missing-provider check.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 139–153)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This helper turns search results into JSON text that the agent can read. It standardizes the answer format for both normal web search and vertical search.

**Data flow**: It receives a list of search hits and an optional direct answer. It copies the useful fields from each hit, such as URL, title, snippet text, publication date, and highlights, then packages them into a JSON string. If an answer is present, it includes that too.

**Call relations**: After `_search_web` or `_search_vertical` receives results from the provider, they hand those results to this helper. The helper prepares the final text that is wrapped in a `ToolResult` and returned to the agent.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 156–174)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_web` tool. It runs one or more general web searches and returns a merged set of results to the agent.

**Data flow**: It receives the tool context and validated search arguments. It gets the provider, builds a search request for each query, applies options like result count, publication dates, and allowed domains, and collects all returned hits. If observation recording is available, it stores the search hits for later inspection. It then returns a tool result containing JSON text.

**Call relations**: This function is called when the agent invokes `search_web`. It first uses `_provider` to find the backend, sends `SearchQuery` objects to that backend, optionally records the results through `record_search_hits`, and uses `_results_json` to produce the response text.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 177–198)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It asks the search provider’s crawler to read a public web page and return its contents, optionally guided by a prompt.

**Data flow**: It receives the tool context and validated fetch arguments, including the URL, optional extraction prompt, length limit, and cache-bypass flag. It checks whether the provider supports fetching. If not, it returns an error message telling the agent to use another route. If fetching is supported, it builds a fetch request, receives the page, records it when possible, adds provenance text explaining that the crawler fetched it, and returns the page data as JSON.

**Call relations**: This function is called when the agent invokes `fetch_url`. It uses `_provider` to get the backend, sends a `FetchRequest` to that backend, records the fetched page through `record_fetched_page` when extension storage exists, and wraps the JSON response in a `ToolResult`.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 201–210)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It performs a search in a specific category, such as images, people, academic papers, videos, or shopping results.

**Data flow**: It receives the tool context and validated vertical-search arguments. It gets the provider, builds a search query that includes the requested vertical, asks for the default number of results, and receives hits plus any answer. If possible, it records the hits. It then returns the results as JSON text.

**Call relations**: This function is called when the agent invokes `search_vertical`. It follows the same broad path as `_search_web`: get the provider with `_provider`, ask it to search, optionally record hits with `record_search_hits`, and format the final response with `_results_json`.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


### Source Connections
Connector packaging and source tools let agents register external sources, select sync targets, and watch synced resources for updates.

### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python projects, a folder often needs an `__init__.py` file so Python treats that folder as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find the drawer by name. Without this file, some Python environments or packaging tools might not recognize `ufo_ext_connectors` as a package, which could make imports fail when the application tries to load connector extensions. There is no runtime logic here, no setup code, and no hidden side effects. Its value is structural: it helps define the project’s module layout and supports clean imports elsewhere in the codebase.


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and source-change hooks`

This file turns synced external content into two object types that agents can work with: `source` and `source_trigger`. A source is a standing sync setup for one provider account, such as a GitHub or Salesforce account, plus selected streams of content. The file validates the provider, stream names, account choice, tenant URL, sharing setting, and backfill window before it writes anything. It also keeps source names deterministic, like a label printed from the account and provider, so the same binding cannot accidentally be registered twice under two names. A trigger is a standing interest in a shared source. It can wake the current conversation when that source changes, or open a stable conversation per changed page. Triggers can also narrow to one resource URL, such as one pull request, so only changes about that resource wake the thread. The hook functions at the bottom are the live parts: one reacts when pages change and delivers alerts, while another notices links in prompts or tool output and offers the agent a ready-to-apply watch manifest. In short, this file is the bridge between synced provider data, the object system, access rules, and conversation notifications.

#### Function details

##### `_Binding.name`  (lines 250–251)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. The name comes from the provider, account, and tenant URL, so the same real source always gets the same name.

**Data flow**: It reads the binding's provider, account, and base URL → passes them to the shared name-making helper → returns the derived source object name.

**Call relations**: Other code treats this name as the binding's identity. It relies on the common `binding_name` helper so source objects and trigger objects agree on what a binding is called.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 254–255)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the binding first existed. A binding can contain several stream rows, so this uses the oldest stream creation time.

**Data flow**: It reads all stream creation times in the binding → chooses the earliest one → returns that timestamp.

**Call relations**: Object detail views use this property to show the binding's creation time as one combined object rather than as separate stream rows.


##### `_Binding.updated_at`  (lines 258–259)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports when anything in the binding was most recently updated. Because each stream row can change separately, this uses the newest stream update time.

**Data flow**: It reads all stream update times in the binding → chooses the latest one → returns that timestamp.

**Call relations**: Object detail views use this to summarize the whole binding's freshness from its underlying stream rows.


##### `_Binding.links`  (lines 261–274)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Shows what credential or connection the source uses for access. This helps readers understand which account or workspace credential powers the sync.

**Data flow**: It reads whether the binding uses a direct workspace credential, a connected account, or a shared source → builds object links where appropriate → returns those links, or none for shared brokered sources.

**Call relations**: Source object detail calls this when presenting a source. It creates references to credential or connection objects so the object system can display relationships between things.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 276–284)

```
def spec(self) -> SourceSpec
```

**Purpose**: Turns the stored binding back into the user-facing source specification. This is what `object_get` can show or what an agent can re-apply.

**Data flow**: It reads provider, stream names, account, URL, sharing subject, and backfill setting → converts internal values into `SourceSpec` fields → returns that specification.

**Call relations**: Source object detail and update checks depend on this to compare what is stored with what the caller submitted.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 286–288)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a source binding. It names the provider, account, and selected streams.

**Data flow**: It joins the stream names and combines them with provider and account → trims the result to the summary length limit → returns the text.

**Call relations**: Trigger summaries and alert messages call this so notifications say what source changed in terms a person can recognize.

*Call graph*: called by 2 (_alert_message, _trigger_summary).


##### `_require_ext`  (lines 297–300)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the extension runtime context is present. This context is the file's doorway to stored sources, files, conversations, and credentials.

**Data flow**: It receives an optional extension context → if it is missing, it raises a runtime error → otherwise it returns the context unchanged.

**Call relations**: Most source and trigger operations call this before touching runtime storage. It acts like checking that the toolbox is actually on the workbench before starting the job.

*Call graph*: called by 11 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers (+1 more)).


##### `_require_connectors`  (lines 303–306)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool turn has a connector registry. The registry says what external account connectors are available.

**Data flow**: It reads `ctx.connectors` from the tool context → raises a runtime error if it is missing → returns the registry if present.

**Call relations**: Account resolution calls this when deciding whether a provider should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 309–344)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Rebuilds high-level source bindings from low-level stored source rows. Each stream is stored separately, but users see one binding per provider/account/URL.

**Data flow**: It asks the extension context for all source rows → ignores rows from providers this extension does not know → parses each row's config and groups rows with the same provider, account, and URL → returns sorted `_Binding` objects.

**Call relations**: Listing sources, finding one source by name, offering watch triggers, and processing page changes all start here because they need the user-facing binding view.

*Call graph*: calls 1 internal fn (sources); called by 5 (_member_rows, _member_rows, _binding_named, on_link_seen, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 347–357)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one source binding by its derived object name. Triggers and source object operations both use this as their lookup step.

**Data flow**: It receives an extension context and a name → rebuilds all bindings → returns the binding whose derived name matches, or `None` if no match exists.

**Call relations**: Source reads, status checks, deletes, resyncs, grants, and trigger creation all call this when they need to confirm that a named source really exists.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 360–361)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the source trigger store. The trigger store is where standing conversation wake-ups are recorded.

**Data flow**: It requires a live extension context → wraps it in `SourceTriggerStore` → returns that store object.

**Call relations**: Trigger listing, creation, deletion, lookup, link offers, page-change delivery, and source deletion use this to read or change trigger rows.

*Call graph*: calls 1 internal fn (_require_ext); called by 7 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_link_seen, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 364–373)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides the actual backfill window for a stream. A backfill window means how far back in time the first sync should look.

**Data flow**: It receives the user's request and the stream's declared default → returns the user's number if given, the default if no user request was made, or `None` for all history.

**Call relations**: Source registration and window widening both call this so they calculate cutoffs by the same rule.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 376–386)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Extracts the fields that define whether two source specs are the same binding setup. It deliberately includes streams, account, URL, sharing, and backfill request.

**Data flow**: It receives a `SourceSpec` → sorts the stream names and collects the identity fields → returns them as a tuple that can be compared directly.

**Call relations**: The main source apply path and resync path use this to tell a no-op reapply from a real change, and to refuse resync requests that try to edit the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 415–436)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Decides which kind of source apply request this is: a resync, an identical reapply, or a real create/update. This keeps special cases from going through the wrong permission path.

**Data flow**: It receives the tool context, object name, new spec, old spec, and expected generation → checks for `resync`, then checks whether the spec is identical to what already exists → either schedules a resync, grants access to the settled source, or delegates to the base object apply flow.

**Call relations**: This is the entry point for applying `source` objects. It hands special cases to `_resync` and `_grant_settled`, and leaves normal object mutation to the parent class.

*Call graph*: calls 3 internal fn (_grant_settled, _resync, _binding_identity).


##### `SourceObjects._grant_settled`  (lines 438–459)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Gives the current agent access to an already-existing source when the agent re-applies the exact same spec. Without this, a no-op apply could appear successful but leave the agent unable to read the feed.

**Data flow**: It reads the speaker, owner information, and binding streams → confirms the speaker is allowed to receive the grant → grants each stream source to the current agent.

**Call relations**: `SourceObjects.apply` calls this only for identical re-applies. It looks up the binding with `_binding_named` and writes grants through the extension context.

*Call graph*: calls 2 internal fn (_binding_named, _require_ext); called by 1 (apply).


##### `SourceObjects._resync`  (lines 461–486)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync of an existing source without changing its configuration. It protects the provider account from being used by someone who can merely see a shared source.

**Data flow**: It receives the submitted spec and current spec → refuses if they are not the same apart from the resync flag → checks owner/admin permission → finds the binding → asks the extension runtime to schedule all its streams for sync.

**Call relations**: `SourceObjects.apply` calls this when `resync` is set. It uses identity comparison, ownership checks, binding lookup, and finally the runtime scheduler.

*Call graph*: calls 5 internal fn (require_speaking_admin, speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `SourceObjects._member_rows`  (lines 488–501)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when someone lists source objects. Each row summarizes one reconstructed binding and its owner or shared status.

**Data flow**: It rebuilds bindings from source rows → turns each binding into an `OwnedRow` with name, summary, and owner information → returns all rows.

**Call relations**: The base object listing machinery calls this. It supplies the source-specific list data while the parent class applies visibility rules.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 503–519)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed view of one source object. It includes the spec, timestamps, and links to credentials or connections.

**Data flow**: It receives a source name and owner → looks up the binding → if found, converts it into an `ObjectDetail`; if missing, returns `None`.

**Call relations**: The base object `get` path calls this after it has decided the caller may see the object. It depends on `_binding_named` and the binding's helper methods.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 521–547)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports operational status for a source, such as next sync time, error count, parked state, and backfill cutoff per stream. This is the health dashboard for a binding.

**Data flow**: It looks up the binding → reads each stream's sync and error fields → returns a JSON-friendly dictionary, including owner member ID only for private sources.

**Call relations**: Status requests for source objects call this after object visibility has been checked. It uses `_binding_named` to connect the object name back to stream rows.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 549–653)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Creates or updates a source after the base object layer has handled ownership rules. This is where provider validation, stream changes, sharing changes, account resolution, backfill windows, registration, grants, and removals happen.

**Data flow**: It receives the desired source spec → validates speaker, provider, streams, backfill rules, URL, account, and derived name → compares with any existing binding → widens windows or flips private to shared when allowed → registers new streams, grants existing streams to the agent, and removes dropped streams.

**Call relations**: The parent object apply flow calls this for real source changes. It coordinates `_resolved_account`, `_validated_base_url`, `_widen_window`, `effective_days`, binding lookup, and extension runtime writes.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 8 external calls (__init__, __init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 655–729)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Safely expands the backfill window for streams that support time-limited history. It refuses narrowing because narrowing could leave old synced pages stranded instead of removed.

**Data flow**: It receives the current binding, kept streams, declared stream windows, account, URL, and new request → computes each new cutoff from the original registration anchor → refuses any move that would make the cutoff later → rewrites source configs and marks widened streams for refetch.

**Call relations**: `SourceObjects._apply_owned` calls this before making other writes when the backfill request changes. It uses the same `effective_days` rule as initial registration.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 731–738)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding by removing every stream row in it. It also removes triggers that watched that source, because those triggers would otherwise point at nothing.

**Data flow**: It receives a source name → finds the binding → removes each stream source through the extension context → asks the trigger store to remove triggers for that binding.

**Call relations**: The base object delete flow calls this after permission checks. It uses `_binding_named`, `_require_ext`, and `_require_triggers` to clean both source rows and trigger rows.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 740–813)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides what account or credential a source will authenticate with. It chooses between a connected account and a direct workspace credential based on what the provider and current grants support.

**Data flow**: It reads the connector registry, current connector accounts, connection details, declared credentials, and the submitted account ID → validates ownership and availability → returns the account handle plus a connection ID when one exists.

**Call relations**: `SourceObjects._apply_owned` calls this before registration so each source row stores the correct account identity and can later sync with the same authentication path.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 816–823)

```
def trigger_name(binding: str, conversation_id: UUID, resource: str='') -> str
```

**Purpose**: Builds the official object name for a source trigger. The name is derived from the watched source, owning conversation, and optional resource URL.

**Data flow**: It receives a binding name, conversation ID, and optional resource → if no resource is given, combines source and conversation → otherwise adds a digest of the resource → returns the trigger name.

**Call relations**: Trigger apply, listing, lookup, and link offers all use this so a trigger cannot be filed under a misleading or duplicate name.

*Call graph*: called by 4 (_apply_owned, _find, _member_rows, on_link_seen); 1 external calls (resource_digest).


##### `SourceTriggerObjects._member_rows`  (lines 861–894)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds rows shown when someone lists source triggers. Each row explains what source or resource is watched, where it reports, who created it, and whether it belongs to the current member.

**Data flow**: It reads reported triggers, reconstructs known source bindings, fetches owner emails, and checks conversation audience sharing → turns each trigger into an `OwnedRow` with fields useful for listing → returns the rows.

**Call relations**: The base object listing machinery calls this. It combines trigger store data with binding summaries and generated trigger names.

*Call graph*: calls 5 internal fn (_bindings_from_ext, _require_ext, _require_triggers, _trigger_summary, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 896–932)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed view of one source trigger. It shows the watched source, optional resource, delivery mode, timestamps, and links to related objects.

**Data flow**: It looks up the named trigger → confirms it matches the expected generation → creates links to the watched source and, for current delivery, the reporting conversation → returns an `ObjectDetail` or `None`.

**Call relations**: The base object `get` path calls this after visibility checks. It relies on `_find` to connect an object name to the trigger store row.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 934–949)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports status-like metadata for a source trigger, including conversation, source, resource, delivery mode, origin, owner email, and whether it is the caller's trigger.

**Data flow**: It finds the trigger by name → verifies the generation still matches → fetches the creator's email → returns a JSON-friendly dictionary.

**Call relations**: Status requests for trigger objects call this. It uses `_find` and authority information to personalize the `mine` field.

*Call graph*: calls 1 internal fn (_find); 2 external calls (authority_member_id, owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 951–998)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new source trigger for the current conversation, or accepts an identical no-op reapply. It refuses private sources and names that do not match the trigger's true identity.

**Data flow**: It receives the desired trigger spec → checks that the source is visible and shared → canonicalizes the resource URL if present → computes the expected trigger name → refuses mismatches or incompatible edits → writes the trigger row → rechecks the source still exists.

**Call relations**: The base object apply flow calls this for source trigger mutations. It calls `_watchable`, `trigger_name`, the trigger store, and `_binding_named` to avoid creating stale or misleading watchers.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 4 external calls (__init__, model_copy, authority_member_id, canonical_resource).


##### `SourceTriggerObjects._watchable`  (lines 1000–1014)

```
async def _watchable(self, ctx: ToolContext, source: str) -> ObjectDetail[SourceSpec]
```

**Purpose**: Checks whether the caller may watch a source. A watch only makes sense for shared sources, because private source changes do not flow to shared conversation alerts.

**Data flow**: It asks the source object store for the named source → raises an unknown-object error if hidden or absent → raises a clear error if the source is private → returns the source detail if it is shared.

**Call relations**: `SourceTriggerObjects._apply_owned` calls this before creating a trigger. It deliberately uses the source object's own `get` path so normal visibility rules stay in one place.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 1016–1020)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes a source trigger after confirming it is still the same row the caller meant to delete. This prevents deleting the wrong trigger if the object changed meanwhile.

**Data flow**: It receives the trigger name and expected owner generation → finds the current listed trigger → refuses if missing or generation differs → removes the trigger from the store.

**Call relations**: The base object delete flow calls this after permission checks. It uses `_find` for safety and `_require_triggers` for the actual removal.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 1022–1033)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds a listed trigger by its derived object name. It is the trigger equivalent of looking up a source binding by name.

**Data flow**: It reads all reported triggers from the trigger store → computes each trigger's official name → returns the matching listed trigger, or `None`.

**Call relations**: Trigger detail, status, and delete operations call this whenever they need to translate an object name back to a stored trigger row.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 1036–1089)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change and wakes the conversations whose source triggers match those changes. This is the live notification engine for source triggers.

**Data flow**: It receives a page-change hook payload → groups changes by source binding → finds triggers for each binding → keeps only shared, readable, resource-matching changes → calls `_fire_trigger` for each trigger that should wake.

**Call relations**: The manifest hook system calls this on page-change batches. It uses binding reconstruction, the trigger store, source-read permissions, `_about_resource`, and `_fire_trigger` to deliver only allowed alerts.

*Call graph*: calls 4 internal fn (_about_resource, _bindings_from_ext, _fire_trigger, _require_triggers); 2 external calls (__init__, suppress).


##### `_about_resource`  (lines 1092–1101)

```
def _about_resource(provider: str, trigger: SourceTrigger, changes: list[PageChange]) -> list[PageChange]
```

**Purpose**: Filters a list of page changes down to the ones relevant to a resource-specific trigger. If the trigger watches the whole source, it leaves the list unchanged.

**Data flow**: It receives a provider, trigger, and changes → if the trigger has no resource, returns all changes → otherwise keeps only changes whose page body links to the watched resource.

**Call relations**: `on_page_change` calls this after basic source and permission filtering, so resource-specific watches only wake for matching pages.

*Call graph*: called by 1 (on_page_change); 1 external calls (resource_matches).


##### `_trigger_summary`  (lines 1104–1110)

```
def _trigger_summary(binding: _Binding | None, trigger: SourceTrigger) -> str
```

**Purpose**: Creates a readable summary for a trigger. If the trigger watches one resource, the resource is emphasized because that is usually what the conversation cares about.

**Data flow**: It receives an optional binding and a trigger → uses the binding summary when available, otherwise the raw binding name → adds the resource prefix if present → returns a trimmed summary.

**Call relations**: Trigger listing calls this when building rows, and it uses `_Binding.summary` to keep source wording consistent with alerts.

*Call graph*: calls 1 internal fn (summary); called by 1 (_member_rows).


##### `on_link_seen`  (lines 1113–1181)

```
async def on_link_seen(ctx: HookContext) -> HookOutcome
```

**Purpose**: Notices useful links in a prompt or tool result and offers the conversation a ready-made source trigger to watch them. It does not create the trigger itself; it only suggests the manifest.

**Data flow**: It receives text from a user prompt or tool output → extracts HTTPS links → checks the conversation is a member-visible one → finds shared readable source bindings → canonicalizes links that are resources of those providers → skips already watched resources → returns injected watch-offer text with apply manifests.

**Call relations**: The hook system calls this after user prompts and tool use. It ties together link extraction, source visibility, trigger store checks, resource recognition, and `trigger_name`.

*Call graph*: calls 5 internal fn (_bindings_from_ext, _links_in, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, canonical_resource, safe_dump).


##### `_links_in`  (lines 1184–1192)

```
def _links_in(text: str) -> tuple[str, ...]
```

**Purpose**: Extracts unique HTTPS links from text in the order they appear. It also removes common punctuation that prose often leaves after a link.

**Data flow**: It scans the text with a link pattern → strips trailing punctuation such as commas or periods → keeps each link only once → returns the tuple of links.

**Call relations**: `on_link_seen` calls this before trying to match links to source resources.

*Call graph*: called by 1 (on_link_seen).


##### `_fire_trigger`  (lines 1195–1260)

```
async def _fire_trigger(ext: ExtensionContext, binding: _Binding, trigger: SourceTrigger, audience: Audience, authorized: list[PageChange]) -> None
```

**Purpose**: Delivers one trigger's authorized page changes to the right conversation or conversations. It creates the alert message and, when possible, writes a change log file with the detailed list.

**Data flow**: It receives the extension context, binding, trigger, audience, and changes → for `current` delivery, writes one log and invokes the owning conversation → for `per_page`, opens or reuses a stable conversation per page and invokes each one → sends idempotency keys so repeats do not create duplicate alerts.

**Call relations**: `on_page_change` calls this after filtering changes. It hands details to `_write_change_log`, `_alert_message`, and `_trigger_scope`, then uses the runtime to invoke agents.

*Call graph*: calls 5 internal fn (invoke, open_conversation, _alert_message, _trigger_scope, _write_change_log); called by 1 (on_page_change); 2 external calls (conversation_audience, authority_from_member_id).


##### `_trigger_scope`  (lines 1263–1271)

```
def _trigger_scope(binding: _Binding, trigger: SourceTrigger) -> str
```

**Purpose**: Builds a stable path segment that identifies what a trigger watches. This separates whole-source triggers from resource-specific triggers in logs and idempotency keys.

**Data flow**: It reads the binding name and optional trigger resource → returns just the binding name for whole-source triggers → otherwise returns the binding name plus a digest of the resource.

**Call relations**: `_fire_trigger` calls this when naming change-log locations and alert idempotency keys, preventing different trigger scopes from overwriting or suppressing each other.

*Call graph*: called by 1 (_fire_trigger); 1 external calls (resource_digest).


##### `_write_change_log`  (lines 1274–1308)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, directory: str, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the full list of changed pages to a runtime file for the alerted conversation. This keeps large change details out of the prompt while still making them available to the agent.

**Data flow**: It receives a conversation ID, directory, timestamp-like name, and page changes → if file storage is unavailable, returns `None` → otherwise writes one JSON line per changed page, prunes older runtime files in that directory, and returns the written path.

**Call relations**: `_fire_trigger` calls this before sending an alert. It uses `_disposition` to label each change as added, updated, or removed.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_fire_trigger); 1 external calls (dumps).


##### `_disposition`  (lines 1311–1317)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Labels a page change as `added`, `updated`, or `removed`. This gives alert text and log files simple human terms for what happened.

**Data flow**: It reads a page change → returns `removed` if it is a tombstone, `added` if creation and change times match, otherwise `updated`.

**Call relations**: Both `_write_change_log` and `_stream_counts` call this so logs and summaries use the same change labels.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1320–1334)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes how many pages changed in each stream. For example, it can say that pull requests had several additions and issues had one removal.

**Data flow**: It receives a list of page changes → counts each change by stream and disposition → formats those counts into a short summary string.

**Call relations**: `_alert_message` calls this to make the notification compact while the detailed page list stays in the log or references.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1337–1363)

```
def _alert_message(binding: _Binding, trigger: SourceTrigger, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to an agent when a watched source changes. It tells what changed, where to find the details, and what the agent should do next.

**Data flow**: It receives the binding, trigger, changes, and optional log path → chooses either named page references, a log-file instruction, or a list instruction depending on change count and storage → combines that with stream counts and watched-source wording → returns the final alert text.

**Call relations**: `_fire_trigger` calls this immediately before invoking a conversation. It uses `_stream_counts`, `_page_reference`, and the binding summary.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (_fire_trigger).


##### `_page_reference`  (lines 1366–1369)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an exact object reference plus a short title. This gives the agent something it can pass directly to `object_get`.

**Data flow**: It reads the page ID and title from a page change → trims the title or uses a fallback label → returns text like a page object reference followed by the label.

**Call relations**: `_alert_message` uses this when there are only a few changed pages, so the alert can name each one directly.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1372–1412)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Validates and normalizes the tenant API URL for providers that need one. This prevents unsafe or wrongly shaped URLs from being stored and used by sync code.

**Data flow**: It receives a provider and optional base URL → checks whether the connector has a fixed host or needs a tenant URL → parses the URL, rejects usernames, passwords, ports, queries, fragments, wrong hosts, or wrong paths → returns a normalized HTTPS URL or `None`.

**Call relations**: `SourceObjects._apply_owned` calls this before account resolution and registration, so invalid tenant addresses are refused before any source rows are written.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### Enrichment Providers
Enrichment providers normalize live or recorded people and company data while insulating callers from provider failures and unsafe inputs.

### `extensions/enrichment/ufo_ext_enrichment/providers.py`

`io_transport` · `startup and enrichment lookups`

This file is the bridge between the enrichment feature and its outside source of truth. When the system wants to learn more about a sign-up email or a company website, it can either call People Data Labs over the network or replay saved response bodies from a JSON file. The replay mode is useful for testing, demos, and repeatable deployments because it avoids making live network calls.

The file has three main layers. First, small parsing functions turn raw People Data Labs response bodies into the project’s own Person, Company, and Location objects. A 404-style body means “no match,” while a badly shaped body becomes an enrichment error.

Second, the provider classes offer a common shape: ask for a person by email, or a company by website. RecordedProvider reads only from a recordings file. PdlProvider can read from that same file first, like checking a notebook before making a phone call, and only contacts People Data Labs when no recording exists. Successful live responses, including clean “not found” answers, can then be written back to the file.

Third, provider_from_env chooses the provider at startup from environment variables. It refuses unsafe or unclear setups, such as recorded mode without a file, an unknown mode, or a live recording file placed inside the package being deployed.

#### Function details

##### `RateLimited.__init__`  (lines 80–82)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: This creates a special error for the case where People Data Labs says the system is asking too often. It can carry the provider’s suggested wait time, if the response includes one.

**Data flow**: It receives an error message and an optional number of seconds to wait. It stores the message as the normal exception text and saves the wait time on the error object. The result is an exception that callers can catch and use to pause future lookup attempts.

**Call relations**: PdlProvider._get creates this error when People Data Labs returns a rate-limit response. That lets the enrichment loop treat rate limiting differently from ordinary failures and try again later instead of treating the lookup as permanently broken.

*Call graph*: called by 1 (_get).


##### `Provider.source`  (lines 87–87)

```
def source(self) -> ProfileSource
```

**Purpose**: This defines the shared promise that every enrichment provider can say where its data came from. The source is later stored with the enriched profile so readers can tell whether it came from live People Data Labs data or a recording.

**Data flow**: A provider object is asked for its source. The concrete provider returns a short source label. Nothing else is changed.

**Call relations**: This is part of the Provider protocol, which is the common contract used by the rest of the enrichment system. RecordedProvider.source and PdlProvider.source provide the actual answers.


##### `Provider.person`  (lines 89–89)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: This defines the shared promise that every provider can look up a person from an email address. It allows the rest of the system to ask for enrichment without caring whether the answer comes from a live service or a recording.

**Data flow**: An email address goes in. A provider searches its chosen source and returns either a Person object or nothing if there is no known match. The protocol itself only describes this behavior; concrete providers do the work.

**Call relations**: This is the common interface implemented by RecordedProvider.person and PdlProvider.person. Callers can use either provider through the same method name.


##### `Provider.company`  (lines 91–91)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: This defines the shared promise that every provider can look up a company from a website. It keeps the rest of the enrichment code independent from the details of recordings and HTTP calls.

**Data flow**: A website string goes in. A provider searches its source and returns either a Company object or nothing if no match is known. The protocol only states the rule; concrete providers perform the lookup.

**Call relations**: This is the common interface implemented by RecordedProvider.company and PdlProvider.company. It lets higher-level enrichment code call one method without branching on provider type.


##### `person_from_body`  (lines 137–148)

```
def person_from_body(body: object) -> Person | None
```

**Purpose**: This turns a raw People Data Labs person response into the project’s Person record. It is used for both live responses and recorded responses so the two modes interpret data the same way.

**Data flow**: A raw response body goes in. The function first checks whether it is a clean “not found” response; if so, it returns nothing. Otherwise it validates the body’s shape, extracts the person fields and match likelihood, and returns a Person object. If the body is malformed, it raises an enrichment error.

**Call relations**: RecordedProvider.person uses this after reading a saved body from disk, and PdlProvider.person uses it after getting a body from the recording cache or People Data Labs. It relies on _is_not_found to recognize clean misses before building a Person.

*Call graph*: calls 1 internal fn (_is_not_found); called by 2 (person, person); 2 external calls (__init__, __init__).


##### `company_from_body`  (lines 151–163)

```
def company_from_body(body: object) -> Company | None
```

**Purpose**: This turns a raw People Data Labs company response into the project’s Company record. It also converts the nested location data into the project’s Location record when location data is present.

**Data flow**: A raw response body goes in. The function returns nothing for a clean “not found” body. For a match, it validates the response, separates out the location if one exists, builds a Location object, and then builds a Company object. If the response does not have the expected shape, it raises an enrichment error.

**Call relations**: RecordedProvider.company uses this for saved bodies, and PdlProvider.company uses it for live or cached bodies. It shares the same _is_not_found helper as person_from_body so misses are treated consistently.

*Call graph*: calls 1 internal fn (_is_not_found); called by 2 (company, company); 3 external calls (__init__, __init__, __init__).


##### `_is_not_found`  (lines 166–167)

```
def _is_not_found(body: object) -> bool
```

**Purpose**: This small helper recognizes People Data Labs’ “no match found” response. It keeps the parsing functions from treating a clean miss as a broken response.

**Data flow**: A raw body goes in. The function checks whether it is a dictionary with a status value of 404. It returns true for that shape and false otherwise.

**Call relations**: person_from_body and company_from_body call this before validating a response as a successful match. It is the fork in the road between “no result” and “parse the data.”

*Call graph*: called by 2 (company_from_body, person_from_body).


##### `Recordings.body`  (lines 180–182)

```
async def body(self, key: str) -> object | None
```

**Purpose**: This reads one saved provider response from the recordings file. It lets recorded mode, and live mode with a recording cache, reuse an earlier response without making a network call.

**Data flow**: A lookup key such as a person email key or company website key goes in. The function reads the JSON file in a worker thread so the async program is not blocked by disk work, then returns the body stored under that key, or nothing if the key is absent.

**Call relations**: RecordedProvider.person, RecordedProvider.company, and PdlProvider._body use this when they want to check whether a lookup has already been recorded. It delegates the actual file reading to the internal _read method through asyncio.to_thread.

*Call graph*: 1 external calls (to_thread).


##### `Recordings.append`  (lines 184–186)

```
async def append(self, key: str, body: object) -> None
```

**Purpose**: This adds or replaces one response body in the recordings file. It lets live People Data Labs lookups become replayable later.

**Data flow**: A lookup key and raw response body go in. The function takes an async lock, which is a guard that stops two tasks writing at the same time, then performs the disk write in a worker thread. Afterward the recordings file contains the new body under that key.

**Call relations**: PdlProvider._body calls this after a successful live fetch when recordings are enabled. It hands off to _append for the actual read-modify-write operation.

*Call graph*: 1 external calls (to_thread).


##### `Recordings._read`  (lines 188–194)

```
def _read(self) -> dict[str, object]
```

**Purpose**: This performs the actual loading of the recordings JSON file. It treats a missing file as an empty set of recordings, but rejects a file whose top-level content is not an object.

**Data flow**: It reads from the Recordings object’s path. If the file does not exist, it returns an empty dictionary. If the file exists, it parses the JSON text and returns the dictionary of recorded bodies. If the parsed JSON is not a dictionary, it raises an enrichment error.

**Call relations**: Recordings.body reaches this indirectly through a worker thread, and Recordings._append calls it before updating the file. This is the single place that defines what a valid recordings file looks like.

*Call graph*: called by 1 (_append); 2 external calls (__init__, loads).


##### `Recordings._append`  (lines 196–201)

```
def _append(self, key: str, body: object) -> None
```

**Purpose**: This rewrites the recordings file with one new saved response. It writes through a temporary file first so readers do not see a half-written JSON file.

**Data flow**: A key and response body go in. The function reads the current recordings, updates the dictionary, writes the whole dictionary to a temporary file beside the real one, and then replaces the real file with the temporary file. The output is an updated recordings file on disk.

**Call relations**: Recordings.append calls this while holding the write lock. It calls Recordings._read first so it preserves existing recordings before adding the new one.

*Call graph*: calls 1 internal fn (_read); 2 external calls (dumps, getpid).


##### `RecordedProvider.source`  (lines 213–214)

```
def source(self) -> ProfileSource
```

**Purpose**: This reports that the provider is using recorded data. That label helps the rest of the system mark where an enriched profile came from.

**Data flow**: The RecordedProvider object is asked for its source. It returns the fixed text value “recorded.” Nothing is read from disk and nothing changes.

**Call relations**: This fulfills the Provider.source contract for RecordedProvider. Higher-level code can ask any provider for its source without needing to know which provider class it has.


##### `RecordedProvider.person`  (lines 216–218)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: This looks up a person using only the recordings file. It never contacts People Data Labs, so it is safe and repeatable for offline replay.

**Data flow**: An email address goes in. The function builds the matching recording key, reads the saved body for that key, and returns nothing if no body is recorded. If a body exists, it passes that body to person_from_body and returns the resulting Person or no-match result.

**Call relations**: This is the recorded implementation of Provider.person. It depends on Recordings.body for file lookup and person_from_body for interpreting the saved provider response.

*Call graph*: calls 1 internal fn (person_from_body).


##### `RecordedProvider.company`  (lines 220–222)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: This looks up a company using only the recordings file. It provides replayed company enrichment without making any network request.

**Data flow**: A website string goes in. The function builds the matching recording key, reads the saved body for that key, and returns nothing if no body is recorded. If a body exists, it sends that body to company_from_body and returns the resulting Company or no-match result.

**Call relations**: This is the recorded implementation of Provider.company. It uses Recordings.body to fetch the raw saved response and company_from_body to turn that response into the project’s Company shape.

*Call graph*: calls 1 internal fn (company_from_body).


##### `PdlProvider.source`  (lines 236–237)

```
def source(self) -> ProfileSource
```

**Purpose**: This reports that the provider is using People Data Labs as its source. Even if a response is replayed from the optional recording cache, this provider represents the live PDL-backed mode.

**Data flow**: The PdlProvider object is asked for its source. It returns the fixed text value “pdl.” No network or file work happens.

**Call relations**: This fulfills the Provider.source contract for PdlProvider. The rest of the enrichment system can store or display the source label without knowing provider internals.


##### `PdlProvider.person`  (lines 239–247)

```
async def person(self, email: str) -> Person | None
```

**Purpose**: This looks up a person in People Data Labs by email, optionally checking a recordings file first. It also prevents obviously invalid overlong email strings from being sent to the provider.

**Data flow**: An email address goes in. The function checks that it is not longer than the allowed email length, builds the People Data Labs request parameters for person enrichment, and asks _body for the raw response. It then turns that raw response into a Person or no-match result with person_from_body. If the email is too long, it raises an enrichment error.

**Call relations**: This is the live-mode implementation of Provider.person. It calls PdlProvider._body to get a cached or live response, then hands the response to person_from_body so parsing stays shared with recorded mode.

*Call graph*: calls 2 internal fn (_body, person_from_body); 1 external calls (__init__).


##### `PdlProvider.company`  (lines 249–257)

```
async def company(self, website: str) -> Company | None
```

**Purpose**: This looks up a company in People Data Labs by website, optionally checking a recordings file first. It blocks overlong website strings before they are sent to the external service.

**Data flow**: A website string goes in. The function checks its length, builds the People Data Labs request parameters for company enrichment, and asks _body for the raw response. It then converts that raw response into a Company or no-match result with company_from_body. If the website is too long, it raises an enrichment error.

**Call relations**: This is the live-mode implementation of Provider.company. It relies on PdlProvider._body for cache-or-fetch behavior and on company_from_body for interpreting the response.

*Call graph*: calls 2 internal fn (_body, company_from_body); 1 external calls (__init__).


##### `PdlProvider._body`  (lines 259–267)

```
async def _body(self, key: str, path: str, params: dict[str, str]) -> object
```

**Purpose**: This is the read-through cache step for live People Data Labs mode. It checks the recordings file first when one is configured, and only calls the live API if there is no saved body.

**Data flow**: A recording key, API path, and request parameters go in. If recordings are enabled and the key exists, the saved body comes out immediately. Otherwise the function calls _get to fetch from People Data Labs, appends the fetched body to recordings when enabled, and returns the body.

**Call relations**: PdlProvider.person and PdlProvider.company call this before parsing responses. It calls PdlProvider._get only when the recordings file cannot answer the lookup.

*Call graph*: calls 1 internal fn (_get); called by 2 (company, person).


##### `PdlProvider._get`  (lines 269–298)

```
async def _get(self, path: str, params: dict[str, str]) -> object
```

**Purpose**: This performs the actual HTTP request to People Data Labs. It turns network errors, missing credentials, rate limits, bad JSON, and provider failures into clear enrichment errors for callers.

**Data flow**: An API path and query parameters go in. The function reads the deploy’s API key, opens an async HTTP client, sends a GET request with the key header, and examines the response. A 200 or 404 response returns parsed JSON. A 429 response raises RateLimited with any usable wait time. Other failures raise an enrichment error containing the status and a shortened response body.

**Call relations**: PdlProvider._body calls this when no recording is available. It uses deploy_env to find the API key, httpx.AsyncClient to make the network request, _retry_after to interpret rate-limit timing, and RateLimited or EnrichmentError to report problems upward.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 1 (_body); 3 external calls (__init__, AsyncClient, deploy_env).


##### `_retry_after`  (lines 301–310)

```
def _retry_after(header: str | None) -> float | None
```

**Purpose**: This interprets a Retry-After response header when People Data Labs rate-limits a request. It only accepts simple positive second counts.

**Data flow**: A header string, or nothing, goes in. If it contains a positive number, that number comes out as seconds to wait. If it is missing, not a number, zero, negative, or a date format, the function returns nothing.

**Call relations**: PdlProvider._get calls this when building a RateLimited error. The result gives callers a useful wait time when the provider stated one clearly.

*Call graph*: called by 1 (_get).


##### `provider_from_env`  (lines 313–348)

```
def provider_from_env() -> Provider | None
```

**Purpose**: This chooses the enrichment provider at startup from environment variables. It prevents the app from starting in confusing or unsafe enrichment modes.

**Data flow**: It reads the provider mode, recordings file path, and deploy API key from the environment. In live PDL mode, it returns no provider if there is no key, returns a plain PdlProvider if no recordings file is named, or returns a PdlProvider with recordings if a safe external file is named. In recorded mode, it requires an existing recordings file and returns a RecordedProvider. For unknown modes or invalid paths, it raises a startup error.

**Call relations**: Startup code calls this to decide whether enrichment can run and how. It constructs PdlProvider, RecordedProvider, and Recordings objects as needed, and uses warn to make important operational choices visible, such as running without a key or using recorded mode.

*Call graph*: 6 external calls (__init__, __init__, __init__, Path, deploy_env, warn).


### Memory Indexing
The Turbopuffer extension implements an external semantic and keyword-backed memory index for stored text chunks.

### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup and memory indexing/search requests`

This extension is the bridge between UFO’s memory system and Turbopuffer’s HTTP API. UFO breaks content into chunks, gives each chunk an embedding (a list of numbers that represents meaning), and later needs to find the most relevant chunks again. Without this file, a workspace configured to use the Turbopuffer backend could not save, search, or delete indexed memory.

The file registers a backend named "turbopuffer" and declares that it needs a Turbopuffer API key. At runtime, the index reads that key, builds HTTP requests, and sends them directly to Turbopuffer. Each workspace gets its own namespace, like a separate labeled drawer, so one workspace’s chunks do not mix with another’s.

The backend supports two search styles. Vector search finds chunks with similar embeddings, which is like looking for ideas with similar meaning. Lexical search uses BM25, a common word-based ranking method, which is like looking for matching terms in a document. Both searches are narrowed by owner kind and subject so the caller only recalls the right slice of memory.

The file also takes care of practical details: shortening SHA-256 chunk IDs to fit Turbopuffer limits, batching large writes and deletes, paging through stored chunks when deleting or pruning, and keeping separate HTTP clients per async event loop so connection pools are not accidentally shared across incompatible loops.

#### Function details

##### `turbopuffer_id`  (lines 49–55)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: This turns UFO’s chunk digest into a document ID that Turbopuffer will accept. Normal SHA-256 digests are shortened using URL-safe base64 so they stay under Turbopuffer’s ID length limit.

**Data flow**: It receives a chunk digest string. If the string looks like a SHA-256 digest, it converts the raw bytes into a shorter URL-safe text ID; otherwise it leaves the ID unchanged. The result is the ID used when writing or deleting documents in Turbopuffer.

**Call relations**: Writing and cleanup paths call this before talking to Turbopuffer. `upsert_body` uses it when preparing new documents, and `TurbopufferIndex.delete` and `TurbopufferIndex.prune` use it when turning stored chunk digests into delete IDs.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 58–67)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: This reverses the shortened Turbopuffer document ID back into UFO’s original chunk digest when possible. It lets search results return the same kind of digest that UFO originally stored.

**Data flow**: It receives an ID from Turbopuffer. If the ID has the expected shortened length and can be decoded, it returns a `sha256:` digest string; if not, it passes the ID through unchanged. The output is safe for UFO’s normal chunk and hit objects.

**Call relations**: Read paths call this when turning Turbopuffer rows back into UFO data. `hit_from_row` uses it for search results, and `TurbopufferIndex._scope_chunks` uses it while listing chunks for delete or prune operations.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 70–86)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: This builds the request body used to insert or replace a batch of chunks in Turbopuffer. It lays the data out in the column-based shape Turbopuffer expects.

**Data flow**: It receives a tuple of `Chunk` objects. It extracts IDs, embeddings, ownership fields, subjects, ordinals, and text into parallel lists, then adds settings for cosine-distance vector search and full-text search on the text field. The result is a JSON-ready dictionary for an upsert request.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch. Inside the body-building step, it calls `turbopuffer_id` so every chunk has a Turbopuffer-friendly document ID before the HTTP request is sent.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 89–105)

```
def bm25_query(text: str) -> str
```

**Purpose**: This cleans and shortens a word-based search query before sending it to Turbopuffer’s BM25 search. BM25 is a ranking method that scores documents by useful matching terms.

**Data flow**: It receives raw query text. It keeps only tokens that contain a meaningful run of letters or digits, removes queries made only of punctuation or one-character fragments, and trims long text to Turbopuffer’s byte limit without intentionally cutting through a term. The output is either a usable lexical query string or an empty string meaning “do not run lexical search.”

**Call relations**: `TurbopufferIndex.lexical` calls this before any word-based search. If it returns nothing, lexical search stops there, which prevents meaningless text from matching too much of the namespace.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 108–112)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: This creates the filter used for normal search queries. It makes sure results come from the requested owner kind and from one of the allowed subjects.

**Data flow**: It receives an owner kind and a set of subjects. It builds Turbopuffer’s filter structure requiring that owner kind and any subject from the sorted subject set. The output is included in the search request body.

**Call relations**: `TurbopufferIndex._query` calls this whenever lexical or vector search is sent to Turbopuffer. It is the gate that keeps broad searches scoped to the correct part of memory.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 115–122)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: This creates the filter used when checking, listing, deleting, or pruning all chunks for one indexed owner. It can also ask for IDs after a certain point, which supports paging through large result sets.

**Data flow**: It receives an `IndexScope`, which names an owner kind and owner ID, plus an optional last-seen ID. It builds a Turbopuffer filter requiring that scope, and adds an ID-greater-than condition when continuing a paged scan. The output is a filter list for query requests.

**Call relations**: `TurbopufferIndex.has_chunks` uses this for a quick existence check. `TurbopufferIndex._scope_chunks` uses it repeatedly while walking through all chunks in a scope.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 125–134)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: This turns one Turbopuffer result row into UFO’s standard `Hit` object. A hit is the search result shape the rest of UFO expects.

**Data flow**: It receives a row dictionary from Turbopuffer and a score already chosen by the caller. It converts fields like ID, owner, subject, ordinal, and text into the right Python types, restores the chunk digest format, and returns a `Hit` carrying the score.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` call this after receiving rows. It calls `chunk_digest_from_id` so the external document ID becomes UFO’s internal digest again.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 137–143)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: This converts Turbopuffer’s vector-search ranking into a score where larger means better. That matches what UFO’s recall logic expects.

**Data flow**: It receives a result row, the row’s position in the result list, and the total number of rows. If Turbopuffer provides a cosine distance, it turns that into similarity by subtracting from 1; otherwise it falls back to a descending rank score. The output is a numeric score.

**Call relations**: `TurbopufferIndex.vector` calls this for every vector-search row before creating `Hit` objects. It gives the later fusion or ranking code a consistent “higher is closer” signal.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex._api`  (lines 160–181)

```
def _api(self) -> httpx.AsyncClient
```

**Purpose**: This returns the HTTP client used to call Turbopuffer from the current async event loop. It avoids sharing one connection pool across different loops, which can break async networking.

**Data flow**: It looks at the currently running event loop. It removes records for loops that have closed, reuses an existing `httpx.AsyncClient` for the live loop when available, or creates and stores a new one with the Turbopuffer base URL and timeout. The output is an async HTTP client ready for requests.

**Call relations**: Every method that sends requests calls this shortly before contacting Turbopuffer. It is the common doorway for upserts, deletes, pruning, existence checks, scope scans, and search queries.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert); 2 external calls (get_running_loop, AsyncClient).


##### `TurbopufferIndex.upsert`  (lines 183–194)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This writes chunks into Turbopuffer, replacing existing documents with the same IDs if needed. It is used when UFO has new or updated memory chunks to index.

**Data flow**: It receives a tuple of chunks. It drops chunks that have no embedding, gets authorization headers, splits the remaining chunks into write-sized batches, builds a Turbopuffer upsert body for each batch, and posts each one. It returns nothing, but Turbopuffer’s stored namespace is updated.

**Call relations**: Indexing code calls this when chunks should become searchable. It relies on `_auth` for the Bearer token, `_path` for the workspace namespace, `_api` for the HTTP client, and `upsert_body` for the request payload.

*Call graph*: calls 4 internal fn (_api, _auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 196–204)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes all indexed chunks for a given scope. It is used when an owner’s indexed memory should be fully dropped.

**Data flow**: It receives an `IndexScope`. It gets authorization headers, lists all chunks currently stored for that scope, converts their digests into Turbopuffer IDs, sends batched delete requests, and returns nothing after Turbopuffer has accepted them.

**Call relations**: Deletion flows call this for full cleanup. It first asks `_scope_chunks` what exists, then uses `turbopuffer_id`, `_api`, `_path`, and `_auth` to send the actual delete batches.

*Call graph*: calls 5 internal fn (_api, _auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 206–216)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes only the chunks in a scope that are not in a keep-set. It is useful after re-chunking content, so old chunks do not linger as stale search results.

**Data flow**: It receives an `IndexScope` and a frozen set of chunk digests to keep. It lists all chunks in that scope, filters out the ones that should remain, converts the rest to Turbopuffer IDs, and sends batched delete requests. The result is that only unwanted stored chunks are removed.

**Call relations**: Maintenance or reindexing code calls this when a scope has been refreshed. Like `delete`, it depends on `_scope_chunks` to inspect the current namespace, then uses the shared auth, path, ID conversion, and HTTP request helpers to remove extras.

*Call graph*: calls 5 internal fn (_api, _auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 218–226)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This quickly checks whether Turbopuffer has any chunks stored for one scope. It avoids downloading a whole scope when the caller only needs yes or no.

**Data flow**: It receives an `IndexScope`. It sends a query asking for just one matching row, filtered by that scope. If the namespace is missing, it returns `false`; otherwise it raises for real HTTP errors and returns whether any rows came back.

**Call relations**: Callers use this as a lightweight existence check. It builds its filter with `scope_filters`, gets credentials through `_auth`, chooses the namespace with `_path`, and sends the request through `_api`.

*Call graph*: calls 4 internal fn (_api, _auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 228–237)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs word-based search over stored chunk text. It is the BM25 side of recall, useful when exact terms or phrases matter.

**Data flow**: It receives query text, allowed subjects, an owner kind, and a result limit. It cleans the query with `bm25_query`; if there is no useful text or no subjects, it returns an empty tuple. Otherwise it asks Turbopuffer to rank by text BM25 and converts rows into `Hit` objects with rank-based scores.

**Call relations**: Search orchestration calls this when it wants the lexical leg of recall. It delegates the HTTP query to `_query`, then uses `hit_from_row` to translate Turbopuffer rows into UFO search hits.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 239–248)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs meaning-based search using an embedding vector. It finds chunks whose stored embeddings are close to the query embedding.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a result limit. If the embedding or subjects are empty, it returns no hits. Otherwise it asks Turbopuffer for approximate nearest-neighbor vector search, scores each row, drops non-positive scores, and returns `Hit` objects.

**Call relations**: Search orchestration calls this when it wants the semantic leg of recall. It sends the shared query through `_query`, uses `vector_score` to normalize ranking, and uses `hit_from_row` to build the standard result objects.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 250–265)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: This is the shared request builder for lexical and vector searches. It sends a scoped Turbopuffer query and returns raw result rows.

**Data flow**: It receives a Turbopuffer `rank_by` instruction, an owner kind, allowed subjects, and a limit. It builds a request body with the ranking rule, limit, requested attributes, and scope filters, sends it to the namespace query endpoint, and returns the response rows. A missing namespace is treated as an empty result set.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this instead of duplicating HTTP request code. It calls `query_filters`, `_auth`, `_path`, and `_api` to assemble and send the request.

*Call graph*: calls 4 internal fn (_api, _auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 267–295)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: This lists all chunks stored for one scope, page by page. It is used before deleting or pruning, because Turbopuffer deletes by document ID.

**Data flow**: It receives an `IndexScope` and already-built authorization headers. It repeatedly queries Turbopuffer for a page of rows sorted by ID, converts each row into a lightweight `Chunk`, and continues from the last ID until a short page means there are no more. It returns the full list of chunks found, or an empty list if the namespace does not exist.

**Call relations**: `TurbopufferIndex.delete` and `TurbopufferIndex.prune` call this to discover what needs to be removed. It uses `scope_filters` for each page, `_path` and `_api` for the request, and `chunk_digest_from_id` when rebuilding chunk identities.

*Call graph*: calls 4 internal fn (_api, _path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 297–299)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: This builds the authorization header required by Turbopuffer. It reads the workspace’s configured API key and formats it as a Bearer token.

**Data flow**: It asks the credential reader for the `turbopuffer_api_key` value. It then returns a headers dictionary containing `Authorization: Bearer <key>`. It does not change stored state.

**Call relations**: All request-sending paths call this before contacting Turbopuffer. It supplies the credentials used by upsert, delete, prune, existence checks, and shared search queries.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 301–302)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: This builds the URL path for the current workspace’s Turbopuffer namespace. The namespace keeps one workspace’s indexed chunks separate from others.

**Data flow**: It receives an optional suffix such as `/query`. It combines the fixed namespace prefix, the credential context’s workspace ID, and the suffix into a Turbopuffer API path. The output is a string used in HTTP calls.

**Call relations**: Every Turbopuffer request path goes through this helper. Search, listing, existence checks, writes, deletes, and pruning all use it so they consistently target the same workspace namespace.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 305–322)

```
def manifest() -> Manifest
```

**Purpose**: This tells UFO how to discover and load this extension. It declares the extension name, version, needed credential, and the factory that builds the Turbopuffer index backend.

**Data flow**: It takes no input. It creates a credential slot for the Turbopuffer API key and an index backend specification whose factory constructs `TurbopufferIndex` from the runtime context. It returns a `Manifest` object for UFO’s extension loader.

**Call relations**: UFO calls this during extension registration or startup. The returned manifest is how the core system learns that selecting the `turbopuffer` index backend should create a `TurbopufferIndex` with access to workspace credentials.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-credential-connections` — The encrypted outside-account credentials, reusable connections, and grants that let agents use them.
- `reg-source-index` — The stored external sources, synced pages, permissions, indexing status, and retry/backoff state.
- `reg-memory-store` — The remembered facts and searchable memory chunks that can be retrieved or condensed later.
- `reg-enrichment-profile-store` — Cached or recorded person/company enrichment data together with permissions controlling who may use it.
- `reg-eval-fixture-state` — Deterministic fake-service datasets and recorded mutations used by evaluation and local-development connectors.
- `reg-shared-infra-clients` — Long-lived non-database infrastructure clients and connection pools such as Redis, HTTP, provider, and service clients shared by workers and request handlers.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.
