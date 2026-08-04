# Provider and Backend Extension Registration  `stage-3.1`

This stage is part of startup and shared behind-the-scenes setup. It is like filling a switchboard before the system begins work, so later code can ask for “a model,” “embeddings,” “search,” or “a hub” without knowing the exact outside service.

The Bedrock extension tells the system how to use Amazon Bedrock Mantle: which AI models are offered, what they cost, what login details they need, and how to build the correct client to call them. The OpenRouter extension adds another route to AI models, wrapping OpenRouter so many possible model companies look like one normal provider. The OpenAI embedding extension turns text into “embeddings,” which are lists of numbers that capture meaning, so the system can compare and search text later. The Exa extension connects web search and page fetching to Exa, while keeping the user’s API key out of the sandboxed tool area. The Redis hub manifest registers a Redis-based live hub, so configuration can choose Redis when it needs shared coordination.

## Files in this stage

### Model provider backends
Registers chat model providers that expose Bedrock-hosted and OpenRouter-routed models to the rest of the system.

### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `extension load and model client creation`

This file is like a catalog card plus a set of connection instructions for Amazon Bedrock Mantle. It lists the Bedrock models UFO can use, including Anthropic Claude models and OpenAI-compatible models, and records practical facts about each one: model ID, price, knowledge cutoff date, context window size, reasoning support, and which API style it uses.

The file does not translate requests itself. Instead, it points each model to an existing client class from the core UFO SDK. Anthropic model IDs use an Anthropic client connected to Bedrock Mantle’s Anthropic endpoint. OpenAI-compatible model IDs use an OpenAI client connected to Bedrock Mantle’s OpenAI-style endpoint. This keeps the extension small: it mostly says “for this model, use this endpoint and this credential.”

A key detail is region selection. Bedrock needs an AWS region, so the file reads either AWS_REGION or AWS_DEFAULT_REGION from the environment. If neither is set, it raises an error early rather than making a confusing failed network call later.

At the end, manifest() packages the provider name, version, required credential slot, and model list into a Manifest. That manifest is what the larger UFO system reads when loading this extension.

#### Function details

##### `bedrock_region`  (lines 42–48)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock requests should use. It checks the usual AWS environment variables and stops with a clear error if no region is configured.

**Data flow**: It reads AWS_REGION first, then AWS_DEFAULT_REGION if the first one is missing. If it finds a value, it returns that region string. If both are absent, it raises a RuntimeError explaining which environment variables must be set.

**Call relations**: The client-building functions call this before creating network clients. That means both Anthropic-style and OpenAI-style Bedrock connections get the same region check before they try to talk to AWS.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 51–63)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds the UFO client used for Anthropic models running through Bedrock Mantle. Someone uses this indirectly when a Bedrock Anthropic model is selected and the system needs an actual connection object to send requests.

**Data flow**: It receives a model description and an API key. It looks up the AWS region, creates an Anthropic Bedrock Mantle SDK client with that key, region, timeout, and no automatic retries, then wraps it in UFO’s AnthropicClient together with the model description. The result is a ready-to-use Anthropic-style model client.

**Call relations**: Model specs created by _anthropic point to this function as their client factory. When the wider UFO system later needs to run one of those models, this function is the bridge from the model catalog entry to the actual Anthropic Bedrock connection.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 66–73)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds the UFO client used for OpenAI-compatible models running through Bedrock Mantle. It chooses the correct Bedrock Mantle OpenAI-style endpoint based on the model’s API style.

**Data flow**: It receives a model description and an API key. It reads the AWS region, builds a base URL for either the Responses API or the Chat Completions API, creates an OpenAI SDK client pointed at that URL, and wraps it in UFO’s OpenAIClient with the model description. The result is a ready-to-use OpenAI-style Bedrock client.

**Call relations**: Model specs created by _openai point to this function as their client factory. Later, when UFO needs to call one of the OpenAI-compatible Bedrock models, this function supplies the correctly configured network client.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 76–94)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Creates a ModelSpec for one Anthropic model on Bedrock. A ModelSpec is the system’s model record: it says what the model is, what it costs, how much text it can accept, and how to create its client.

