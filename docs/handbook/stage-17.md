# Retrieval, research, memory, indexing, and embeddings  `stage-17`

This stage is the system’s recall library. It works behind the scenes while conversations run, so agents can find saved facts, synced pages, and web evidence instead of relying only on the current prompt. The core search and memory files define common “plug shapes”: one for web search and fetching, one for memory lookup, and one for splitting text into searchable chunks. Different backends can then fit into those shapes. The default index stores text chunks locally, while Turbopuffer can store and search them in an external service. OpenAI embeddings turn text into number lists that capture rough meaning, so searches can match ideas, not just exact words. The memory store saves durable facts, indexes them, and searches them later. Its condenser turns raw pages or old facts into cleaner long-term memories, while memory objects, events, and package files define safe access and shared names. Synced source pages provide read-only documents that can become memories. For web research, Exa supplies search and page fetching, research tools expose that to agents, and observations save found sources so a conversation can show its evidence later.

## Files in this stage

### Web search access
Exa and the research tool layer provide web search and page fetching through the shared search contract.

### `extensions/exa/ufo_ext_exa.py`

`io_transport` · `request handling, when a research/search tool chooses the Exa backend`

This file is the bridge between the project’s general search interface and Exa, an external web search and content API. Other parts of the system ask for “search results” or “the contents of this page” using project-owned types. This file translates those requests into Exa’s expected HTTP requests, sends them to api.exa.ai, and translates Exa’s replies back into the project’s standard result objects.

A key safety point is where the API key lives. The Exa key is read by the host process through a credential access object. It is put only into the outgoing HTTP header sent to Exa. It is not injected into the sandboxed tool environment. In everyday terms, the host acts like a clerk who can use the key at the counter, but never hands the key to the customer.

The main class, ExaSearchProvider, offers two abilities: search and fetch. Search builds an Exa search request from a SearchQuery, including optional domain filters, dates, or special verticals like academic and people search. Fetch asks Exa for the text of one URL, optionally with a summary prompt or a forced fresh crawl. Helper functions check that Exa’s response really contains a results list and safely convert optional fields.

#### Function details

##### `ExaSearchProvider.search`  (lines 54–56)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs one web search through Exa and returns the results in the project’s normal SearchResults shape. Someone uses this when they want search without caring which external search service is behind it.

**Data flow**: It receives a SearchQuery containing the search text, result count, and optional filters. It turns that query into an Exa request body, sends it to Exa’s /search endpoint, checks that the reply has a usable results list, converts each Exa item into a SearchHit, and returns all hits inside a SearchResults object.

**Call relations**: This is the public search path of ExaSearchProvider. It relies on _search_body to prepare Exa’s request format, _post to make the authenticated HTTP call, _results to validate the response, and _hit to translate each returned item into the project’s standard search-result format.

*Call graph*: calls 4 internal fn (_hit, _post, _search_body, _results); 1 external calls (__init__).


##### `ExaSearchProvider.fetch`  (lines 58–74)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches readable text for a single URL through Exa’s contents API. It is used when the system has a web address and wants page content, and optionally a short Exa-generated summary.

**Data flow**: It receives a FetchRequest with a URL, an optional character limit, an optional summary prompt, and an optional force-refresh flag. It builds an Exa /contents request, caps the text length at the file’s maximum, sends the request, reads the first result if one exists, and returns a FetchedPage with the URL, text, and optional summary.

**Call relations**: This is the public page-fetch path of ExaSearchProvider. It hands the actual network call to _post, uses _results to make sure Exa’s response has the expected list shape, and uses _opt_str so the summary is included only when Exa returned a real string.

*Call graph*: calls 3 internal fn (_post, _opt_str, _results); 1 external calls (__init__).


##### `ExaSearchProvider._search_body`  (lines 77–94)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON body that Exa expects for a search request. This keeps the public search method simple and keeps Exa-specific request rules in one place.

**Data flow**: It receives a SearchQuery. It starts with the basic query text and number of results, then adds different options depending on the query: normal searches can include text snippets, highlights, allowed domains, and date limits; vertical searches use shorter text and may map project vertical names to Exa category names. It returns a dictionary ready to be sent as JSON.

**Call relations**: ExaSearchProvider.search calls this before contacting Exa. The returned body is passed directly into _post, so this function is the translation step between the project’s search request model and Exa’s request format.

*Call graph*: called by 1 (search).


##### `ExaSearchProvider._hit`  (lines 97–107)

```
def _hit(item: dict[str, object]) -> SearchHit
```

**Purpose**: Turns one raw Exa search result into the project’s standard SearchHit object. This gives the rest of the system a consistent result shape, even though Exa’s response format is its own.

**Data flow**: It receives one result dictionary from Exa. It pulls out the URL, title, text, published date, and highlights, replacing missing text-like fields with safe defaults and keeping highlights only if they are strings. It returns a SearchHit.

**Call relations**: ExaSearchProvider.search uses this for every valid result returned by _results. It also uses _opt_str to avoid treating non-string published-date values as real dates.

*Call graph*: calls 1 internal fn (_opt_str); called by 1 (search); 1 external calls (__init__).


##### `ExaSearchProvider._post`  (lines 109–117)

```
async def _post(self, path: str, body: dict[str, Json]) -> object
```

**Purpose**: Sends an authenticated HTTP POST request to Exa and returns the decoded JSON reply. It is the single place where the Exa API key is read and attached to an outgoing request.

**Data flow**: It receives an API path such as /search or /contents and a JSON-ready request body. It asks the credential system for the Exa API key, creates an asynchronous HTTP client, posts the body to Exa with the key in the x-api-key header, and returns the parsed JSON response. If Exa replies with an error status, it raises ExaError with the status and body text.

**Call relations**: Both search and fetch depend on this function for the actual network trip to Exa. It uses httpx.AsyncClient, and tests can provide a custom transport through the provider so calls can be faked without reaching the real Exa service.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_results`  (lines 120–124)

```
def _results(payload: object) -> list[dict[str, object]]
```

**Purpose**: Checks that an Exa response contains a results list and returns only the entries that look like dictionaries. It prevents a malformed response from being mistaken for a valid empty answer.

**Data flow**: It receives the decoded response body from Exa. If the body is a dictionary with a results field that is a list, it filters that list down to dictionary items and returns them. If the results list is missing or not a list, it raises ExaError.

**Call relations**: Both ExaSearchProvider.search and ExaSearchProvider.fetch call this after _post returns. It acts as the gatekeeper between raw Exa JSON and the project’s typed result objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `_opt_str`  (lines 127–128)

```
def _opt_str(value: object) -> str | None
```

**Purpose**: Returns a value only if it is actually a string; otherwise it returns None. This is a small safety helper for optional text fields that may be absent or oddly typed in Exa’s response.

**Data flow**: It receives any value. If the value is a string, it passes that string through unchanged. If it is anything else, such as null, a number, or a dictionary, it returns None.

**Call relations**: ExaSearchProvider._hit uses this for published dates, and ExaSearchProvider.fetch uses it for summaries. This keeps those fields from carrying unexpected non-text values into the project’s standard objects.

*Call graph*: called by 2 (_hit, fetch).


##### `manifest`  (lines 131–144)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system: its name, version, required Exa credential, and the search provider it can build. Without this, the system would not know how to discover or select the Exa backend.

**Data flow**: It takes no input. It creates a Manifest that names the extension, declares the exa_api_key credential slot, and registers a search provider specification for the exa backend. That provider specification includes a builder that receives credentials and returns an ExaSearchProvider.

**Call relations**: The extension loading system calls this to learn what the file offers. The manifest connects configuration such as selecting the exa search provider to the actual ExaSearchProvider class used later during search and fetch requests.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `request handling`

This file is the bridge between an agent asking for web research and the outside search service that actually does the work. Without it, the agent would not have a safe, consistent way to search the web or read public URLs during a turn.

It defines three tool inputs using Pydantic models, which are structured forms that check incoming arguments before the tool runs. For example, web search limits the number of queries and results, fetch requires a public URL, and vertical search only allows known categories such as image, people, academic, video, or shopping.

When a tool runs, it first looks up the turn’s configured SearchProvider, meaning the search backend chosen by the host process. This matters because credentials stay on the host side; the workspace or sandbox does not see the search provider’s secret keys. The tool then builds a search or fetch request, waits for the provider’s answer, records observations for the activity/history system when available, and returns a ToolResult containing JSON text.

A key safety detail is fetch provenance. Fetched pages come from the provider’s crawler session, not from the user’s workspace session. So if a fetched page appears logged in or personalized, that identity belongs to the crawler, not the user. The returned content explicitly says this.

#### Function details

##### `_provider`  (lines 145–148)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: Gets the search provider for the current tool call. It deliberately fails loudly if no provider is configured, because a research tool cannot do useful work without a backend to search or fetch from.

**Data flow**: It receives the tool context, reads the context’s search_provider field, and either returns that provider or raises an error. Nothing is changed; it is a gatekeeper that makes sure later code has a real backend to call.

**Call relations**: The web search, vertical search, and URL fetch flows all call this first. If it returns a provider, those functions continue by sending search or fetch requests; if it raises an error, the tool call stops early instead of pretending research succeeded.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 151–165)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: Turns search hits into a JSON string that the agent can consume. It standardizes the shape of search output so regular web search and vertical search return the same kind of result package.

**Data flow**: It takes a list of search hits and an optional direct answer. For each hit, it copies the URL, title, snippet text, published date, and highlights into a plain dictionary. It then wraps those in a top-level results object, adds the answer if present, and serializes the whole thing into JSON text.

**Call relations**: Both _search_web and _search_vertical call this after they receive results from the provider. It is the final formatting step before those functions place the text into a ToolResult for the agent.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 168–186)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: Runs one or more ordinary web searches for current or factual information. It is used when the agent needs pages, snippets, and optionally a provider-supplied answer for general web research.

**Data flow**: It receives the tool context and validated search arguments. It gets the provider, then loops through each requested query. For each query it builds a SearchQuery with result limits, date filters, and allowed domains, sends it to the provider, and collects the returned hits. It keeps the first provider answer it sees, records the hits if an extension recorder is available, and returns a ToolResult containing JSON search results.

**Call relations**: This is the handler registered for the search_web tool. Its flow starts by calling _provider, hands each query to the external SearchProvider, optionally hands the combined hits to record_search_hits for history or observation logging, then calls _results_json to prepare the final response.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 189–210)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: Fetches the contents of a public HTTP or HTTPS URL through the configured search provider. It is used when the agent needs to read a specific page rather than search for pages.

**Data flow**: It receives the tool context and validated fetch arguments. It gets the provider and first checks whether that provider can fetch URLs at all. If fetching is unsupported, it returns an error ToolResult telling the agent to use another route. Otherwise it builds a FetchRequest with the URL, optional extraction prompt, length limit, and cache-bypass flag. It sends that request to the provider, records the fetched page if possible, then returns JSON containing the page URL, text, crawler provenance warning, and summary if one exists.

**Call relations**: This is the handler registered for the fetch_url tool. It calls _provider before doing anything else, delegates the actual page retrieval to the SearchProvider, optionally passes the page to record_fetched_page for observation logging, and wraps the final JSON in TextContent and ToolResult for the agent.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 213–222)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: Searches a specialized content category, such as images, people profiles, academic papers, videos, or shopping results. It gives the agent a clearer path when the desired result type matters more than a broad web search.

**Data flow**: It receives the tool context and validated vertical-search arguments. It gets the provider, builds a SearchQuery using the requested vertical and a default result count, sends it to the provider, records the returned hits if observation logging is available, and returns the hits plus any provider answer as JSON text inside a ToolResult.

**Call relations**: This is the handler registered for the search_vertical tool. It follows the same broad pattern as _search_web: get the provider, call the provider’s search method, record hits when possible, and use _results_json to shape the final answer.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


### `core/src/ufo/search.py`

`data_model` · `startup and request handling`

This file is a boundary, or “seam,” between the core system and any real web search backend. The core project does not include a built-in search engine and does not know API keys. Instead, an extension provides a SearchProvider, and core talks to it through the shapes defined here.

The data classes describe the messages passed across that boundary. A SearchQuery says what to search for, how many results are wanted, optional date limits, allowed domains, and a broad category such as academic or image search. SearchResults comes back with ranked SearchHit items, and possibly a direct answer if the backend can produce one. FetchRequest asks for the contents of one URL, possibly with a prompt asking the backend to extract something specific. FetchedPage is the returned page text and optional summary.

The SearchProvider protocol is the contract every backend must follow. A protocol is like saying, “Any object is acceptable if it has these methods and properties.” This keeps the research tools simple: they ask the selected provider to search or fetch, while each backend handles its own service details outside the sandbox. Without this file, the core system and search extensions would not have a clear, safe agreement about what information is exchanged.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells the rest of the system whether this search backend can also fetch and read the contents of a web page. It is used so tools can avoid asking a provider to do something it cannot do.

**Data flow**: The provider is already selected and available. The system reads this property and gets back a true-or-false answer. Nothing is changed; the answer simply decides whether page fetching is allowed.

**Call relations**: When a research tool wants to fetch a URL, it first checks this property on the selected SearchProvider. If it says fetching is supported, the tool can continue to call fetch; if not, the tool should stop or report that fetching is unavailable.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This method is the standard way to ask the selected backend to run a web search. It takes a SearchQuery and returns SearchResults in the shape core expects, no matter which outside service provided them.

**Data flow**: A SearchQuery goes in, containing the search sentence, result count, and optional filters such as dates or domains. The provider sends that request to its own backend service and translates the response into SearchResults. The result comes back as ranked hits and possibly a direct answer.

**Call relations**: Research tools call this method through the turn's tool context when they need web information. The concrete backend does the outside API work, then hands normalized results back to core so the rest of the system does not need backend-specific code.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This method is the standard way to ask a provider to retrieve and extract text from one web page. It is only meant to be used when supports_fetch says the provider can do this.

**Data flow**: A FetchRequest goes in with a URL and optional instructions such as an extraction prompt, a character limit, or a request to bypass cache. The provider fetches or recrawls the page through its backend and returns a FetchedPage with extracted text and possibly a summary. The method does not expose backend credentials to core or the sandbox.

**Call relations**: The research fetch_url tool checks supports_fetch before calling this method. Once allowed, the selected provider performs the actual page retrieval and returns the cleaned page content in the common FetchedPage format.


### Indexing and embeddings
The default index, Turbopuffer backend, shared indexing contract, and OpenAI embeddings turn text chunks into searchable lexical or vector context.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `indexing and search request handling`

This file is the project’s default memory search engine. Other parts of the system can give it small pieces of text, called chunks, and later ask, “Which chunks match these words?” or “Which chunks are closest to this embedding?” An embedding is a list of numbers that represents the meaning of text for similarity search.

The important point is that this backend works with two different databases. In PostgreSQL, it uses PostgreSQL’s native full-text search and pgvector support for vector distance. In SQLite, it uses SQLite FTS5 for word search and stores embeddings as raw bytes, then compares vectors in Python. This keeps the rest of the system database-neutral: callers work with plain Chunk, Hit, and IndexScope objects, while this file hides the database-specific details.

The DefaultIndex class is the main piece. Each method opens a workspace-scoped database transaction, checks which database dialect is being used, and runs the right SQL. It can insert or update chunks, delete all chunks for an owner, check whether an owner has indexed chunks, remove stale chunks after re-indexing, and run lexical or vector searches.

Without this file, a default deployment would have no built-in way to index text for later retrieval. It is like the card catalog for the system’s memory: it decides how text is filed, refreshed, removed, and found again.

#### Function details

##### `pgvector_literal`  (lines 31–32)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the text format expected by PostgreSQL’s pgvector extension. This is used when writing or searching vector embeddings in PostgreSQL.

**Data flow**: It receives a tuple of floating-point numbers. It converts each value to a plain float representation, joins them with commas, wraps them in square brackets, and returns that string for use in SQL parameters.

**Call relations**: DefaultIndex.upsert uses it before saving an embedding to PostgreSQL, and DefaultIndex.vector uses it before sending a search embedding to PostgreSQL. It is the small adapter between Python’s vector shape and PostgreSQL’s vector text shape.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 35–43)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how similar two vectors are using cosine similarity, where higher means more similar. This is used for SQLite vector search, because SQLite does not provide the same vector search machinery as PostgreSQL here.

**Data flow**: It receives two tuples of numbers. It calculates the length of each vector, returns 0.0 if either vector has no usable length, otherwise divides their dot product by the product of their lengths and returns the similarity score.

**Call relations**: DefaultIndex.vector calls this after reading stored embeddings from SQLite. In the SQLite path, the database supplies candidate rows, and this function supplies the actual similarity score used to sort them.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 46–47)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding from a Python tuple into bytes so SQLite can store it in a database column. SQLite does not know this project’s vector type, so the vector is packed into a compact binary form.

**Data flow**: It receives a tuple of floating-point numbers. It uses binary packing to encode each number as a 32-bit float and returns the resulting bytes.

**Call relations**: DefaultIndex.upsert calls this when saving chunks into SQLite. It is paired with unpack_embedding, which reverses the process during vector search.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 50–51)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts a stored SQLite embedding back from bytes into a Python tuple of numbers. This lets the code compare stored vectors with a query vector in Python.

**Data flow**: It receives a byte string from the database. It treats every four bytes as one floating-point number, unpacks the whole blob, and returns the resulting tuple.

**Call relations**: DefaultIndex.vector calls this for SQLite rows before passing the restored vector to cosine. It is the read-side partner to pack_embedding.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 54–63)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object, which is the standard search result returned by this index. It keeps result construction consistent for both word search and vector search.

**Data flow**: It receives a database row and a score. It copies the chunk identity, owner information, subject, position, text, and score into a Hit object, then returns that object.

**Call relations**: DefaultIndex.lexical and DefaultIndex.vector both call this after the database, or Python scoring code, has found matching rows. It turns raw database output into the neutral result type expected by the rest of the system.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 171–208)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing chunks with the same digest. This is used when text has been chunked or re-chunked and the searchable index needs to reflect the current version.

**Data flow**: It receives a tuple of Chunk objects. If the tuple is empty, it does nothing. Otherwise it opens a database transaction, checks whether the connection is PostgreSQL or SQLite, then writes each chunk with its text, owner details, subject, order, and optional embedding. PostgreSQL embeddings are converted with pgvector_literal; SQLite embeddings are packed with pack_embedding, and SQLite full-text search rows are refreshed alongside the main chunk row.

**Call relations**: This method is called by higher-level indexing code through the index backend interface. Inside this file it relies on pgvector_literal for PostgreSQL vectors and pack_embedding for SQLite storage, then leaves the database with searchable chunk records.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 210–217)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks that belong to one owner. This is used when an indexed object should no longer appear in search results.

**Data flow**: It receives an IndexScope, which identifies an owner kind and owner id. It opens a transaction, chooses PostgreSQL or SQLite SQL, and deletes matching chunk records. In SQLite it also deletes matching full-text-search entries so the side table does not point at removed chunks.

**Call relations**: DefaultIndex.prune calls this when the keep-set is empty, meaning no chunks for that owner should remain. Higher-level code can also use it directly through the backend interface to clear an owner’s indexed content.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 219–222)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a given owner already has any chunks in the index. This helps callers decide whether indexing work is needed or whether content is already present.

**Data flow**: It receives an IndexScope with owner information. It opens a transaction, asks the chunk table for one matching row, and returns true if a row exists or false if none is found.

**Call relations**: This method stands as a small query in the index backend interface. It does not call other local helpers; it simply asks the database for evidence that the scope has indexed content.


##### `DefaultIndex.prune`  (lines 224–238)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes stale chunks for an owner while keeping a known set of current chunk digests. This matters after re-chunking, when old chunks should stop appearing in search results.

**Data flow**: It receives an IndexScope and a frozen set of chunk digests to keep. If the keep-set is empty, it deletes the whole scope. Otherwise it opens a transaction and deletes only chunks for that owner whose digest is not in the keep-set. In SQLite it also removes matching full-text-search rows before deleting from the main chunk table.

**Call relations**: When there is nothing to keep, it hands off to DefaultIndex.delete. Otherwise it performs its own database-specific cleanup. This makes upsert safe to use during refreshes, because old chunks can be swept away afterward.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 240–276)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed chunks by words in the query text. It returns the best text matches within selected subjects and one owner kind.

**Data flow**: It receives a query string, a set of subjects, an owner kind, and a result limit. If there are no subjects, it returns no results. For PostgreSQL it also rejects blank queries, then uses PostgreSQL full-text search and ranking. For SQLite it turns the query into quoted search terms for FTS5, runs the full-text query, and ranks by SQLite’s BM25 score. In both cases it converts rows into Hit objects.

**Call relations**: Higher-level retrieval code calls this when it wants keyword-style search. After the database returns matching rows, this method calls _hit so callers receive normal Hit objects rather than database rows.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 278–311)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed chunks by vector similarity. This supports meaning-based retrieval, where chunks can match even if they do not share the exact same words.

**Data flow**: It receives a query embedding, subjects, an owner kind, and a result limit. If the embedding or subject set is empty, it returns no results. In PostgreSQL it converts the query vector with pgvector_literal and lets the database score by vector distance. In SQLite it reads candidate rows with stored embeddings, unpacks each embedding, scores it with cosine similarity in Python, sorts by score from best to worst, and returns the top hits.

**Call relations**: Higher-level retrieval code calls this for semantic search. It uses pgvector_literal on the PostgreSQL path, and on the SQLite path it combines unpack_embedding and cosine before calling _hit to produce standard search results.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 314–324)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system and registers the default index backend. This is how the system discovers that the backend named "default" can be created from this file.

**Data flow**: It creates a Manifest with this extension’s name and version. Inside that manifest it creates an IndexBackendSpec named "default" whose factory builds a DefaultIndex using the transaction opener supplied by the host context. It returns the completed manifest.

**Call relations**: The extension loading system calls this function when discovering available extensions. The returned manifest tells the core system how to construct DefaultIndex when the default index backend is selected or when no explicit index backend is configured.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `jobs/serve indexing and recall`

This extension is the bridge between UFO’s internal memory objects and Turbopuffer’s HTTP API. Without it, choosing `memory.index_backend = "turbopuffer"` would not work: chunks could not be written to Turbopuffer, old chunks could not be cleaned up, and recall searches would have no way to ask Turbopuffer for matching memories.

The file treats each stored text chunk like a document in a workspace-specific Turbopuffer namespace. A namespace is like a separate drawer in a filing cabinet, so one workspace’s records do not mix with another’s. Each chunk gets an ID, its vector embedding for meaning-based search, and plain attributes such as owner type, owner ID, subject, order, and text. Searches are filtered by owner kind and allowed subjects, so recall only looks in the right part of memory.

The `TurbopufferIndex` class provides the main index operations: add chunks, delete a whole scope, prune stale chunks after re-chunking, check whether a scope has anything stored, and run lexical or vector queries. It sends direct HTTP requests with a Bearer API key read from UFO’s credential store. Helper functions translate IDs, build request bodies, trim full-text queries to Turbopuffer’s limit, create filters, and turn Turbopuffer response rows back into UFO `Hit` objects.

#### Function details

##### `turbopuffer_id`  (lines 43–49)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: Turns UFO’s chunk digest into an ID suitable for Turbopuffer. Standard SHA-256 digests are shortened into base64url text so they stay well under Turbopuffer’s ID length limit; other IDs are left alone.

**Data flow**: It receives a chunk digest string. If the string looks like `sha256:` followed by 64 hex characters, it removes the prefix, converts the hex bytes into URL-safe base64, and drops the padding; otherwise it returns the original string unchanged.

**Call relations**: When chunks are written, `upsert_body` uses this to prepare document IDs. When chunks are removed, `TurbopufferIndex.delete` and `TurbopufferIndex.prune` use the same conversion so the delete request names the exact documents Turbopuffer stored.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 52–61)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: Converts a Turbopuffer document ID back into UFO’s normal chunk digest form when possible. This keeps search results and exported rows speaking UFO’s usual `sha256:...` language.

**Data flow**: It receives an ID string from Turbopuffer. If the ID has the expected shortened base64url length, it tries to decode it into bytes and returns `sha256:` plus the hex form; if it does not fit or cannot be decoded, it passes the ID through unchanged.

**Call relations**: `hit_from_row` uses this when turning query results into `Hit` objects. `_scope_chunks` also uses it while listing chunks for delete or prune, so later code compares and deletes using UFO’s familiar digest values.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 64–80)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body that tells Turbopuffer to insert or replace a batch of chunks. It arranges the data in the column format Turbopuffer expects and enables full-text search on the chunk text.

**Data flow**: It receives a tuple of `Chunk` objects. It extracts parallel lists of IDs, vectors, owner fields, subjects, ordinals, and text, converts chunk digests to Turbopuffer IDs, and returns a dictionary ready to send as JSON.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch. This helper is where UFO’s chunk-shaped data is translated into Turbopuffer’s document-shaped upload format.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 83–92)

```
def bm25_query(text: str) -> str
```

**Purpose**: Prepares a text query for Turbopuffer’s BM25 search, which is a word-based ranking method for finding text that shares important terms. It prevents too-long text from being rejected by Turbopuffer.

**Data flow**: It receives raw query text, trims surrounding whitespace, and encodes it as bytes. If it fits Turbopuffer’s 1024-byte full-text limit, it returns it as-is; otherwise it clips the bytes safely and tries to avoid ending in the middle of a word.

**Call relations**: `TurbopufferIndex.lexical` calls this before sending a word-based search. It acts as a safety gate between arbitrary user or model text and Turbopuffer’s stricter query size rule.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 95–99)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: Builds the filter used for normal recall searches. The filter says: search only chunks with this owner kind and only subjects from the allowed recall set.

**Data flow**: It receives an owner kind string and a frozen set of subjects. It returns a Turbopuffer filter expression that combines an exact owner-kind match with a subject-in-list match.

**Call relations**: `TurbopufferIndex._query` uses this for both lexical and vector searches. It is the shared guardrail that keeps recall from wandering into unrelated memory.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 102–109)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: Builds the filter used when working with one exact index scope, such as all chunks for one owner. It can also ask for only IDs after a certain point, which supports page-by-page listing.

**Data flow**: It receives an `IndexScope` and optionally an `after_id`. It creates a Turbopuffer filter for matching owner kind and owner ID, and adds an ID-greater-than condition if pagination needs to continue after a previous row.

**Call relations**: `TurbopufferIndex.has_chunks` uses this to check whether a scope contains at least one chunk. `_scope_chunks` uses it repeatedly while walking through all chunks in a scope for delete or prune.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 112–121)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: Turns one Turbopuffer result row into UFO’s standard `Hit` object. A `Hit` is the project’s common shape for “this chunk matched the search, with this score.”

**Data flow**: It receives a response row dictionary and a score. It converts the stored ID back to a chunk digest, reads the owner, subject, ordinal, and text fields from the row, and returns a `Hit` containing those values plus the score.

**Call relations**: Both `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` call this after `_query` returns raw rows. It is the final translation step from Turbopuffer’s response format back into UFO’s recall format.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 124–130)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: Computes a higher-is-better score for a vector search result. Turbopuffer may return cosine distance, where smaller means closer, so this function flips it into a similarity-style score.

**Data flow**: It receives one result row, the row’s position in the result list, and the total number of rows. If the row includes a numeric `$dist`, it returns `1 - distance`; otherwise it falls back to a descending rank score based on position.

**Call relations**: `TurbopufferIndex.vector` calls this before creating `Hit` objects. It makes vector results compatible with UFO’s later recall fusion logic, which expects larger scores to mean better matches.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 144–155)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Writes new or updated chunks to Turbopuffer. It skips chunks that do not have embeddings, because this backend stores chunks as searchable vector documents.

**Data flow**: It receives a tuple of `Chunk` objects. It filters out chunks without embeddings, gets an authorization header, splits the remaining chunks into batches, converts each batch with `upsert_body`, and posts each batch to the workspace namespace.

**Call relations**: This is called when UFO needs to add or refresh indexed memory. It relies on `_auth` for the API key, `_path` for the namespace URL, and `upsert_body` for the Turbopuffer JSON shape.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 157–165)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all chunks belonging to one index scope. This is used when a whole owner’s indexed memory should be removed.

**Data flow**: It receives an `IndexScope`. It gets authorization, lists all chunks currently in that scope with `_scope_chunks`, converts their digests to Turbopuffer IDs, and sends batched delete requests.

**Call relations**: Higher-level indexing code calls this when a scope should disappear. The method first asks `_scope_chunks` what exists, then uses `_path` and `turbopuffer_id` to send precise deletes to Turbopuffer.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 167–177)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes only stale chunks from one scope while keeping a supplied set of chunk digests. This matters after re-chunking, when some chunks are still valid but old leftovers should be removed.

**Data flow**: It receives an `IndexScope` and a frozen set of digests to keep. It fetches all chunks in the scope, filters out the ones whose digests are in the keep set, converts the rest to Turbopuffer IDs, and sends batched delete requests.

**Call relations**: This is the careful cleanup companion to `upsert`. It uses `_auth`, `_scope_chunks`, `turbopuffer_id`, and `_path` so a refreshed owner does not leave orphaned old chunks behind.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 179–185)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a scope currently has any chunks stored in Turbopuffer. It asks for just one row, making this a lightweight existence check.

**Data flow**: It receives an `IndexScope`. It builds a query for the first matching ID, sends it to the namespace with authorization, returns `False` if the namespace does not exist, otherwise returns whether the response contains any rows.

**Call relations**: Indexing or serving code can use this before deciding whether there is indexed material to work with. It uses `scope_filters` for the exact scope, plus `_auth` and `_path` for the HTTP request.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 187–196)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a word-based search over stored chunk text. It uses BM25 ranking, which favors chunks containing important words from the query.

**Data flow**: It receives query text, allowed subjects, owner kind, and a limit. It trims and bounds the query with `bm25_query`, returns no results if there is no usable text or no subjects, then asks `_query` for BM25-ranked rows and converts them into scored `Hit` objects.

**Call relations**: This is one of the public recall paths of the backend. It hands the actual HTTP query to `_query`, then uses `hit_from_row` so callers get UFO `Hit` values rather than raw Turbopuffer rows.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 198–207)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a meaning-based search using an embedding vector. This finds chunks whose stored embeddings are close to the query embedding, even if the wording differs.

**Data flow**: It receives an embedding, allowed subjects, owner kind, and a limit. If the embedding or subject set is empty, it returns no hits; otherwise it asks `_query` for approximate nearest-neighbor vector results, scores each row with `vector_score`, and turns positive-scoring rows into `Hit` objects.

**Call relations**: This is the other main recall path, alongside `lexical`. It delegates shared request work to `_query`, then uses `vector_score` and `hit_from_row` to make the results fit UFO’s recall scoring model.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 209–222)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: Sends the shared Turbopuffer query request used by both lexical and vector search. It centralizes filtering, included attributes, authentication, and error handling for recall queries.

**Data flow**: It receives a `rank_by` instruction, owner kind, subject set, and result limit. It builds a JSON body with ranking, limit, attributes to return, and filters; posts it to the namespace query endpoint; returns an empty list if the namespace is missing; otherwise returns the response rows.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` call this instead of each building their own HTTP request. It uses `query_filters`, `_auth`, and `_path` to make sure every search is scoped and authorized the same way.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 224–252)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: Lists all chunks in one exact scope, page by page. This is needed because delete and prune must know which document IDs currently exist before they can remove them.