**Data flow**: It receives the model ID, price, knowledge cutoff, and optionally a context window size. It combines those with shared Bedrock settings such as provider name, credential slot, API key environment variable, reasoning support, chat API style, and the Anthropic client factory. It returns a complete ModelSpec.

**Call relations**: This helper is used while building the BEDROCK_MODEL_SPECS list. It keeps the repeated Anthropic model setup in one place so each listed Claude model only needs to provide the facts that differ.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 97–111)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates a ModelSpec for one OpenAI-compatible model on Bedrock. It records the model’s identity and limits, and tells UFO to use the OpenAI-style Bedrock client when calling it.

**Data flow**: It receives the model ID, price, knowledge cutoff, context window, and API surface. It combines those with shared Bedrock settings such as provider name, credential slot, API key environment variable, reasoning support, and the OpenAI client factory. It returns a complete ModelSpec.

**Call relations**: This helper is used while building the BEDROCK_MODEL_SPECS list. It keeps OpenAI-compatible model entries short while still giving each one the correct API style and client-building path.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 172–183)

```
def manifest() -> Manifest
```

**Purpose**: Returns the extension manifest that the UFO system reads to discover this provider. The manifest says the extension’s name and version, what credential it needs, and which models it offers.

**Data flow**: It creates a credential slot named for the Bedrock API key and describes what that key is for. It then packages that credential requirement together with the provider name, version, and full Bedrock model list into a Manifest object. The returned Manifest is the public summary of this extension.

**Call relations**: The larger extension-loading flow calls this to register Bedrock Mantle support. Once returned, the manifest gives the rest of UFO everything it needs to show available models, ask for the right credential, and later create clients for selected models.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `extension discovery and model request handling`

The core system already knows how to talk to OpenAI-style chat APIs. OpenRouter uses that same basic shape, but adds its own details: model names often need a provider prefix, requests can include a reasoning effort setting, and sometimes OpenRouter may route to an upstream provider that returns an empty answer. This file wraps those differences so the rest of the system does not need to care.

At startup, the file declares a small catalog of OpenRouter models, including their prices, context window size, knowledge cutoff, and API key location. That catalog becomes a manifest, which is how the larger system discovers this extension.

During a request, OpenRouterModelClient.complete turns the project’s internal ModelRequest into OpenAI-compatible chat parameters, opens a streaming response, and yields small events as they arrive: text pieces, tool-call starts, tool-call argument fragments, and finally token usage. If the service reports that the answer was cut off because the token budget was reached, it raises a clear truncation error. If OpenRouter temporarily fails before anything has been returned, it retries with backoff, meaning it waits longer after each failure. If a provider returns a normal but empty answer, it can retry while asking OpenRouter to avoid that provider, like asking a dispatcher not to send the next taxi from a company that just failed to show up.

#### Function details

##### `openrouter_slug`  (lines 53–63)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: This converts the model name used by the system into the provider/model name OpenRouter expects. It prevents callers from having to remember whether a model needs an OpenAI or Anthropic prefix.

**Data flow**: It receives a model id as text. If the id already contains a slash, it leaves it alone because it already looks like an OpenRouter slug. If it looks like an OpenAI model, it adds openai/; if it looks like an Anthropic Claude model, it adds anthropic/; otherwise it passes the name through unchanged.

**Call relations**: OpenRouterModelClient._create_kwargs calls this while building the outgoing request. The result becomes the model field sent to OpenRouter.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 66–71)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: This reads which upstream provider OpenRouter used for a streamed response chunk. That matters because an empty answer can be retried while excluding the provider that produced it.

**Data flow**: It receives one streamed chat chunk from the OpenAI-style SDK. It looks in the chunk’s extra OpenRouter metadata for a provider value, turns it into text if present, and returns it; if no provider is recorded, it returns nothing.

**Call relations**: OpenRouterModelClient.complete calls this for incoming chunks. The provider it returns may later be added to an ignore list if the completion ends without producing text or tool calls.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 74–83)