**Data flow**: It receives an `IndexScope` and already-built authorization headers. It repeatedly queries Turbopuffer for up to one page of rows ordered by ID, converts each row into a lightweight `Chunk`, and continues after the last ID until there are no more pages.

**Call relations**: `TurbopufferIndex.delete` and `TurbopufferIndex.prune` call this before deciding what to delete. It uses `scope_filters` for each page, `_path` for the query URL, and `chunk_digest_from_id` to restore UFO digest values.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 254–256)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: Builds the HTTP authorization header for Turbopuffer requests. It reads the API key from UFO’s credential access object at the moment a request needs it.

**Data flow**: It reads the `turbopuffer_api_key` credential slot from the current credential provider. It returns a dictionary containing an `Authorization: Bearer ...` header.

**Call relations**: Most network-facing methods call this before talking to Turbopuffer: `upsert`, `delete`, `prune`, `has_chunks`, and `_query`. Keeping it in one place makes every request use the same credential source.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 258–259)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: Builds the Turbopuffer API path for this workspace’s namespace. The namespace name includes the workspace ID, keeping each workspace’s indexed chunks separate.

**Data flow**: It receives an optional suffix such as `/query`. It combines the fixed namespace prefix, the credential object’s workspace ID, and the suffix into a path like `/namespaces/ufo-<workspace>/query`.

**Call relations**: All methods that call Turbopuffer use this to aim at the right namespace: writing, deleting, pruning, checking for chunks, querying, and listing scope chunks. It is the routing helper for every HTTP request in the backend.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 262–282)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO so the system can discover and build the Turbopuffer index backend. It also declares the required API-key credential slot.

**Data flow**: It creates and returns a `Manifest` containing the extension name, version, credential requirement, and index backend specification. The backend factory receives a context, takes its credential access object, creates an HTTP client pointed at Turbopuffer’s base URL, and returns a `TurbopufferIndex`.

**Call relations**: Core extension loading calls this when registering available capabilities. Through the returned `IndexBackendSpec`, selecting the `turbopuffer` backend later creates the `TurbopufferIndex` used by indexing and recall operations.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/indexing.py`

`domain_logic` · `indexing and search preparation`

Search works best when long text is broken into steady, meaningful pieces. This file is the “cutting table” for that work. It takes a page or memory item, splits its body into chunks, asks an embedding service to turn each chunk into a list of numbers that represents its meaning, and sends those chunks to an index backend for storage and later search.

The file deliberately does not talk to a database itself. Instead, it defines small value objects like `Chunk`, `Hit`, and `IndexScope`, plus two interfaces: `IndexBackend` for search storage and `EmbedClient` for text embeddings. An interface is a promise: any extension can plug in as long as it provides the listed methods.

`TextChunker` is the main local worker. It tries to split text at natural breaks first, such as paragraphs, lines, sentences, and punctuation. If that is not enough, it falls back to whitespace or character-sized pieces. It also adds a little overlap from the previous chunk, like repeating the last few sentences when turning a book page, so later searches do not lose context at chunk boundaries. Each chunk gets a stable digest, a fingerprint made from its owner and text. After updating chunks, old fingerprints are pruned so edited text does not leave stale search results behind.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the storage-side promise for adding or replacing chunks in the search index. “Upsert” means insert if new, update if already present.

**Data flow**: It receives a group of `Chunk` objects, each containing text, ownership information, and usually an embedding. The backend implementation stores them in whatever search system it uses. Nothing is returned; the visible result is that those chunks become available for search.

**Call relations**: `chunk_embed_upsert` calls this after text has been split and embedded. This method is only a contract here; an actual extension supplies the database or search-engine behavior.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the storage-side promise for removing all indexed chunks that belong to one owner, such as one page or one memory item.

**Data flow**: It receives an `IndexScope`, which names the owner kind and owner id. The backend removes matching chunks from its index. It returns nothing; the change is in the stored search data.

**Call relations**: No function in this file calls it directly, but other indexing flows can use it when an owner is deleted or should no longer be searchable. The concrete backend decides how the removal happens.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the storage-side promise for cleaning up old chunks after an owner’s text has changed. It keeps only the chunk digests that are still current.

**Data flow**: It receives an `IndexScope` for the owner and a set of chunk digests to keep. The backend deletes any indexed chunks for that owner whose digest is not in the set. It returns nothing; the result is that stale chunks no longer appear in search.

**Call relations**: `chunk_embed_upsert` calls this every time it re-indexes a body. That makes updates safe: newly created chunks are saved, and chunks from older versions are removed.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the storage-side promise for checking whether an owner already has chunks in the index. It is useful for deciding whether indexing work is needed.

**Data flow**: It receives an `IndexScope` naming one owner. The backend looks in its index and returns `True` if chunks exist for that owner, otherwise `False`.

**Call relations**: This file defines the contract but does not call it. Other parts of the system can use it to avoid unnecessary re-indexing or to detect missing search data.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the storage-side promise for keyword-style search. It finds chunks whose text matches the query words.

**Data flow**: It receives a query string, a set of subjects to search within, an owner kind, and a result limit. The backend searches its text index and returns matching `Hit` objects with scores.

**Call relations**: This file only declares the method. Search features elsewhere can call it when they want traditional word matching rather than meaning-based vector search.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the storage-side promise for meaning-based search using an embedding. An embedding is a list of numbers that roughly captures what a piece of text is about.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a result limit. The backend compares that embedding against stored chunk embeddings and returns the closest `Hit` objects with scores.

**Call relations**: This file defines the shape of the call. A concrete backend supplies the actual nearest-neighbor search, while higher-level search code can use it without knowing the storage details.


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the promise an embedding provider must keep: turn text strings into numerical representations of meaning. Those numbers are later used for semantic search.

**Data flow**: It receives a tuple of text strings. The embedding implementation sends them through a model or service and returns one numeric vector for each input text, in the same order.

**Call relations**: `chunk_embed_upsert` calls this after the chunker has split a body into pieces. The returned vectors are copied into the chunks before they are sent to `IndexBackend.upsert`.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This function performs the full re-indexing step for one body of text. It splits the text, embeds the pieces, stores them, and removes old chunks that no longer match the current text.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner information, a subject, and the body text. First it turns the body into chunks. If there are chunks, it embeds their text and creates updated chunk objects that include those embeddings, then saves them through the backend. Finally it tells the backend to keep only the digests from the current chunk set, which also clears old chunks when the new body is empty.

**Call relations**: This is the shared workflow used by indexers for pages or memory items. It calls `TextChunker.chunk` indirectly through the provided chunker, then asks `EmbedClient.embed` for vectors, hands finished chunks to `IndexBackend.upsert`, and always finishes with `IndexBackend.prune` to prevent stale search results.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public method for turning one text body into `Chunk` records ready to be embedded. It attaches ownership details and a stable fingerprint to each piece.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks `_slices` to produce the actual text pieces, then numbers them in order and wraps each one in a `Chunk`. For every chunk it calls `_digest` to create a unique digest based on the owner, position, and text.

**Call relations**: `chunk_embed_upsert` uses this as the first step in indexing. Internally it relies on `_slices` for splitting and `_digest` for stable chunk identity.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This is the main splitting plan for raw text. It decides whether the text is small enough to keep whole or needs to be divided into searchable pieces.

**Data flow**: It receives one text string. Empty or whitespace-only text becomes an empty list. Short text is stripped and only capped by character length. Longer text is recursively split at natural boundaries, merged into reasonable sizes, given overlap for context, and finally capped by maximum character length. It returns plain text pieces.

**Call relations**: `TextChunker.chunk` calls this before wrapping pieces into `Chunk` objects. It coordinates `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars` as stages in the splitting pipeline.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates how large a text is for chunking purposes. It treats languages without spaces, such as Chinese, Japanese, and Korean, differently from space-separated writing.

**Data flow**: It receives text and first counts non-whitespace characters. If the text is empty after removing whitespace, it returns zero. If enough characters are CJK characters, it uses character count as the size estimate; otherwise it counts runs of non-space text as words.

**Call relations**: `_slices`, `_recursive_split`, and `_greedy_merge` all call this when deciding whether a piece is small enough. It keeps chunk sizing from depending only on English-style spaces.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This enforces a hard maximum character length for chunks. It is the safety net that prevents extremely long pieces from being sent to embedding or storage.

**Data flow**: It receives one text string. If it fits within `max_chars`, it returns it as a one-item list, unless it is empty. If it is too long, it cuts it into character windows with a small overlap between neighboring windows, trims whitespace, and returns the non-empty pieces.

**Call relations**: `_slices` calls this for short text and again after overlap is applied to long text. It is the final guardrail after more natural splitting has already been attempted.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks large text into smaller parts by trying natural separators from broad to narrow. It starts with paragraphs and works down toward punctuation and whitespace.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split on the current group of delimiters. If no useful split happens, it moves to the next level. If a piece is still too large, it splits that piece again at the next level. The result is a list of smaller text pieces.

**Call relations**: `_slices` calls this when the original text is too large. It uses `_split_at_delimiters` while natural delimiters remain, falls back to `_split_on_whitespace`, and checks sizes with `_count_words`.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This splits text at the earliest matching delimiter from a given set, while keeping the delimiter attached to the piece before it. That helps preserve punctuation and paragraph marks.

**Data flow**: It receives text and a tuple of delimiter strings. It repeatedly finds the earliest next delimiter, cuts through it, and continues with the remaining text. It drops pieces that are only whitespace and returns the useful pieces.

**Call relations**: `_recursive_split` calls this at each natural splitting level, such as paragraph breaks, line breaks, sentence endings, or commas. It supplies candidate pieces that `_recursive_split` may accept or split further.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural punctuation or line breaks are not enough. It divides text by word runs, or by raw characters if there are no usable word breaks.

**Data flow**: It receives text. If normal words are found, it groups them into batches of about `target_words`. If there are no words, or one huge run with no spaces, it cuts the raw text into fixed-size pieces. It returns only non-empty pieces.

**Call relations**: `_recursive_split` calls this at the final delimiter level. It is the last resort that ensures even awkward input can still be chunked.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This recombines pieces that became too small during splitting. It helps avoid creating many tiny chunks when neighboring pieces can safely fit together.

**Data flow**: It receives a list of text pieces. Starting with the first piece, it tries to append each next piece. If the combined text stays within a generous size limit, it keeps merging; otherwise it stores the current chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` to decide whether a combined piece is still a reasonable size.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds context from the end of one chunk to the start of the next chunk. The goal is to make search results less likely to miss meaning that crosses a boundary.