```
def _usage_of(usage: CompletionUsage) -> Usage
```

**Purpose**: This converts OpenAI-style token usage into the project’s own Usage object. It also separates cached input tokens from newly processed input tokens, so cost and accounting can be more accurate.

**Data flow**: It receives the SDK’s usage report. It reads prompt tokens, completion tokens, and any cached prompt-token detail. It checks that cached tokens are not greater than total prompt tokens, then returns Usage with input tokens, output tokens, and cache-read tokens filled in.

**Call relations**: OpenRouterModelClient.complete calls this when a stream chunk includes usage information. The Usage object it creates is yielded as the final event in a successful stream.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 100–168)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the main streaming request method for OpenRouter. It sends a model request, turns OpenRouter’s streamed response into the project’s standard model events, and deals with provider failures, empty completions, and truncated responses.

**Data flow**: It receives a ModelRequest containing the model, messages, tools, token limit, and reasoning preference. It builds OpenRouter request arguments, opens a streaming chat completion, and reads chunks one by one. Text chunks become TextDelta events, tool-call metadata becomes ToolCallStart and ToolCallDelta events, final token accounting becomes a Usage event, and certain failure cases become exceptions or retries.

**Call relations**: This method is called by the wider model-running flow when an OpenRouter-backed model is chosen. It relies on _create_kwargs to prepare the outgoing request, _chunk_provider to remember which upstream provider was used, and _usage_of to translate token accounting. It hands streamed events back to the caller as they arrive, so the rest of the system can react before the whole answer is complete.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 5 external calls (__init__, __init__, __init__, __init__, sleep).


##### `OpenRouterModelClient._create_kwargs`  (lines 170–199)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: This prepares the exact OpenAI-style chat request that will be sent to OpenRouter. It hides the translation from the project’s internal request shape to the network API shape.

**Data flow**: It receives a ModelRequest and a set of providers to ignore. It chooses the OpenRouter model slug, converts system and conversation messages into OpenAI-style messages, includes the token limit and streaming options, adds reasoning settings when appropriate, adds ignored providers when needed, and converts tool definitions into OpenAI function-tool format. It returns a dictionary of request arguments.

**Call relations**: OpenRouterModelClient.complete calls this right before creating the streaming completion. It calls openrouter_slug for the model name and openai_messages from the SDK helpers for message conversion, then gives the finished argument set back to complete for the network call.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 202–206)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: This creates an OpenRouterModelClient for a specific model spec and API key. It is the bridge between a catalog entry and a live client that can make requests.

**Data flow**: It receives a ModelSpec and an API key. It builds an asynchronous OpenAI-compatible SDK client pointed at OpenRouter’s base URL, then wraps that SDK client and the spec inside OpenRouterModelClient.

**Call relations**: _openrouter stores this function in each ModelSpec as the client factory. When the registry or runtime needs a usable client for one of those specs, this factory can produce the OpenRouterModelClient.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 209–226)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This builds one catalog entry for an OpenRouter model. The catalog entry tells the system what the model costs, how much context it supports, where to find its API key, and which client factory to use.

**Data flow**: It receives a model id, price information, a knowledge cutoff date, and optionally a context window size. It combines those with fixed OpenRouter settings such as provider name, API surface, reasoning support, and API key names, then returns a ModelSpec.

**Call relations**: The module uses this helper to create the OpenRouter model list. It calls ModelSpec to make each entry, and those entries are later included in the manifest returned by manifest.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 249–250)

```
def manifest() -> Manifest
```

**Purpose**: This exposes the extension’s name, version, and model catalog to the larger system. Without it, the system would not know which OpenRouter models this extension offers.

**Data flow**: It reads the module’s fixed extension name, version, and prepared OpenRouter model specs. It packages them into a Manifest object and returns it.

**Call relations**: The extension-loading code calls this kind of function to discover what an extension provides. It calls Manifest to create the object that the registry can consume.

*Call graph*: 1 external calls (__init__).


### Embedding and search backends
Adds external services for text embeddings, semantic lookup, web search, and page-content retrieval.

### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and background embedding/indexing`

This extension is the project’s built-in way to create embeddings, which are long lists of numbers that represent the meaning of text. Those numbers are what make semantic search possible: instead of matching exact words, the system can find text that is similar in meaning.

The file registers an embedding backend named "default", so the rest of the system can use it when no other embedding backend is chosen. It uses OpenAI’s `text-embedding-3-large` model and expects the deployment to provide an `OPENAI_API_KEY` environment variable. Importantly, it does not require that key at startup. The system can boot without it, which is useful for local development, but an actual embedding request will fail clearly if the key is missing.

Before sending text to OpenAI, the file carefully limits request size. It trims each text item to a maximum length, then groups items into batches that stay under both item-count and total-character limits. This is like packing boxes for shipping: each box can only hold so many objects and so much total weight. The embed client then sends each batch to OpenAI, keeps the returned vectors in the same order as the input texts, and returns them to the indexing system.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for safe embedding requests. It trims overly long text items and groups the remaining text into batches that stay within OpenAI request-size limits.

**Data flow**: It takes a tuple of text strings. For each string, it cuts it down to the maximum allowed item length, then adds it to the current batch unless that would exceed the maximum number of items or total characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send to the embedding provider.

**Call relations**: OpenAIEmbedClient.embed calls this before contacting OpenAI. That means the network call receives neatly packed, size-limited batches instead of one possibly huge request that could be rejected or fail unpredictably.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the main method that turns text into OpenAI embedding vectors. Someone uses it when the system needs numeric representations of text for search, indexing, or similarity matching.

**Data flow**: It receives a tuple of text strings. It reads `OPENAI_API_KEY` from the environment, fails immediately if the key is missing, creates an asynchronous OpenAI client, splits the input text into safe batches, sends each batch to OpenAI, sorts the returned rows back into input order, converts the embeddings to plain tuples of floats, and returns all vectors as a tuple.

**Call relations**: This method is the runtime worker for the backend created by build. During embedding work, it calls plan_embed_batches to shape the request safely, then hands each batch to openai.AsyncOpenAI so the external OpenAI service can produce the vectors.

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client that the rest of the system will use. It deliberately does not read the OpenAI key yet, so the application can start even when embedding is not configured.

**Data flow**: It receives an ExtensionContext, which represents the surrounding workspace or extension environment, but it does not need to read anything from it. It constructs and returns an OpenAIEmbedClient using the default model settings.

**Call relations**: The manifest points to this function as the factory for the default embedding backend. When the core system sets up embedding support, it calls build, and the returned OpenAIEmbedClient later performs real embedding work through its embed method.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–89)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what this extension provides. In this case, it announces an embedding backend named "default" and says that build should be used to create it.

**Data flow**: It takes no input. It creates an EmbedBackendSpec describing the backend name and factory function, wraps that in a Manifest with the extension name and version, and returns the Manifest to the extension loader.