**Data flow**: It receives a list of chunk texts. If there is only one chunk or overlap is disabled, it returns the list unchanged. Otherwise, each chunk after the first is prefixed with trailing context taken from the previous chunk.

**Call relations**: `_slices` calls this after pieces have been merged into chunk-sized blocks. It asks `_trailing_context` for the prefix text and uses neighboring chunk pairs to build the overlapped result.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the overlap text to carry from one chunk into the next. It tries to avoid starting the overlap in the middle of an already-complete sentence when possible.

**Data flow**: It receives one chunk of text. If the chunk is too short to need overlap, it returns an empty string. Otherwise it takes the last `overlap_words` word runs, then looks for a sentence boundary early in that trailing text. If it finds one, it trims off the earlier sentence ending and returns the later context; otherwise it returns the full trailing text.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building the next chunk’s prefix. It is the small helper that makes overlap more sentence-aware rather than blindly copying words.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable fingerprint for a chunk. The fingerprint lets the index recognize whether a chunk is the same as before or has changed.

**Data flow**: It receives owner kind, owner id, subject, ordinal position, and chunk text. It joins those values with a separator and hashes the result with SHA-256, a standard one-way fingerprint algorithm. It returns the digest string prefixed with `sha256:`.

**Call relations**: `TextChunker.chunk` calls this for every produced piece. Later, `chunk_embed_upsert` uses these digests as the keep-set for pruning, so unchanged chunks can be kept and outdated chunks can be removed.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and background indexing`

This extension is the system's built-in bridge to OpenAI embeddings. An embedding is a list of numbers that represents the meaning of text, a bit like turning a paragraph into a map coordinate so similar ideas land near each other. The file registers this OpenAI-based backend under the name "default", so the rest of the system can use it when no other embedding backend is chosen.

The important safety work here is controlling what gets sent to OpenAI. The file clips very long text items and groups inputs into batches that stay under fixed size limits. This prevents one embedding request from becoming too large and failing unexpectedly.

The OpenAI API key is not read when the extension is first loaded. Instead, it is read from the environment each time embeddings are requested. That means a local development server can start without a key, but the first real embedding attempt will fail clearly if the key is missing. When embeddings are requested, the client calls OpenAI's async API, waits for each batch, sorts returned rows back into the original order, and returns the vectors as plain tuples of floats.

Without this file, deployments that rely on the default embedding setup would have no built-in way to create vectors for indexing and retrieval.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for embedding requests by cutting each item to a safe maximum length and grouping items into batches that are not too large. It exists to keep requests within provider and system limits before anything is sent over the network.

**Data flow**: It receives a tuple of text strings. For each string, it keeps only the allowed leading characters, then adds it to the current batch unless that batch would exceed the item count or total character limit. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send to OpenAI.

**Call relations**: OpenAIEmbedClient.embed calls this just before contacting OpenAI. In the larger flow, it acts like a packing step before shipping: it makes sure each box is small enough before the client sends it to the external embedding service.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This asynchronous method turns text strings into OpenAI embedding vectors. Someone uses it when the system needs numeric representations of text for indexing, search, or memory lookup.

**Data flow**: It receives a tuple of text strings. It reads OPENAI_API_KEY from the environment, fails clearly if the key is missing, builds an async OpenAI client, splits the input with plan_embed_batches, sends each batch to OpenAI, sorts the returned rows into input order, and converts each embedding into a tuple of floats. It returns one vector per input text, in the same order as the original texts.

**Call relations**: This is the main working method of the embed client built by build. When the indexing or serving side needs embeddings, it calls this method; the method first uses plan_embed_batches to shape the payload, then hands each batch to openai.AsyncOpenAI so the external provider can produce the vectors.

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client object that the core system will use for the default embedding backend. It deliberately does not require the OpenAI key at construction time, so startup can succeed even before any embedding request is made.

**Data flow**: It receives an ExtensionContext, which represents the workspace or extension environment, but it does not need to read anything from it here. It constructs and returns an OpenAIEmbedClient with the default model setting. No network call happens at this point.

**Call relations**: manifest points to this function as the factory for the default embedding backend. During extension setup, the core system uses build to obtain the client; later, actual embedding work happens through OpenAIEmbedClient.embed.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–90)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It says the extension's name and version, declares that OPENAI_API_KEY is a deployment key, and registers the default embedding backend.

**Data flow**: It takes no input. It creates an EmbedBackendSpec that names the backend and points to build as the factory, then wraps that information in a Manifest. The returned Manifest is what the host reads to discover and wire in this extension.

**Call relations**: The extension loader calls manifest when it wants to learn what this file provides. The manifest then hands the system enough information to call build later and create the OpenAIEmbedClient when the default embedding backend is needed.

*Call graph*: 2 external calls (__init__, __init__).


### Memory recall pipeline
The memory extension condenses sources, stores durable facts, exposes recall interfaces, and lets users browse or open memory objects.

### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `page-change processing and periodic background consolidation`

This file is like a librarian who first takes notes from new documents, then later combines similar notes into a tidy summary. The first part, `FactDeriver`, watches batches of changed source pages. For each page that still exists and has enough text, it asks the language model to extract durable standalone facts. It only writes facts that pass basic validation and are marked important enough. After the replacement facts are safely written, it marks older facts from that same page revision as superseded, meaning recall should stop treating them as current. This ordering matters: the code avoids deleting old facts unless a real replacement was created.

The second part, `MemoryConsolidator`, runs periodically rather than during page updates. It looks for older fact memories that are not already superseded, groups them by subject, embeds their text into numeric vectors, and clusters facts that are semantically close. An embedding is a list of numbers that lets the system compare meanings. For each close group, it asks the model for one concise summary, writes that as a `semantic` memory item, and marks the original facts as superseded by the summary. This keeps recall from being flooded with many small overlapping facts while preserving their combined meaning.

#### Function details

##### `FactDeriver.apply`  (lines 123–137)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: This is the entry point for turning changed source pages into memory facts. It decides which page changes need fact extraction, removes facts for pages that no longer exist, and only retires old page facts after new facts have actually landed.

**Data flow**: It receives a batch of page changes. It asks the memory store which pages are still live, immediately supersedes facts for pages that disappeared, filters out tombstones and very short pages, then splits the remaining pages into small groups. Each group is sent to `_derive`; for every page that successfully produced at least one committed fact, it tells the store to supersede older facts from that page revision.

**Call relations**: The page-change runner calls this when replaying source-page updates. It uses `itertools.batched` to keep model work bounded, then hands each group to `FactDeriver._derive`, which does the careful extraction and writing.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 139–177)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> tuple[PageChange, ...]
```

**Purpose**: This function performs one safe fact-derivation pass for a small group of pages. Its main job is to avoid writing facts for stale page versions and to return only the pages that truly received replacement facts.

**Data flow**: It takes a tuple of page changes. First it rereads the current page state and keeps only pages whose subject and revision still match the change it was given. It asks `_extract` for facts from those authorized pages. For each returned fact, it checks that the fact points to a known page, has high or medium notability, and that the page has not changed since extraction began. It then writes a `MemoryWrite` record to the store. The output is the set of page changes for which at least one fact was actually committed.

**Call relations**: It is called by `FactDeriver.apply` for each bounded group of eligible pages. It calls `FactDeriver._extract` to get model-produced facts, then creates `MemoryWrite` objects for the memory store. Its return value tells `apply` which old page facts may now be safely superseded.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 179–231)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: This function asks the language model to read a small group of pages and report the facts worth keeping. It uses a structured tool call so the model returns machine-checkable fact entries instead of loose prose.

**Data flow**: It receives page changes and builds a compact JSON payload containing each page ID and a capped amount of body text. It creates a model request with instructions, a tool schema, token limits, and forced use of the `record_facts` tool. After the model replies, it finds that tool call, reads its `facts` list, validates each fact entry, drops invalid entries, and returns the valid `ExtractedFact` objects. If the model did not make the required tool call or did not provide a facts list, it raises an error so the caller does not pretend the batch was settled.

**Call relations**: It is called only by `FactDeriver._derive`. It builds `Message`, `ModelRequest`, and `ToolSchema` objects and serializes the page payload with `json.dumps` before handing the request to the configured model.

*Call graph*: called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `MemoryConsolidator.run`  (lines 261–270)

```
async def run(self) -> None
```

**Purpose**: This is the periodic top-level routine that merges older related facts into summary memories. It does nothing if no model is configured, because summarizing requires a language model.

**Data flow**: It starts by checking whether model access exists. If not, it returns without changing anything. Otherwise it loads candidate aged facts, groups them by subject, skips groups that are too small, embeds each group, finds clusters of similar facts, and consolidates clusters large enough to be useful. The result is zero or more new semantic summaries in the memory table, with the original facts marked as superseded.

**Call relations**: A scheduler or background job calls this at intervals. It coordinates the full consolidation pipeline by calling `_aged_facts`, `_buckets`, `_embed`, `_clusters`, and `_consolidate` in order.

*Call graph*: calls 5 internal fn (_aged_facts, _buckets, _clusters, _consolidate, _embed).


##### `MemoryConsolidator._aged_facts`  (lines 272–302)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: This function finds older fact memories that are eligible to be merged. It deliberately ignores page-derived facts, already superseded facts, and very new facts.

**Data flow**: It computes a cutoff time, opens a database transaction, and selects a limited number of memory rows for the current workspace. The rows must be fact items, old enough, not created directly from a page, and not already superseded. It converts each database row into an `_AgedFact` value and returns them as a tuple.

**Call relations**: It is called by `MemoryConsolidator.run` at the start of a consolidation pass. It uses SQLAlchemy to build the database query and `_AgedFact` to carry only the fields needed by later steps.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 304–313)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: This function groups candidate facts by their subject so only facts about the same thing are compared and merged. It also caps each subject group so one busy subject cannot make the job too large.

**Data flow**: It receives aged facts, builds a dictionary from subject to facts, sorts each subject’s facts by recency, keeps only the newest allowed number for each subject, and returns a tuple of subject-and-facts pairs. It does not touch the database or call the model.

**Call relations**: It is called by `MemoryConsolidator.run` after eligible facts are loaded. The grouped output feeds the embedding and clustering steps, keeping consolidation focused within one subject at a time.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 315–319)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: This function turns fact text into embeddings, which are numeric fingerprints of meaning. Those fingerprints let the code compare facts by similarity rather than exact wording.

**Data flow**: It receives a tuple of aged facts, truncates each body to a safe length, and sends the texts to the embedding client. It then pairs each returned vector with the matching fact ID and returns a dictionary from fact ID to vector.

**Call relations**: It is called by `MemoryConsolidator.run` for each subject bucket large enough to consider. Its output is passed into `_clusters`, where similarity is measured.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 321–340)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: This function groups facts whose embeddings are close enough to mean roughly the same thing. It uses a simple newest-first approach, so newer facts become the heads of possible clusters.

**Data flow**: It receives facts and their embedding vectors. It sorts facts by recency, then for each fact compares its vector to the head of existing clusters using cosine similarity, a common way to measure whether two direction-like number lists point the same way. If the similarity is above the threshold, the fact joins that cluster; otherwise it starts a new cluster. It returns all clusters, including single-item ones that the caller may later skip.

**Call relations**: It is called by `MemoryConsolidator.run` after embeddings are created. It calls `_cosine` for each similarity check, and its clusters are handed back to `run`, which sends large enough clusters to `_consolidate`.

*Call graph*: calls 1 internal fn (_cosine); called by 1 (run).


##### `MemoryConsolidator._consolidate`  (lines 342–400)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: This function turns one cluster of related facts into a single semantic summary and safely marks the originals as replaced by that summary. It is careful not to overwrite data if the facts changed while the summary was being prepared.

**Data flow**: It receives a language model and a cluster of aged facts. First it calls `_summarize`; if no summary text comes back, it stops. It creates a new summary ID, opens a transaction, rereads the donor facts, and checks that the database still contains exactly the same bodies and confidence values it summarized. If everything still matches, it inserts a new semantic memory item and updates the original fact rows so their `superseded_by` field points to the summary. If the final update affects the wrong number of rows, it raises an error because the donors changed unexpectedly.

**Call relations**: It is called by `MemoryConsolidator.run` for each cluster large enough to merge. It calls `_summarize` before opening the write transaction, then uses SQLAlchemy `select`, `insert`, and `update` statements inside the transaction. It also creates the summary ID with `uuid4`.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 402–411)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: This function asks the language model to write one concise statement that combines several related facts. It is the model-facing part of consolidation.

**Data flow**: It receives a model and a fact cluster. It builds a JSON payload containing truncated fact bodies, creates a model request with the consolidation instructions and token limits, and asks the model for a plain text completion. It strips surrounding whitespace, caps the result to the maximum summary length, and returns that string.

**Call relations**: It is called by `MemoryConsolidator._consolidate` before any database write happens. It creates the `Message` and `ModelRequest`, serializes the payload with `json.dumps`, and hands the request to `ModelAccess.complete`.

*Call graph*: calls 1 internal fn (complete); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_recency`  (lines 414–415)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: This small helper provides a consistent sorting key for facts by time, with the fact ID as a tie-breaker. It helps the consolidator process newer facts first in a stable way.

**Data flow**: It receives one `_AgedFact` and returns a pair made from its creation time and ID. Callers can use that pair as a sorting key.

**Call relations**: It supports the consolidation flow wherever facts need to be ordered by recency, especially bucket trimming and clustering. It does not call other project functions and does not change any data.


##### `_cosine`  (lines 418–424)

```
def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This helper measures how similar two embedding vectors are. A higher score means the two fact texts are likely closer in meaning.

**Data flow**: It receives two equal-length tuples of numbers. It computes the size of each vector, returns 0 if either vector has no length, and otherwise returns the dot product divided by those sizes. The output is a floating-point similarity score.

**Call relations**: It is called by `MemoryConsolidator._clusters` when deciding whether a fact should join an existing cluster. It uses `math.sqrt` for the vector-length calculation.

*Call graph*: called by 1 (_clusters); 1 external calls (sqrt).


### `extensions/memory/ufo_ext_memory/store.py`

`domain_logic` · `request handling and background indexing`

This file gives the memory extension its durable “notebook” and its search pipeline. A memory is stored first as a database row, without doing expensive work immediately. Later, an indexer job breaks the text into chunks, asks an embedding service to turn the text into numbers that capture meaning, and writes those chunks into the search index. This keeps user-facing writes fast.

The file also controls who can read what. Memories can be written directly, or derived from synced source pages. If a memory came from a page, a reader must still have access to at least one source that produced it, and the page must still be at the same subject and revision. This prevents stale or unauthorized text from leaking through search results.

Recall combines several signals. It searches by plain words, by semantic similarity, and also scans a small “tail” of just-written memories that have not been indexed yet. It blends and ranks those matches, then applies recency decay for facts, so newer or higher-confidence facts can matter more. Source-page search uses similar search fusion, but returns matching page snippets. A separate page indexer mirrors current pages into the memory index and removes chunks when pages are deleted or changed.

#### Function details

##### `recall_subjects`  (lines 149–150)

```
def recall_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Turns an audience description into the exact subject strings that memory recall should search. A subject is the visibility label used to decide which memories are in scope.

**Data flow**: It receives an Audience object, passes it to the shared audience helper, and returns the resulting frozen set of subject names. Nothing is stored or changed.

**Call relations**: This is a small bridge from the memory extension to the shared audience code. Other memory flows can use it before calling recall so searches are limited to the right visibility subjects.

*Call graph*: 1 external calls (audience_subjects).


##### `_granted_link`  (lines 153–161)

```
def _granted_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]
```

**Purpose**: Builds a database permission check for page-derived memories. It says a memory is readable if any source linked to that memory is among the reader’s allowed sources.

**Data flow**: It receives a set of source IDs and produces a SQL condition. That condition can be added to a larger database query; it does not run the query itself.

**Call relations**: MemoryStore._untail_leg and MemoryStore._enrich add this condition when reading memories. In the larger flow, it is the fence that keeps search results from showing facts derived from sources the reader cannot access.

*Call graph*: called by 2 (_enrich, _untail_leg); 1 external calls (exists).


##### `inventory`  (lines 201–270)

```
async def inventory(transaction: Transaction, workspace_id: UUID) -> tuple[MemoryInventoryItem, ...]
```

**Purpose**: Returns a bounded, operator-friendly listing of stored memories in a workspace. It is for inspecting the memory store, not for answering a recall query.

**Data flow**: It opens a workspace transaction, reads the newest memory rows, reads their source links, then calculates age, half-life, and current decay weight using one shared current time. It returns MemoryInventoryItem objects and does not modify the database.

**Call relations**: It uses _aware, half_life_days, and decay_multiplier to show the same timing signals recall uses. Unlike MemoryStore.recall, it does not search the index or hide superseded rows; it is an explorer view of what is stored.

*Call graph*: calls 3 internal fn (_aware, decay_multiplier, half_life_days); 3 external calls (__init__, now, select).


##### `_aware`  (lines 273–274)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a datetime has a timezone. If a time has no timezone attached, it treats it as UTC, the shared clock used by this file.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it unchanged; otherwise it returns a copy marked as UTC.

**Call relations**: Inventory, recall enrichment, source search, and decay calculations use it before comparing times. It prevents mistakes caused by mixing timezone-aware and timezone-naive timestamps.

*Call graph*: called by 4 (_enrich, search_sources, decay_multiplier, inventory); 1 external calls (replace).


##### `MemoryWrite.page_origin_is_complete`  (lines 297–305)

```
def page_origin_is_complete(self) -> Self
```

**Purpose**: Checks that a memory derived from a page includes all required page-origin details. A page-derived memory needs the page ID, the page revision, and the source ID together.

**Data flow**: It reads the MemoryWrite object after validation. If some page-origin fields are present but not all, it raises a validation error; otherwise it returns the same object.

**Call relations**: This runs automatically when a MemoryWrite is created. MemoryStore.commit can then rely on page-derived writes being complete, which keeps database rows and source links consistent.


##### `_fuse`  (lines 333–356)

```
def _fuse(legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]) -> dict[str, tuple[float, float, str]]
```

**Purpose**: Combines search hits from multiple search methods into one best score per owning row. It is the shared scoring core for memory recall and source-page search.

**Data flow**: It receives several ranked hit lists, including the vector-similarity list. It compares each chunk’s rank across lists, keeps the strongest chunk for each owner, records the best semantic similarity score, and returns a map from owner ID to score details and matched text.

**Call relations**: fuse_hits and fuse_recall both call this helper. It sits between raw index results and the higher-level flows that read database rows back.

*Call graph*: called by 2 (fuse_hits, fuse_recall); 1 external calls (from_iterable).


##### `fuse_hits`  (lines 359–364)

```
def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks source-page search results by combining keyword and semantic index hits. It returns a simple ordered list of owners and snippets.

**Data flow**: It receives lexical hits, vector hits, and a limit. It asks _fuse to combine them, sorts by the fused rank score, trims to the limit, and returns Fused records.

**Call relations**: MemoryStore.search_sources calls this after querying the index. The resulting ordered page IDs are then checked against the page mirror and read permissions.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (search_sources); 1 external calls (__init__).


##### `fuse_recall`  (lines 367–384)

```
def fuse_recall(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]
```

**Purpose**: Ranks memory recall candidates by blending rank-based fusion with semantic closeness. It also includes a special tail list for newly written memories that are not indexed yet.

**Data flow**: It receives keyword hits, vector hits, tail hits, and a limit. It combines them with _fuse, normalizes the rank score, blends it with the best vector similarity score, sorts, trims, and returns Fused records.

**Call relations**: MemoryStore.recall calls this after collecting the three candidate lists. Its output is not final yet; recall still checks permissions, drops stale rows, applies recency decay, and enforces diversity.

*Call graph*: calls 1 internal fn (_fuse); called by 1 (recall); 1 external calls (__init__).


##### `half_life_days`  (lines 402–408)

```
def half_life_days(item_class: str, memory_kind: str) -> float | None
```

**Purpose**: Chooses how quickly a fact should lose ranking strength as it gets older. Non-fact memory classes do not decay here.

**Data flow**: It receives an item class and memory kind. If the item is a fact, it returns the configured half-life for that kind; otherwise it returns None.

**Call relations**: decay_multiplier uses this to compute recall ranking weight, and inventory uses it to display the same rule to operators.

*Call graph*: called by 2 (decay_multiplier, inventory).


##### `decay_multiplier`  (lines 411–423)

```
def decay_multiplier(item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime) -> float
```

**Purpose**: Calculates the recency-and-confidence weight applied to a memory’s relevance score. This is how an old low-confidence fact becomes less prominent over time.

**Data flow**: It receives the item class, kind, confidence, source time, and current time. For facts with a time, it computes a multiplier based on confidence and age; for other items or missing times, it returns 1.0.

**Call relations**: decay_factor uses it during recall, while inventory uses it for display. It calls half_life_days and _aware so ranking and operator reporting use the same math.

*Call graph*: calls 2 internal fn (_aware, half_life_days); called by 2 (decay_factor, inventory).


##### `decay_factor`  (lines 426–429)

```
def decay_factor(item: Recalled, now: datetime) -> float
```

**Purpose**: Applies the decay calculation to a recalled memory object. It is a convenience wrapper for recall ranking.

**Data flow**: It receives a Recalled item and the current time. It chooses the item’s as-of time, falling back to created-at time, then returns the decay multiplier.

**Call relations**: MemoryStore.recall calls it after database enrichment. It turns the raw search score into a time-adjusted score before final sorting.

*Call graph*: calls 1 internal fn (decay_multiplier); called by 1 (recall).


##### `enforce_type_diversity`  (lines 432–450)

```
def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]
```

**Purpose**: Prevents one memory class from filling the whole recall result list. This helps results include a mix instead of, for example, only facts or only episodic pointers.

**Data flow**: It receives already-ranked recalled rows and a limit. It keeps rows in order while capping each item class, then backfills from skipped rows if there is room, and returns the final tuple.

**Call relations**: MemoryStore.recall uses it after scoring and before converting episodic memories into topic pointers. It is a final shaping step for user-facing recall results.

*Call graph*: called by 1 (recall).


##### `as_topic_pointer`  (lines 453–463)

```
def as_topic_pointer(item: Recalled, index: int) -> Recalled
```

**Purpose**: Turns an episodic memory result into a short pointer rather than returning the full body. Episodic memory acts like a breadcrumb to browse, not text to inject directly.

**Data flow**: It receives a recalled item and its result index. If the item is episodic, it returns a copy with a short “Memory topic” body and recall_mode set to topic; otherwise it returns the item unchanged.

**Call relations**: MemoryStore.recall applies this to final diversified results. It uses dataclasses.replace so the original recalled item is not mutated.

*Call graph*: called by 1 (recall); 1 external calls (replace).


##### `MemoryStore.commit`  (lines 490–579)

```
async def commit(self, write: MemoryWrite) -> None
```

**Purpose**: Writes one memory into the database without indexing it immediately. This keeps commits fast and marks the row for a later indexing job.

**Data flow**: It receives a MemoryWrite, creates a stable content-based ID from workspace, subject, class, and body, then inserts or updates the memory row. If the memory came from a source page, it also inserts or updates the page-to-memory source link. Rebinding to a new page revision clears the embedding digest so the indexer will re-check it.

**Call relations**: This is the write side of MemoryStore. MemoryIndexer.run later notices rows with no embedding digest and publishes or withholds their chunks; supersede_page_facts may later remove source links created here.

*Call graph*: 3 external calls (case, or_, uuid5).


##### `MemoryStore.supersede_page_facts`  (lines 581–697)

```
async def supersede_page_facts(self, page_id: UUID, revision: int | None) -> None
```

**Purpose**: Retires the memory links produced by an older state of a page. It removes only the stale page’s contribution unless that was the last source for the memory.

**Data flow**: It receives a page ID and optional current revision. It finds stale memory_source links for that page, locks affected memory rows when supported, deletes stale links, repoints surviving memories to another link if needed, deletes memory rows with no links left, and removes deleted memories from the index.

**Call relations**: A fact-derivation flow calls this when a page’s facts have been replaced or a page is gone. It works with commit’s source links and hands deleted memory IDs to the index backend for cleanup.

*Call graph*: 4 external calls (__init__, delete, select, update).


##### `MemoryStore.recall`  (lines 699–739)