**Call relations**: The extension system calls manifest to discover this file’s capabilities. The returned Manifest connects the backend name to build, so later startup code can construct the OpenAIEmbedClient when the default embedding backend is needed.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/exa/ufo_ext_exa.py`

`io_transport` · `request handling, when a research search or fetch uses the Exa provider`

This file is one concrete search backend. The rest of the system asks for searches through a common search interface, and this file teaches that interface how to talk to Exa. Without it, a deployment that chooses Exa as its search provider could not turn a user’s research request into real web search results or fetched page contents.

The main class, ExaSearchProvider, works like a translator at a service desk. On one side, the project speaks in its own simple objects, such as SearchQuery, SearchResults, FetchRequest, and FetchedPage. On the other side, Exa expects HTTP requests with Exa-specific fields and returns Exa-shaped JSON data. This file converts between those two languages.

For search, it builds a request body from the user’s query, including options like number of results, allowed domains, recency, or special search categories. It sends that body to Exa’s /search endpoint, checks that the response is valid, and turns each result into the project’s standard SearchHit format. For fetching, it asks Exa’s /contents endpoint for the text of one URL, optionally requesting a summary or forcing a fresh crawl.

A key safety detail is that the Exa API key is read on the host side through CredentialAccess. It is put only into the outgoing Exa request header, not passed into the sandbox. The manifest at the bottom declares the extension, its credential slot, and how the core system can build this provider.

#### Function details

##### `ExaSearchProvider.search`  (lines 54–56)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Exa and returns the results in the project’s normal search-result shape. Someone uses this when the research tools need a list of matching web pages rather than raw Exa data.

**Data flow**: It receives a SearchQuery with the search words and options. It turns that query into an Exa request body, sends it to Exa, pulls the results list out of the returned JSON, converts each Exa item into a SearchHit, and returns a SearchResults object containing those hits.

**Call relations**: This is the main search entry point for this provider. It relies on _search_body to prepare the Exa-specific request, _post to send the network request with credentials, _results to verify and extract the result list, and _hit to translate each result into the core format.

*Call graph*: calls 4 internal fn (_hit, _post, _search_body, _results); 1 external calls (__init__).


##### `ExaSearchProvider.fetch`  (lines 58–74)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches readable text for a single web page through Exa. It is used when the system already has a URL and wants page content, and possibly a summary, in a standard format.

**Data flow**: It receives a FetchRequest containing a URL, an optional maximum text length, an optional prompt for summarizing, and a flag for forcing a fresh crawl. It builds an Exa /contents request, caps the requested text length to a safe maximum, sends the request, takes the first returned item if one exists, and returns a FetchedPage with the URL, text, and optional summary.

**Call relations**: This is the provider’s fetch entry point. It hands the prepared request to _post, then uses _results to read Exa’s results safely and _opt_str to include a summary only when Exa actually returned a string.

*Call graph*: calls 3 internal fn (_post, _opt_str, _results); 1 external calls (__init__).


##### `ExaSearchProvider._search_body`  (lines 77–91)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the exact JSON body that Exa expects for a search request. It exists so the public search method can stay focused on the larger search flow while this helper handles Exa’s request details.

**Data flow**: It receives a SearchQuery. For a normal search, it asks Exa for text snippets and highlights, and it may add allowed domains or a start date based on recency. For a vertical search, such as academic or people search, it uses a shorter text limit and may map the project’s vertical name to Exa’s category name. It returns a dictionary ready to send as JSON.

**Call relations**: ExaSearchProvider.search calls this before contacting Exa. When recency is requested, it uses the current UTC time and subtracts the needed number of days to create Exa’s start date.

*Call graph*: called by 1 (search); 2 external calls (now, timedelta).


##### `ExaSearchProvider._hit`  (lines 94–104)

```
def _hit(item: dict[str, object]) -> SearchHit
```

**Purpose**: Turns one Exa search-result item into the project’s SearchHit object. This keeps the rest of the system from needing to know Exa’s field names or response quirks.

**Data flow**: It receives one result item as a dictionary from Exa. It reads fields such as URL, title, text, published date, and highlights, using safe fallbacks when fields are missing or not strings. It returns a SearchHit in the core format.

**Call relations**: ExaSearchProvider.search calls this once for each valid result returned by _results. It uses _opt_str for optional string fields and then hands the cleaned values into SearchHit.

*Call graph*: calls 1 internal fn (_opt_str); called by 1 (search); 1 external calls (__init__).


##### `ExaSearchProvider._post`  (lines 106–114)

```
async def _post(self, path: str, body: dict[str, Json]) -> object
```

**Purpose**: Sends one authenticated HTTP POST request to Exa and returns the decoded JSON response. It is the single place where this provider reads the Exa API key and talks to the network.

**Data flow**: It receives an Exa path, such as /search or /contents, and a JSON-ready request body. It reads the Exa API key from CredentialAccess, creates an async HTTP client pointed at api.exa.ai, sends the body with the key in the x-api-key header, and returns the parsed JSON response. If Exa returns an error status, it raises ExaError with the status and response text.

**Call relations**: Both search and fetch call this when they are ready to contact Exa. It uses httpx.AsyncClient for the actual HTTP request and raises ExaError so failures are visible to the caller instead of being mistaken for empty results.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_results`  (lines 117–121)

```
def _results(payload: object) -> list[dict[str, object]]
```

**Purpose**: Extracts the results list from an Exa response and checks that it really is a list. This prevents malformed or unexpected Exa responses from quietly looking like successful empty searches.

**Data flow**: It receives the decoded response payload from Exa. If the payload is a dictionary with a results field that is a list, it keeps only the items in that list that are dictionaries. If the results field is missing or not a list, it raises ExaError. It returns a clean list of result dictionaries.

**Call relations**: Both ExaSearchProvider.search and ExaSearchProvider.fetch call this after _post returns. It forms the safety gate between raw Exa JSON and the project’s typed result objects.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `_opt_str`  (lines 124–125)

```
def _opt_str(value: object) -> str | None
```

**Purpose**: Returns a value only if it is a string, otherwise returns nothing. It is a small safety helper for optional text fields that may be missing or may have the wrong type.

**Data flow**: It receives any value from an Exa response. If that value is a string, it passes it through unchanged; otherwise it returns None. This prevents non-text values from being placed into fields that expect optional text.

**Call relations**: ExaSearchProvider._hit uses it for published dates, and ExaSearchProvider.fetch uses it for summaries. In both places, it helps turn loose JSON data into stricter project objects.

*Call graph*: called by 2 (_hit, fetch).


##### `manifest`  (lines 128–141)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It tells the system the extension’s name and version, what credential it needs, and how to build the Exa search provider.

**Data flow**: It creates a Manifest containing one credential slot named exa_api_key and one search provider specification named exa. The provider specification includes a builder function that receives credentials and returns an ExaSearchProvider. The completed Manifest is returned to the extension loader.

**Call relations**: The host system calls this when loading the extension. The returned manifest is how the broader project discovers that Exa is available as a search backend and knows which credential to ask for before creating the provider.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Live hub backend
Advertises the Redis-backed hub implementation so configuration can select it at runtime.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup`

A hub is the part of the system that passes live frames or messages between running server instances. The core system has its own in-process hub, which works inside one server process. This extension adds a Redis Streams version, so multiple server instances can share the same flow of frames through Redis, an external message store. In everyday terms, it replaces a private office whiteboard with a shared noticeboard everyone can read.

This file is the extension’s manifest. A manifest is a small registration card: it names the extension, gives its version, and lists what new capability it offers. Here, the capability is a hub backend named "redis".

The important safety check is in `_build_hub`. If someone selects the Redis backend but forgets to provide `hub.url`, the code fails immediately with a clear error. That is better than starting successfully and then failing later when the first frame is published. If the URL is present, the builder creates a `RedisStreamHub`, which is the actual Redis-backed implementation.

Without this file, the extension might contain working Redis hub code, but the main system would not know how to find it or create it from configuration.

#### Function details

##### `_build_hub`  (lines 17–22)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed hub from a Redis URL. It also protects the system from a bad setup by refusing to continue if the URL is missing.

**Data flow**: It receives a URL value, which may be real text like `redis://host:6379/0` or may be missing. If the value is missing, it raises a clear startup error. If the value is present, it passes that URL into `RedisStreamHub` and returns the new hub object.

**Call relations**: This function is handed to the hub registration as the recipe for building the Redis backend. When the core system sees that the selected hub backend is `redis`, it can call this builder; the builder then hands off to `RedisStreamHub.__init__` to create the actual Redis stream hub.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 25–30)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension’s registration information. It tells the main system the extension name, version, and that it provides a hub backend called `redis`.

**Data flow**: It reads the constants in this file, packages `_build_hub` as the construction recipe for the Redis hub, and returns a `Manifest` object containing that information. It does not connect to Redis itself; it only describes how the system can do so later.

**Call relations**: This is the function the extension loader calls when discovering what this extension offers. It creates a `HubSpec` for the Redis backend, then places that spec inside a `Manifest` so the core system can later call `_build_hub` when the Redis hub is selected.

*Call graph*: 2 external calls (__init__, __init__).