```
async def recall(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[Recalled, ...]
```

**Purpose**: Finds memories relevant to a query for a given set of subjects and a source reader. This is the main read path for memory recall.

**Data flow**: It gets the reader’s allowed source IDs, asks the index for keyword and semantic hits, scans the small unindexed tail, fuses candidates, reads matching memory rows back with permission and freshness checks, applies decay, sorts, enforces type diversity, and returns Recalled items.

**Call relations**: It orchestrates _source_ids, _legs, _untail_leg, fuse_recall, _enrich, decay_factor, enforce_type_diversity, and as_topic_pointer. It is where stored rows, search index results, permissions, and ranking rules meet.

*Call graph*: calls 8 internal fn (_enrich, _legs, _source_ids, _untail_leg, as_topic_pointer, decay_factor, enforce_type_diversity, fuse_recall); 2 external calls (replace, now).


##### `MemoryStore.search_sources`  (lines 741–807)

```
async def search_sources(self, query: str, subjects: frozenset[str], limit: int, start: datetime | None=None, end: datetime | None=None, *, source_reader: SourceReader) -> tuple[SourceMatch, ...]
```

**Purpose**: Searches synced source pages and returns matching snippets. It is like recall, but the owners are pages rather than memory items.

**Data flow**: It receives a query, subjects, limit, optional time window, and source reader. It collects keyword and vector hits, fuses them, reads page mirror rows from mem_page, asks for readable current page states, checks that each page is still current and allowed, and returns SourceMatch records.

**Call relations**: It calls _legs to reach the index, fuse_hits to rank candidates, and _readable_states to enforce source permissions. PageIndexer._apply keeps the page chunks and mem_page mirror that this function reads.

*Call graph*: calls 4 internal fn (_legs, _readable_states, _aware, fuse_hits); 3 external calls (__init__, select, UUID).


##### `MemoryStore._source_ids`  (lines 809–815)

```
async def _source_ids(self, source_reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Gets the set of source IDs the current reader is allowed to read. Source-derived memory recall cannot safely run without this authority.

**Data flow**: It receives a SourceReader. If the MemoryStore was not wired with a readable-source provider, it raises an error; otherwise it calls that provider and returns the allowed source IDs.

**Call relations**: MemoryStore.recall calls this before searching and enriching results. The returned IDs are later used by _untail_leg and _enrich to filter page-derived memories.

*Call graph*: called by 1 (recall).


##### `MemoryStore._legs`  (lines 817–829)

```
async def _legs(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]
```

**Purpose**: Runs the two main index searches for a query: keyword search and semantic vector search. The vector search is skipped if the query cannot be embedded.

**Data flow**: It receives the query, subject set, owner kind, and limit. It embeds the query, asks the index for lexical hits, optionally asks for vector hits using the embedding, and returns both hit lists.

**Call relations**: MemoryStore.recall uses it for memory-item candidates, and MemoryStore.search_sources uses it for page candidates. It delegates embedding failure handling to _embed_query.

*Call graph*: calls 1 internal fn (_embed_query); called by 2 (recall, search_sources).


##### `MemoryStore._untail_leg`  (lines 831–886)

```
async def _untail_leg(self, query: str, subjects: frozenset[str], limit: int, source_ids: frozenset[UUID]) -> tuple[Hit, ...]
```

**Purpose**: Finds newly committed memories that have not yet been indexed. This lets a fresh memory be recalled before the background indexer has run.

**Data flow**: It splits the query into terms, checks that there are terms and subjects, builds a permission condition using source grants, reads a bounded number of newest unindexed memory rows, counts term matches in each body, and returns sorted Hit objects for matching rows.

**Call relations**: MemoryStore.recall calls this as a third search leg alongside index results. It uses _granted_link so even the temporary unindexed search respects source permissions.

*Call graph*: calls 1 internal fn (_granted_link); called by 1 (recall); 4 external calls (__init__, split, or_, select).


##### `MemoryStore._embed_query`  (lines 888–896)

```
async def _embed_query(self, query: str) -> tuple[float, ...]
```

**Purpose**: Turns a search query into a vector, which is a list of numbers used for semantic similarity search. If embedding fails, recall can still continue with keyword search.

**Data flow**: It receives a query string. Blank queries return an empty vector; otherwise it asks the embed backend to embed the query, logs and returns empty on failure, and returns the first vector on success.

**Call relations**: MemoryStore._legs calls this before vector search. Its graceful failure path keeps recall and source search available even when the embedding service is temporarily unavailable.

*Call graph*: called by 1 (_legs).


##### `MemoryStore._enrich`  (lines 898–977)

```
async def _enrich(self, fused: tuple[Fused, ...], subjects: frozenset[str], source_ids: frozenset[UUID], start: datetime | None, end: datetime | None) -> tuple[Recalled, ...]
```

**Purpose**: Turns fused memory hit IDs into full recalled memory records, while applying database-side safety checks. This is where candidate hits become actual readable memories.

**Data flow**: It receives fused hits, subjects, allowed source IDs, and an optional time window. It reads matching non-superseded memory rows, filters by workspace, subject, time, and source grants, checks page-derived rows against current page state, and returns Recalled objects in the fused order.

**Call relations**: MemoryStore.recall calls it after fuse_recall. It uses _granted_link and _aware, and it calls page_states so stale page-derived memories do not escape just because their chunks still appeared in the index.

*Call graph*: calls 2 internal fn (_aware, _granted_link); called by 1 (recall); 4 external calls (__init__, or_, select, UUID).


##### `MemoryStore._readable_states`  (lines 979–986)

```
async def _readable_states(self, page_ids: tuple[UUID, ...], source_reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Fetches current page states only for pages the source reader is allowed to read. It is the page-level permission gate for source search.

**Data flow**: It receives page IDs and a SourceReader. Empty input returns an empty map; missing readable-page authority raises an error; otherwise it returns the provider’s page-state map.

**Call relations**: MemoryStore.search_sources calls this after it has candidate page IDs. The returned states are compared with mem_page rows before snippets are returned.

*Call graph*: called by 1 (search_sources).


##### `store_for`  (lines 989–1002)

```
def store_for(ext: ExtensionContext) -> MemoryStore
```

**Purpose**: Builds a MemoryStore from the extension context. It makes sure required search-index and embedding services are present.

**Data flow**: It receives an ExtensionContext. If index or embed backends are missing, it raises an error; otherwise it copies the needed transaction, workspace, page-state, and permission hooks into a new MemoryStore.

**Call relations**: Higher-level extension code can call this when it needs the memory workflow. It is the wiring point between the platform context and the store methods in this file.

*Call graph*: 1 external calls (__init__).


##### `MemoryIndexer.run`  (lines 1024–1026)

```
async def run(self) -> None
```

**Purpose**: Runs one pass of the background memory indexing job. It claims due memory rows and indexes them one by one.

**Data flow**: It asks _claim_due for a batch of rows whose embedding work is due. For each claimed MemoryItem, it calls _index_item. It returns nothing directly, but it may write chunks to the index and update database rows.

**Call relations**: This is the public driver for MemoryIndexer. The scheduler or background worker calls it; _claim_due and _index_item do the detailed work.

*Call graph*: calls 2 internal fn (_claim_due, _index_item).


##### `MemoryIndexer._claim_due`  (lines 1028–1063)

```
async def _claim_due(self) -> tuple[MemoryItem, ...]
```

**Purpose**: Atomically reserves a batch of memory rows that need indexing. The claim prevents overlapping workers from doing the same embedding work twice.

**Data flow**: It computes a lease cutoff time, selects rows whose embedding digest is missing and whose claim is absent or expired, locks or relies on the database writer rules, stamps embedding_claimed_at, and returns MemoryItem objects.

**Call relations**: MemoryIndexer.run calls it at the start of a pass. The rows it returns are later processed by _index_item, and _settle clears the claim when a row reaches a terminal decision.

*Call graph*: called by 1 (run); 5 external calls (now, timedelta, or_, select, update).


##### `MemoryIndexer._index_item`  (lines 1065–1102)

```
async def _index_item(self, item: MemoryItem) -> None
```

**Purpose**: Decides whether one claimed memory can be published to the index, then indexes it or removes its chunks. It keeps stale page-derived memories out of search candidates.

**Data flow**: It receives a claimed MemoryItem. It checks whether its page binding is publishable; if not, it deletes index chunks and settles the row. If publishable and chunks are missing, it chunks and embeds the body. It then re-checks the row’s current binding before settling, deleting chunks or leaving the row due if the binding changed.

**Call relations**: MemoryIndexer.run calls this for each claimed row. It relies on _publishable for page freshness, chunk_embed_upsert for chunking and embedding, and _settle to mark the row no longer due.

*Call graph*: calls 2 internal fn (_publishable, _settle); called by 1 (run); 3 external calls (__init__, select, chunk_embed_upsert).


##### `MemoryIndexer._publishable`  (lines 1104–1113)

```
async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool
```

**Purpose**: Checks whether a memory’s body is allowed to appear in the index. Directly written memories are publishable; page-derived memories are publishable only while their page is still current at the same subject and revision.

**Data flow**: It receives a subject, optional page ID, and optional revision. If there is no page ID, it returns true. Otherwise it reads current page state and returns true only if subject and revision still match.

**Call relations**: MemoryIndexer._index_item calls this before and after embedding work. This double-check keeps old page revisions from occupying search-index candidate slots.

*Call graph*: called by 1 (_index_item).


##### `MemoryIndexer._settle`  (lines 1115–1139)

```
async def _settle(self, item: MemoryItem) -> None
```

**Purpose**: Marks a claimed memory row as decided by the indexer. Settled means it no longer belongs in the due-for-indexing set.

**Data flow**: It receives the MemoryItem that was claimed. It writes a SHA-256 digest of the body, clears the claim time, and updates the timestamp, but only if the row still matches the same body and page binding and still has a claim.

**Call relations**: MemoryIndexer._index_item calls this after publishing or deliberately withholding a row. The guarded update prevents an old indexing decision from overwriting a newer page rebinding.

*Call graph*: called by 1 (_index_item); 2 external calls (sha256, update).


##### `PageIndexer.apply`  (lines 1162–1164)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Applies a delivered batch of source-page changes to the page index. It is the batch entry point for page-derived searchable source snippets.

**Data flow**: It receives a tuple of PageChange objects and passes each one to _apply in order. It returns nothing, but each change may update page chunks and the mem_page mirror.

**Call relations**: The core page-change runner owns the cursor and calls this with a batch. PageIndexer._apply performs the per-page safety checks and writes.

*Call graph*: calls 1 internal fn (_apply).


##### `PageIndexer._apply`  (lines 1166–1224)

```
async def _apply(self, change: PageChange) -> None
```

**Purpose**: Processes one page change by updating or deleting that page’s searchable chunks and mirror row. It refuses to publish chunks for a page that is no longer current.

**Data flow**: It reads the current page state, first marks facts left behind by the page as due again, then handles tombstones or stale changes by deleting page chunks and mem_page rows. For a current live change, it chunks and embeds the page body, re-checks that the page has not changed during embedding, and then upserts the mem_page mirror.

**Call relations**: PageIndexer.apply calls this for each change. It uses _unsettle_left_behind_facts to prompt the memory indexer to withdraw stale fact chunks, and chunk_embed_upsert to publish page chunks.

*Call graph*: calls 1 internal fn (_unsettle_left_behind_facts); called by 1 (apply); 5 external calls (__init__, delete, insert, update, chunk_embed_upsert).


##### `PageIndexer._unsettle_left_behind_facts`  (lines 1226–1254)

```
async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None
```

**Purpose**: Marks page-derived memory rows as needing re-indexing when their source page has moved on or disappeared. This lets the memory indexer remove chunks for facts tied to old page revisions.

**Data flow**: It receives a page ID and the current page state, if any. It builds a database condition for memory rows derived from that page that no longer match the live subject and revision, or all such rows if the page is gone, then clears their embedding digest and claim time.

**Call relations**: PageIndexer._apply calls this before handling every page change. It does not delete memory rows itself; it asks MemoryIndexer, through the due marker, to make the publish-or-withhold decision.

*Call graph*: called by 1 (_apply); 2 external calls (or_, update).


### `core/src/ufo/memory.py`

`data_model` · `cross-cutting memory lookup and browsing`

This file is a small contract between two sides of the system: code that wants to recall past information, and extensions that know how to search stored memory. Without this contract, every memory provider could return different fields or require different calls, and consumers would need custom code for each one.

The main result type is `MemoryMatch`, a plain data record for one memory hit. It carries the kind of memory, the text snippet to show, an optional durable object reference that can be opened later, an optional creation time for judging recency, and an optional subject. Think of it like a search result card: enough information to decide whether to open the full item.

`MemorySearchProvider` is a `Protocol`, meaning “anything with these methods counts.” It describes what a memory provider must offer: search by query, browse recent memory items for a readable subject set, and report which kinds of items can be listed.

`MemorySearch` is a thin wrapper around one chosen provider. It does not add new searching rules. It simply forwards calls to the provider, giving the rest of the codebase one stable object to ask for memory search and browsing.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Defines the search operation that every memory provider must support. A caller gives one or more query strings, plus a `SourceReader` that represents what sources the caller is allowed to read, and may narrow the search to a time range.

**Data flow**: The inputs are search text, a reader permission/source context, and optional start and end times. A concrete provider is expected to use those inputs to find matching memory items and return them as a tuple of `MemoryMatch` records. This protocol method itself does not perform the search; it states the required shape of the operation.

**Call relations**: This is the provider-side method that `MemorySearch.search` forwards to. In practice, other memory extensions supply the real implementation, and consumers call through `MemorySearch` so they do not depend on the provider’s concrete class.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Defines how a memory provider should return recent memory items that a set of subjects may read. This is for browsing recent memory, not for query-based searching.

**Data flow**: The inputs are a frozen set of readable subjects, a maximum number of items, an optional set of memory kinds to include, and an optional cursor that marks where the previous page ended. A concrete provider uses those to return a `ListingPage` of `MemoryMatch` items, usually in newest-first order, along with paging information. The cursor style avoids the common problem where newly added items make page numbers repeat or skip results.

**Call relations**: This is the provider-side method that `MemorySearch.list_recent` delegates to. Browsing code can call the wrapper, while each provider decides how to fetch the correct recent records from its own storage.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Defines how a provider tells callers which memory item kinds can be shown in recent-item browsing filters. This lets user-facing code offer valid filter choices instead of guessing.

**Data flow**: There are no inputs. A concrete provider returns a tuple of kind names, such as the categories of memory items it can list. The protocol method only describes that promise; the provider supplies the actual list.

**Call relations**: This is the provider-side method used by `MemorySearch.listable_kinds`. It supports callers that need to build filtering options before asking for recent memory items.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs a memory search through the selected provider. It gives callers one simple place to ask for recall results without caring which extension is doing the actual search.

**Data flow**: The caller passes a `SourceReader`, query strings, and optional start and end times. This method passes those values unchanged to the wrapped provider’s `search` method. The provider’s tuple of `MemoryMatch` results comes back out unchanged.

**Call relations**: This is the consumer-facing doorway for query search in this file. Its only handoff is to `MemorySearchProvider.search`, which may be implemented by any memory extension that follows the protocol.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Asks the selected provider for a page of recent memory items visible to a specific subject set. It is the wrapper’s browsing counterpart to query search.

**Data flow**: The caller supplies readable subjects, a result limit, optional kind filters, and an optional paging cursor. This method forwards those values directly to the provider’s `list_recent` method. It returns the resulting `ListingPage` of `MemoryMatch` entries exactly as the provider produced it.

**Call relations**: This method sits between browsing consumers and the concrete provider. It hands the request to `MemorySearchProvider.list_recent`, keeping the rest of the system insulated from provider-specific details.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the memory item kinds that the selected provider can list. Callers use this to know what recent-memory filters are available.

**Data flow**: There are no inputs beyond the wrapper’s stored provider. The method asks that provider for its listable kinds and returns the tuple it receives.

**Call relations**: This is the wrapper method for `MemorySearchProvider.listable_kinds`. It lets consumers ask the chosen memory provider about available categories through the same `MemorySearch` object they use for searching and browsing.


### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `cross-cutting`

This file does not contain executable code. Its job is mostly to label the folder as the home of the memory extension and to give a short summary of what that extension does.

In plain terms, the memory extension is meant to help the system remember useful facts over time. Those facts can then be recalled when a user submits a prompt, updated or derived when a page changes, and organized by a background memory-index job. You can think of it like a notebook attached to the system: one part writes down lasting facts, another part looks them up when they might help, and another part keeps the notebook organized so it stays useful.

Without this package marker, Python would not treat this directory as a normal importable package in the same way. Without the summary, newcomers would have less immediate context about why the surrounding files exist.


### `extensions/memory/ufo_ext_memory/events.py`

`config` · `cross-cutting`

This is a small constants file for the memory extension. Its main job is to prevent scattered code from inventing slightly different strings or limits for the same idea. The memory extension appears to emit a structured event before a response is produced, when it tries to recall relevant memories. The event name is stored here as MEMORY_RECALL_EVENT, so producers and listeners can use one agreed label instead of hard-coding text in many places. The file also sets two safety limits: how many recalled memory IDs should be included at most, and how long an error class name should be when reporting recall problems. These limits matter because event data is often logged, stored, or sent elsewhere; keeping it compact helps avoid noisy logs and oversized records. A useful analogy is a shared label maker: instead of every person writing their own version of a label, this file supplies the official label and a couple of rules about how much detail can fit on it.


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the doorway for reading saved memory items through the system’s object interface. A memory item is a stored note, fact, preference, decision, event, or task that the memory extension has recorded elsewhere. This file does not create or edit those memories. Instead, it answers questions like “show me the visible live memories” or “open this memory id and show me the full text.”

The important job here is safe visibility. Each memory has a subject, which is the audience allowed to read it. When a caller asks for memories, the code compares the caller’s readable subjects against the memory rows in the database. If a memory was distilled from a synced page, it also checks that the caller can still read that source page at the same revision. This prevents shared memory from leaking information from a page or room the reader should no longer see.

Listing returns only live memories: items that have not been superseded. Getting one item can still return a superseded memory, because an old search result or stale reference may point to it. In that case, the detail includes a `superseded_by` link, like a forwarding address to the newer memory. There is no delete or apply path here. Attempts to change memory through this object kind are refused, because writes must go through `memory_update`, and old memories are retired by superseding them rather than deleting them.

#### Function details

##### `_require_ext`  (lines 60–63)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This helper makes sure the memory object code was given its extension context, which is the object that knows how to reach the extension’s database and related services. Without it, memory reads cannot safely run.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it stops the operation by raising an error, because continuing would mean trying to read memory without the needed workspace and database information.

**Call relations**: The public read entry points call this first before doing real work. `MemoryObjects.list`, `MemoryObjects.get`, `MemoryObjects.member_page`, and `MemoryObjects.member_detail` use it as a guardrail before handing the context to the lower-level page or item readers.

*Call graph*: called by 4 (get, list, member_detail, member_page).


##### `_row`  (lines 66–71)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str) -> ObjectRow
```

**Purpose**: This helper turns a full memory item into a short row suitable for lists. It keeps the id, a short preview of the body, and the main filterable fields.

**Data flow**: It takes the memory id as text, the memory body, its subject, its item class, and its memory kind. It cuts the body down to a short summary and packages these values into an `ObjectRow`, which is the compact shape used in list results.

**Call relations**: The list-building code in `MemoryObjects._page` uses this for each visible memory. `MemoryObjects.member_detail` also uses it so the portal detail response can include the same kind of summary row alongside the full item detail.

*Call graph*: called by 2 (_page, member_detail); 1 external calls (__init__).


##### `_member_reader`  (lines 74–82)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: This helper builds the reading identity for a signed-in member who is viewing memory outside an active conversation turn. It answers the question: “which audiences should this member be allowed to read from?”

**Data flow**: It receives a member id. It looks up the current agent, builds the member’s conversation audience, converts that audience into readable subject strings, and returns a `SourceReader`, which is the bundle of identity and audience information used for visibility checks.

**Call relations**: The portal-facing methods `MemoryObjects.member_page` and `MemoryObjects.member_detail` call this before reading memory. They then pass the resulting reader into `_page` or `_item`, so the same visibility rules are used outside a live turn as inside one.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 91–92)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the normal object-list operation for memories during a tool or conversation request. It returns the visible, non-superseded memory rows for the caller.

**Data flow**: It receives a tool context and a list query. From the context it gets the extension context and the caller’s source reader, then passes both to `_page`. The result is an `ObjectPage`, which is a paged list of compact memory rows.

**Call relations**: This is a public entry point into the memory object store. It performs only setup and delegation: it checks the extension context with `_require_ext`, asks the tool context who is reading, and lets `_page` do the database lookup and visibility filtering.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 94–107)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the live memory list for a signed-in member viewing through the portal, outside a conversation turn. It deliberately uses the member’s own readable subjects, even if the caller is an admin.

**Data flow**: It receives the extension context, member id, admin flag, and list query. It builds a reader for that member with `_member_reader`, verifies the extension context with `_require_ext`, and asks `_page` to produce the visible memory list. The admin flag does not widen the memory audience here.

**Call relations**: This is the portal version of `MemoryObjects.list`. Instead of using `ToolContext.source_reader`, it creates the reader from the member id, then relies on the shared `_page` method so portal lists and in-turn lists follow the same filtering rules.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 109–127)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: This returns one memory item for a signed-in member viewing through the portal. It can return superseded items too, so an old reference can still explain what replaced it.

**Data flow**: It receives the extension context, memory name, member id, and admin flag. It builds the member’s reader, loads the item through `_item`, and returns `None` if the item is missing or not visible. If visible, it creates a compact row from the detail and wraps both row and detail into a `MemberObject` for portal display.

**Call relations**: This is the portal version of `MemoryObjects.get`. It calls `_member_reader` to define the member’s reading audience, `_item` to fetch and check the memory, and `_row` to create the summary that sits beside the full detail.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 1 external calls (__init__).


##### `MemoryObjects.get`  (lines 129–130)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory item by id during a tool or conversation request. It returns the full memory text, metadata, and provenance links if the caller is allowed to read it.

**Data flow**: It receives a tool context and a memory name. It gets the extension context and caller reader from the context, then passes them with the name to `_item`. The result is either an `ObjectDetail` for the memory or `None` if the name is invalid, missing, or not visible.

**Call relations**: This is the public detail entry point for memory objects. It does the same setup as `list`, but delegates to `_item` instead of `_page` because it needs one full memory rather than a page of summaries.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 132–184)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the core list reader. It finds live memory rows in the database and filters out anything the caller should not see.

**Data flow**: It receives the extension context, a source reader, and a list query. It reads the caller’s allowed subjects, queries the `memory_item` table for rows in the current workspace whose subject is allowed and that have not been superseded, and caps the raw database result. For memories derived from pages, it asks which source pages are still readable and keeps only rows whose source page, subject, and revision still match. It turns the remaining rows into compact objects and applies the requested page shape with `object_page`.

**Call relations**: `MemoryObjects.list` and `MemoryObjects.member_page` both hand work to this method. It uses the extension transaction to read the database, `readable_page_states` to confirm source-page access, `_row` to format each result, and `object_page` to produce the final paged response.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 186–252)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This is the core detail reader. It opens one memory by UUID, checks visibility, and builds the full object detail including links to its source page or replacement memory.

**Data flow**: It receives the extension context, a source reader, and the requested memory name. It first tries to parse the name as a UUID; if that fails, it returns `None`. It then queries the memory row in the current workspace and only among the reader’s allowed subjects. If the memory came from a page, it checks that the page is still readable at the recorded revision. Finally, it builds a `MemorySpec` with the memory body and metadata, adds `created_from` and `superseded_by` links when present, and returns an `ObjectDetail`.

**Call relations**: `MemoryObjects.get` and `MemoryObjects.member_detail` both rely on this method for the actual lookup. It uses the database transaction for the row read, `readable_page_states` for source-page safety, and object link/detail classes to shape the answer returned to callers.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 254–261)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no status for memory objects. In this object kind, memory is read-only and does not expose a separate generation or pending-change status.

**Data flow**: It receives the tool context, memory name, and optional expected generation. It ignores them and returns `None`, meaning there is no status payload to show or act on.

**Call relations**: This fills the object-store interface slot for status checks. Unlike `get` or `list`, it does not call into the database or any helper because this memory object kind has no status operation to perform.


##### `MemoryObjects.apply`  (lines 263–272)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or update a memory through the generic object apply operation. The system requires memory changes to go through `memory_update` instead.

**Data flow**: It receives the requested name, new memory spec, optional old spec, and optional expected generation. Rather than saving anything, it raises a `VerbNotSupported` error with a message explaining that memories are not applied through this route.

**Call relations**: This is called when the generic object system tries to apply a change to a memory object. It intentionally does not hand off to any writer; it stops the flow so all memory recording stays on the dedicated write path.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 274–281)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to delete a memory object. Memories are retired by being superseded during consolidation, not by direct deletion.

**Data flow**: It receives the tool context, memory name, and optional expected generation. It does not remove database rows or cleanup derived chunks. Instead, it raises a `VerbNotSupported` error explaining that memories cannot be deleted this way.

**Call relations**: This is called when the generic object system tries to delete a memory. It ends the operation immediately, matching the file’s rule that reads are allowed here but mutations are not.

*Call graph*: 1 external calls (__init__).


### Research and source records
Research package setup, saved source observations, and synced pages preserve the evidence and documents that later become recallable context.

### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `extensions/research/ufo_ext_research` using normal Python import paths.

Because the file is empty, it does not set up configuration, expose helper functions, or run any startup code. Its job is more like putting a label on a drawer: the drawer may contain useful tools elsewhere, but this label is what lets Python recognize the drawer as part of the organized code structure.

Without this file, depending on the Python version and how the project is loaded, imports from this research extension package could become less predictable or fail in environments that expect traditional package markers.


### `extensions/research/ufo_ext_research/observations.py`

`domain_logic` · `during research result recording and when the conversation sources slot is read`

When the research tool searches the web or fetches a page, it produces source material: URLs, titles, snippets, and sometimes publication dates. This file turns those temporary results into durable conversation observations. In plain terms, it is like keeping a small bibliography for each chat.

The file defines a database table for saved sources, keyed by workspace, conversation, and a digest of the URL. The digest is a fixed-length fingerprint made from the URL, so the same source can be recognized and updated without storing a huge URL as the key. When new sources arrive, the file trims overly long text, checks that each source fits the expected conversation-source shape, and writes it to the database. If the same URL was already recorded for that conversation, it updates the old row instead of making a duplicate.

It also keeps the list from growing forever. After each write, it keeps only the most recent and best-ranked sources, up to a fixed limit. Finally, it exposes a conversation slot provider called `SOURCES_SLOT`, which lets the rest of the app ask, “How many sources are available?” and “Please read the sources to display.”

#### Function details

##### `_bounded`  (lines 53–54)

```
def _bounded(value: str, limit: int) -> str
```

**Purpose**: This small helper cuts a string down to a maximum allowed length. It is used to keep saved source titles, snippets, and dates from becoming too large for display or storage expectations.

**Data flow**: It receives some text and a character limit. It returns the same text if it is already short enough, or only the first part of the text if it is too long. It does not change anything outside itself.

**Call relations**: `record_sources` calls this before validating and saving source information. It acts like a simple measuring gate before data goes into the conversation-source format.

*Call graph*: called by 1 (record_sources).


##### `record_sources`  (lines 57–130)

```
async def record_sources(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, sources: tuple[RetrievedSource, ...]) -> None
```

**Purpose**: This is the main saving routine for retrieved sources. It stores sources for a specific workspace conversation and turn, updates existing entries for the same URL, and trims the saved list so it stays small and useful.

**Data flow**: It receives the extension context, a conversation ID, a turn ID, and a tuple of retrieved sources. If there are no sources, it stops immediately. Otherwise it opens a database transaction, cleans and validates each source, computes a SHA-256 URL digest as a stable fingerprint, and inserts or updates the database row. After writing, it deletes older extra rows so only the configured maximum number of sources remains for that conversation.

**Call relations**: `record_search_hits` and `record_fetched_page` both hand normalized source records to this function. Inside, it uses the extension context to open a transaction, `_bounded` to shorten long fields, `ConversationSource` to validate what can be shown in the conversation UI, and SQLAlchemy database operations to insert, update, select, and delete rows.

*Call graph*: calls 2 internal fn (transaction, _bounded); called by 2 (record_fetched_page, record_search_hits); 5 external calls (__init__, now, sha256, delete, select).


##### `record_search_hits`  (lines 133–152)

```
async def record_search_hits(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, hits: tuple[SearchHit, ...]) -> None
```

**Purpose**: This adapts ordinary search results into the saved-source format. Someone uses it when the research extension has search hits and wants those hits to appear later as conversation sources.

**Data flow**: It receives the extension context, conversation ID, turn ID, and search-hit objects. For each hit, it copies the URL, title, result text, and published date into a `RetrievedSource`. It then passes the whole converted tuple to `record_sources`, which does the actual database work.

**Call relations**: This function sits between the search system and the source-saving routine. Search results come in as `SearchHit` objects, and this function translates them into `RetrievedSource` objects before handing them off to `record_sources`.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `record_fetched_page`  (lines 155–173)

```
async def record_fetched_page(ext: ExtensionContext, conversation_id: UUID, turn_id: UUID, page: FetchedPage) -> None
```

**Purpose**: This saves a single fetched web page as a conversation source. It is used when the research extension has opened or summarized a page directly, not just listed it as a search result.

**Data flow**: It receives the extension context, conversation ID, turn ID, and a fetched page. It builds one `RetrievedSource` using the page URL as both URL and title, and using the page summary if present, otherwise the page text. It then sends that one-item tuple to `record_sources` for validation and storage.

**Call relations**: This function is another adapter into the shared saving path. It converts a `FetchedPage` into the common `RetrievedSource` shape, then relies on `record_sources` to update the durable source list.

*Call graph*: calls 1 internal fn (record_sources); 1 external calls (__init__).


##### `_source_count`  (lines 176–186)

```
async def _source_count(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This counts how many saved sources a conversation has, up to the display limit. It is used to summarize the “Sources” slot without loading every source body.

**Data flow**: It receives a conversation slot context, which includes the extension context and conversation ID. It queries the database for the number of source rows in the current workspace and conversation. It returns `None` if there are no sources, otherwise it returns the count capped at the source limit.

**Call relations**: The `SOURCES_SLOT` provider uses this as its summarize function. When the UI or host system wants a lightweight summary of the sources slot, this function checks the database and reports whether there is anything worth showing.

*Call graph*: 1 external calls (select).


##### `_read_sources`  (lines 189–217)

```
async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload
```

**Purpose**: This reads the saved sources for a conversation and packages them for display. It returns the actual source list plus a flag saying whether there were more sources than the allowed display limit.

**Data flow**: It receives a conversation slot context. It queries the database for source rows in the current workspace and conversation, ordered by most recently updated and then by rank, and reads one more than the normal limit so it can detect overflow. It converts the rows into `ConversationSource` objects and returns a `SourcesSlotPayload` containing the visible sources and a `truncated` value that says whether some were left out.

**Call relations**: The `SOURCES_SLOT` provider uses this as its read function. After `record_sources` has saved source observations, this function is the path that brings them back out for the conversation sources panel or any other consumer of that slot.

*Call graph*: 3 external calls (__init__, __init__, select).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A synced page is like a photocopy placed in the workspace by an automatic courier. The original lives in an outside system, such as a source connector, and the sync driver brings a copy into UFO so people and tools can browse it. This file provides the object-facing view of those copies.

The main idea is safety and consistency. Pages are not authored here, so create and update are refused. Listing a page shows searchable metadata such as source, stream, title, and timestamps. Getting a page reads the body from blob storage, which is the system’s place for larger stored content, but only returns a bounded amount of text so one huge document cannot overwhelm an object read. Delete means “forget”: it tombstones the synced page so the existing page-change pipeline can clean up any derived index data.

The file also checks visibility. It asks the extension context for pages the current reader is allowed to see, and before returning a body it re-checks that the page has not changed underneath the read. If the page changed while being read, it returns nothing rather than mixing old body bytes with new metadata. Links can connect a page back to the source binding that synced it.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the current tool request has an extension context attached. The extension context is needed to read synced source pages and forget them.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it. If not, it stops the operation with a runtime error, because page objects cannot work without that workspace-specific access point.

**Call relations**: Page listing, page reading, and page deletion all call this before using extension services. It acts like checking that the key is on the keyring before trying to open the source-page cabinet.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This turns a page timestamp into a consistent UTC timestamp string. It accepts either a timestamp supplied by the outside provider or, if that is missing, the workspace row’s own timestamp.

**Data flow**: It takes an optional provider timestamp string and a stored row datetime. If the provider timestamp is absent, it uses the row datetime and adds UTC if needed. If the provider timestamp is present, it parses it and rejects it if it has no timezone. The result is always an ISO-formatted UTC time with microseconds.

**Call relations**: _Page.spec and _Page.fields use this whenever they expose created and updated times. That keeps list results and detailed reads speaking the same time language.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This gives the object name for a page. The name is simply the page row’s UUID, written as text.

**Data flow**: It reads the page’s internal UUID and converts it to a string. Nothing else changes.

**Call relations**: PageObjects.list uses this name when showing rows, and PageObjects._find compares it with the requested name when someone asks for a specific page.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This builds the optional link from a page back to the source binding that synced it. If the source binding name is unknown, it returns no links.

**Data flow**: It reads the page’s stored source name. If there is no source name, it returns an empty tuple. If there is one, it creates an object link whose relationship says the page was "synced_by" that source object.

**Call relations**: PageObjects.get includes these links in the detailed page response. This helps a reader jump from a synced document back to the source configuration that produced it.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This builds the full public description of a page, including its metadata and the body text that was just read from blob storage. It is the shape returned when someone gets a page.

**Data flow**: It receives the body text and a flag saying whether that body had to be cut off. It combines those with the page’s stored source, stream, title, subject, digest, blob reference, and normalized timestamps. The result is a PageSpec object ready to return to the caller.

**Call relations**: PageObjects.get calls this after it has safely read the body and confirmed the page is still current. It relies on _page_timestamp so provider timestamps are validated and normalized.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This creates a short human-readable summary for a page in list results. It gives enough context to recognize the page without reading the full body.

**Data flow**: It combines the title, source backend, stream, and visibility subject into one string, then trims it to the configured maximum length. It does not change the page.

**Call relations**: PageObjects.list uses this when building each visible row. It is the quick label people see before deciding whether to open the page.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This prepares the searchable and sortable metadata shown in page lists. These fields let callers filter or order pages without fetching every body.

**Data flow**: It reads the page’s source id, backend, stream, title, and timestamps. It normalizes the timestamps and returns a plain dictionary of JSON-friendly values.

**Call relations**: PageObjects.list puts these fields into each ObjectRow. Like _Page.spec, it uses _page_timestamp so list metadata and detailed metadata match.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the visible pages as a paged object list. It is used when a caller wants to browse synced pages by metadata rather than read one whole document.

**Data flow**: It receives the tool context and a list query. It asks _pages for all pages the current reader may see, turns each page into a row with name, summary, and fields, then passes those rows and the query to the object paging helper. The result is an ObjectPage containing the requested slice and ordering/filtering behavior.

**Call relations**: This is the object kind’s list operation. It depends on _pages to collect allowed page records, then hands the rows to object_page so the standard object-list machinery can shape the response.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This returns the detailed view of one synced page, including a bounded copy of its body text. It is careful not to return stale or inconsistent data if the page changes during the read.

**Data flow**: It receives a context and page name. It finds the visible page, streams the body bytes from blob storage up to one byte past the maximum, decodes valid UTF-8 text, and notes whether the body was truncated. Then it asks the extension context for the page’s current readable state and compares subject, revision, digest, and body reference with what it read. If everything still matches, it returns an ObjectDetail with the page spec, timestamps, and links; otherwise it returns None.

**Call relations**: This is the object kind’s read operation. It calls _find to locate the page, uses _require_ext to re-check the live readable page state, and finally uses _Page.spec and _Page.links indirectly to form the public response.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for pages. Pages are synced, read-only objects, so there is no apply operation whose progress needs to be tracked here.

**Data flow**: It receives the context, name, and optional expected generation, but does not inspect them. It always returns None and changes nothing.

**Call relations**: This fills the standard object-kind interface. Unlike object types that support long-running updates, page objects have no status story to hand off.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This rejects attempts to create or update a page. Pages must come from the content-sync driver, not from direct user edits.

**Data flow**: It receives the requested name, new spec, optional old spec, and optional generation check. Instead of writing anything, it raises VerbNotSupported with an explanation that pages are landed by syncing a source.

**Call relations**: This is called when the generic object system tries to apply a create or update to a page object. It stops that path immediately so the sync driver remains the only writer.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This forgets a synced page, but only for workspace admins. Forgetting tombstones the page so the existing cleanup pipeline can remove related indexed state.

**Data flow**: It receives the context, page name, and optional generation check. It first asks whether the speaker is an admin. If not, it raises AdminRequired. If the speaker is an admin, it finds the visible page by name; if none exists, it raises an error. Otherwise it asks the extension context to forget that page id.

**Call relations**: This is the object kind’s delete operation. It calls _find to resolve the object name and _require_ext to reach the extension service that actually tombstones the page.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This searches the current reader’s visible pages for one page with the requested object name. It is a shared helper for get and delete.

**Data flow**: It receives a context and a page name. It asks _pages for the allowed page set, compares each page’s name with the requested name, and returns the first match. If nothing matches, it returns None.

**Call relations**: PageObjects.get uses this before reading a body, and PageObjects.delete uses it before forgetting a page. It keeps name lookup behavior the same for both operations.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This gathers the synced pages the current reader is allowed to see and wraps them in the local _Page helper shape. It also adds source-provider information and, when possible, a linkable source binding name.

**Data flow**: It receives the tool context, gets the extension context, loads registered sources, and builds lookup tables from source id to backend and source binding name. Then it asks for source pages using the current reader’s source permissions. Each returned record is converted into a _Page with page metadata, digest, body reference, timestamps, and optional source name. The result is a tuple of _Page objects.

**Call relations**: PageObjects.list calls this to build the browse view, and PageObjects._find calls it when get or delete needs one named page. It is the main bridge between the extension’s stored source-page records and the object interface exposed by this file.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).

## 📊 State Registers Touched

- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-background-job-schedule` — The durable list of background jobs and dispatch rules used to retry and run work outside user requests.
- `reg-source-sync-state` — External source records, sync cursors, saved pages, deletion markers, and retry or backoff status.
- `reg-search-index` — The searchable text chunks, embeddings, and backend index state used to find relevant documents and pages.
- `reg-memory-store` — Saved facts, memory pages, confidence, provenance, and audience labels that let the assistant remember useful context.
- `reg-automation-objectives` — Durable objectives, steps, monitors, checks, blockers, and evidence for work that continues across turns.
- `reg-conversation-slots` — Side-panel data shown beside a conversation, such as tasks, sources, sites, automations, and workspace changes.
- `reg-research-observation-log` — Saved web research source observations and evidence records tied to conversations for later citation and display.
- `reg-provider-client-pools` — Per-process reusable transport/client state for external providers such as model APIs, search and embedding services, connector brokers, browser providers, billing services, and related retry or throttle windows.
- `reg-source-access-grants` — Durable permissions mapping agents to the synced sources they are allowed to read or search, separate from third-party account connection grants.
- `reg-sample-notes-store` — Durable sample-note extension records and note metadata used as optional workspace content across setup, tools, retrieval, and persistence paths.
