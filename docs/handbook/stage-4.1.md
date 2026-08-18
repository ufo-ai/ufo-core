# Model, embedding, search, and sandbox backend registration  `stage-4.1`

This stage is part of startup. It prepares the “phone book” the rest of the system uses to find outside services safely and consistently. The models package marker makes model code importable. The model spec defines the standard record for an AI model: provider, cost, key source, context size, and special behavior. The core catalog fills that record for built-in Anthropic and OpenAI models, while the Bedrock extension adds Amazon Bedrock models. The registry then combines these entries into one master lookup table.

The same pattern is used for search and memory. The search interface defines how UFO asks for web results or page text, and the Exa extension supplies one real search provider. The OpenAI embedding extension turns text into number lists, called embeddings, so similar meanings can be compared. Turbopuffer stores and searches those embeddings as a memory index.

For safe execution and live connections, the sandbox selector chooses a valid code-running backend, and the Redis manifest registers Redis-based shared communication transports.

## Files in this stage

### Model definitions and registry
Defines the model metadata shape, built-in and Bedrock-backed model offerings, and the registry used by the rest of the system.

### `core/src/ufo/models/__init__.py`

`data_model` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` is used to say, “this folder is a package you can import from.” Here, it sits inside `core/src/ufo/models`, which suggests this folder is meant to hold data model code: the project’s shared shapes for important information, such as classes or schemas used elsewhere.

Because the file is empty, it does not create classes, run setup code, or change any data. Its value is structural. It helps make imports predictable, such as allowing code to refer to the `ufo.models` package. Think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it tells the rest of the office that this drawer exists and has a name.

If this file were removed, the project might still work in newer Python versions because Python can sometimes treat folders without `__init__.py` as packages. However, keeping it avoids ambiguity and preserves compatibility with tools, packaging systems, or older expectations that rely on explicit package markers.


### `core/src/ufo/models/spec.py`

`data_model` · `model registry setup and per-request model calls`

This file is a central fact sheet for AI models. Without it, one part of the system might think a model supports tool use with reasoning, another part might bill it differently, and another might call it through the wrong API. That kind of scattered knowledge usually fails late and confusingly, such as during a user turn or after a provider rejects a request.

The main type is `ModelSpec`, a frozen data record, meaning once it is created its values cannot be changed by accident. Each model spec names the provider, how to build the model client, the model's price, its knowledge cutoff date, how much context it can accept, whether it supports extended reasoning, which API surface it uses, and where its API key should come from. The smaller `ReasoningSupport` record explains whether reasoning is available at all and whether it can be combined with tools.

The file also protects the system from bad model definitions. When a `ModelSpec` is created, it checks that the knowledge cutoff looks like `YYYY-MM` and that a model is not marked as supporting reasoning with tools if it does not support reasoning in the first place. It also translates provider authentication failures into a clear credential error, and decides whether a given request should actually send a reasoning setting.

#### Function details

##### `ModelSpec.__post_init__`  (lines 60–68)

```
def __post_init__(self) -> None
```

**Purpose**: This method checks that a newly created model record makes sense. It catches mistakes in the model registry early, before a bad model definition can cause confusing failures during a user request.

**Data flow**: A new `ModelSpec` has already been filled with values. This method reads its `knowledge_cutoff` and reasoning flags, checks that the date is in `YYYY-MM` form, and checks that tool-based reasoning is not enabled unless reasoning itself is enabled. If the data is valid, nothing changes; if it is invalid, it raises a clear `ValueError`.

**Call relations**: This runs automatically after a `ModelSpec` is constructed. It does not hand work to other project functions; its job is to stop invalid model facts at creation time so later registry lookups and model calls can trust the spec.


##### `ModelSpec.key_rejected`  (lines 70–80)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This turns a provider's rejected API key response into a clear, user-facing credential problem. Someone would use it when the provider says the key is not accepted, so the system can explain which key source may need replacing.

**Data flow**: It reads the model's id, provider name, environment-variable key name, and workspace BYOK slot name. It chooses the key label to mention, builds a message saying the provider rejected the key, and returns a `CredentialValueInvalid` error object with that message. It does not change the spec.

**Call relations**: When a model call gets an authentication rejection from the provider, this method packages that failure in the project's own credential-error type. It calls `CredentialValueInvalid.__init__` to create the typed error, so the rest of the system can treat the problem as a replaceable bad key rather than a vague network or streaming failure.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 82–93)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This decides whether a request should include a reasoning-effort setting when it is sent to the model provider. It prevents the system from asking for reasoning in cases where the model or its API does not support it.

**Data flow**: It receives the requested reasoning effort and the tools being sent with the request. It checks the model's reasoning capabilities: if the model does not support reasoning, it returns `None`; if tools are present but the model cannot combine tools with reasoning, it also returns `None`. Otherwise it returns the requested reasoning effort unchanged, including an explicit `off` setting.

**Call relations**: This is used at the point where a model request is being prepared for the provider. It acts like a gatekeeper: request-building code asks the spec what reasoning setting is safe to put on the wire, and this method either passes the requested value through or tells the caller to omit the setting entirely.


### `core/src/ufo/models/catalog.py`

`config` · `startup and model lookup`

This file is like a price sheet and address book for the project's built-in language models. Without it, the rest of the system would not know which model IDs are valid, which company provides each model, how much to charge for token use, how much conversation history each model can accept, or where to find the needed API key.

The file defines shared constants first: environment variable names for API keys, default context windows, and a common statement that these models support reasoning and tools together. A context window is the maximum amount of text the model can consider at once.

Small helper functions then build model clients and model descriptions. A model description, `ModelSpec`, is the project's standard record for one model. It says things such as: this is an Anthropic or OpenAI model, use this client builder, bill it at this rate, and call it through this API style.

The main function, `core_model_specs`, returns the full built-in list. It includes several Claude models and GPT models, with special notes baked into the data. For example, some GPT-5.6 models are marked to use OpenAI's Responses API instead of the chat API because that is the legal way to combine their features. At import time, the file also builds ready-to-use constants for the catalog, prices, and a pricing digest used elsewhere to recognize the exact price table.

#### Function details

##### `_anthropic_client`  (lines 24–25)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates the wrapper object the project uses to talk to an Anthropic model. Someone uses it when they already know the model's specification and have the API key needed to contact Anthropic.

**Data flow**: It receives a `ModelSpec`, which describes the model, and an API key string. It first uses the key to create the lower-level Anthropic SDK client, then wraps that SDK client together with the model specification in an `AnthropicClient`. The result is a ready-to-use project-level client for that specific model.

**Call relations**: This function is stored inside Anthropic `ModelSpec` records as the way to create a real client later. When another part of the system wants to call an Anthropic model, it can use the client builder from the spec, which in turn calls Anthropic's SDK client creator and then constructs the project's `AnthropicClient` wrapper.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 28–29)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates the wrapper object the project uses to talk to an OpenAI model. It connects a model description and an OpenAI API key to produce the client used for actual requests.

**Data flow**: It receives a `ModelSpec` and an API key string. It uses the key to create the lower-level OpenAI SDK client, then packages that SDK client with the model specification inside an `OpenAIClient`. The result is a ready-to-use project-level client for that OpenAI model.

**Call relations**: This function is saved inside OpenAI `ModelSpec` records as their client builder. Later, when the system needs to send a request to an OpenAI-backed model, the spec points back to this function, which creates the underlying OpenAI SDK client and wraps it in the project's `OpenAIClient`.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 32–51)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This helper builds one complete `ModelSpec` for an Anthropic model. It keeps the repeated Anthropic settings in one place so each catalog entry only needs to provide the parts that vary, such as model ID, price, and knowledge cutoff.

**Data flow**: It receives the model ID, pricing information, knowledge cutoff date, API-key environment variable name, and optionally a context window size. It combines those with Anthropic-specific defaults: provider name, client builder, key slot, reasoning support, chat API surface, and default context size. It returns a finished `ModelSpec` that the rest of the system can use to identify, bill, and call the model.

**Call relations**: `core_model_specs` calls this helper repeatedly while assembling the built-in Claude catalog. The helper hands back standardized Anthropic specs, so the main catalog function does not have to repeat the same provider and client details for every Claude model.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 54–68)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds one complete `ModelSpec` for an OpenAI model. It centralizes the OpenAI defaults while still allowing a model to choose a different API surface when needed.

**Data flow**: It receives the model ID, pricing information, knowledge cutoff date, API-key environment variable name, and optionally the API surface to use. It adds OpenAI-specific defaults such as provider name, client builder, context window, key slot, and reasoning support. It returns a finished `ModelSpec` that describes how to bill and call that OpenAI model.

**Call relations**: `core_model_specs` calls this helper for each built-in GPT model. For most models it uses the default chat API surface, but for GPT-5.6 entries the caller passes the Responses API surface so later requests are formed in the way OpenAI accepts.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 71–173)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds and returns the full tuple of built-in model specifications for core UFO. It is the single place where the project lists its directly supported Anthropic and OpenAI models and their pricing facts.

**Data flow**: It receives the names of the environment variables that should hold the Anthropic and OpenAI API keys. It creates `ModelPrice` records for each model, then passes those prices plus model IDs, knowledge cutoffs, key environment names, and any special context or API settings into `_anthropic` and `_openai`. It returns a fixed tuple of `ModelSpec` objects, one per built-in model.

**Call relations**: This is the catalog assembly step. It calls `_anthropic` for Claude models and `_openai` for GPT models, and those helpers turn each row of facts into a standard `ModelSpec`. At module load time, the file calls this function to create `CORE_MODEL_SPECS`; the later price maps and pricing digest are then derived from that returned catalog.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `startup / provider discovery`

This extension is like a catalog entry plus a set of connection instructions for Amazon Bedrock Mantle. Without it, the rest of the system would not know that these Bedrock models exist, what API shape they use, where to send requests, or which environment variables contain the needed AWS-style token and region.

The file defines shared provider details first: the provider name, credential slot, environment variable names, timeout, context window sizes, and reasoning support. A context window is the maximum amount of text a model can consider at once.

It then provides two small client builders. Anthropic model IDs are connected through Anthropic’s Bedrock Mantle client. OpenAI-compatible model IDs are connected through the system’s OpenAI client wrapper, with a Bedrock Mantle base URL chosen from the AWS region and the API style: either chat completions or responses.

The central list, BEDROCK_MODEL_SPECS, is the actual model catalog. Each ModelSpec records a model ID, price, knowledge cutoff, context size, API style, and credential source. Finally, manifest() packages all of this into a Manifest so the host application can discover the extension, ask the user for the right credential, and expose these models.

#### Function details

##### `bedrock_region`  (lines 43–49)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock Mantle requests should use. This matters because Bedrock endpoints are regional, so the client cannot build a correct URL without a region.

**Data flow**: It reads the process environment, first looking for AWS_REGION and then AWS_DEFAULT_REGION. If either value exists, it returns that region string. If neither is set, it stops with a clear error telling the user which environment variables to set.

**Call relations**: The Anthropic and OpenAI client builders both call this before creating their network clients. It acts as the shared checkpoint that ensures every Bedrock request is aimed at a real AWS region.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 52–64)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds the Anthropic-flavored client used for Bedrock Mantle models whose IDs are Anthropic models. Someone uses it indirectly through a ModelSpec when the system needs to talk to one of those models.

**Data flow**: It receives a model specification and an API key. It asks bedrock_region for the AWS region, creates an Anthropic Bedrock Mantle async client with the key, region, no automatic retries, and a 60-second timeout, then wraps that low-level client in the system’s AnthropicClient along with the model spec.

**Call relations**: Model specifications created by _anthropic store this function as their client factory. Later, when the system needs a usable client for an Anthropic Bedrock model, this builder supplies the correctly configured AnthropicClient.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 67–74)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds the OpenAI-compatible client used for Bedrock Mantle models that speak an OpenAI-style API. It hides the Bedrock-specific endpoint choice from the rest of the system.

**Data flow**: It receives a model specification and an API key. It reads the AWS region, chooses a base URL based on whether the model uses the responses API or the chat API, creates an OpenAI SDK client for that URL, and wraps it in the system’s OpenAIClient with the model spec.

**Call relations**: Model specifications created by _openai store this function as their client factory. When one of those OpenAI-compatible Bedrock models is selected, this builder turns the stored model details and key into a working OpenAIClient.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 77–95)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Creates a ModelSpec for an Anthropic model served through Bedrock Mantle. It keeps the repeated Anthropic model settings in one place so each catalog entry only needs to provide the model-specific facts.

**Data flow**: It takes a model ID, price, knowledge cutoff, and optionally a context window size. It combines those with shared Bedrock settings: provider name, Anthropic client factory, reasoning support, chat API style, credential slot, and environment variable. The result is a complete ModelSpec for one Anthropic model.

**Call relations**: The module calls this while building BEDROCK_MODEL_SPECS. Each returned ModelSpec later tells the host application how to price, display, authenticate, and connect to that Anthropic Bedrock model.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 98–113)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates a ModelSpec for an OpenAI-compatible model served through Bedrock Mantle. It centralizes the repeated OpenAI-style settings for this provider.

**Data flow**: It takes a model ID, price, knowledge cutoff, context window size, and API surface. It adds the shared provider name, OpenAI client factory, reasoning support, credential slot, environment variable, and a note that this provider does not support the system’s 'no retention' mode for these models. It returns a complete ModelSpec.

**Call relations**: The module calls this while building BEDROCK_MODEL_SPECS. The returned specs are later used by the host application to present and call OpenAI-compatible Bedrock models through the right API shape.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 181–192)

```
def manifest() -> Manifest
```

**Purpose**: Returns the extension’s public description to the UFO host application. This is how the host discovers the provider name, version, required credential, and available models.

**Data flow**: It creates a CredentialSlot describing the Bedrock API key the user must provide, then builds a Manifest containing the extension name, version, credential requirement, and the full Bedrock model list. The returned Manifest is the package of information the host consumes.

**Call relations**: The host application calls this during extension discovery or startup. It does not create model clients itself; instead, it hands the host the catalog and credential description needed to set up those clients later.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/models/registry.py`

`domain_logic` · `startup, then model-call and billing lookup`

This file solves a coordination problem: many parts of the system need to talk about models, but they must all agree on the same facts. Without this registry, one part might route a request to a model, another part might price it differently, and another might fail later because the model name was wrong.

The registry combines built-in model definitions with model definitions contributed by extension manifests. Each model has a unique id, like a label on a shelf. If two models try to use the same label, startup fails immediately instead of letting one silently replace the other. It also checks that the configured default models really exist, so typos are caught when the system boots, not during a user’s turn.

Once built, `ModelRegistry` is used as the common lookup table. It can turn the special `auto` model choice into the configured real model, return the model’s specification, create the right provider client with the correct key, identify the provider for metering, and expose pricing information. API keys are deliberately fetched at the moment a model client is created, so workspace-specific keys and rotated environment keys can take effect without restarting the server.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name `auto` into the real default model configured for this deployment. If the caller already named a specific model, it leaves that name unchanged.

**Data flow**: It receives a model id string. If that string is the shared `auto` placeholder, it returns the registry’s configured `auto_model`; otherwise it returns the original string. It does not change the registry.

**Call relations**: This is used by `ModelRegistry.model_key_env` before checking which environment variable is needed. That matters because onboarding should check the key for the model that will actually run, not for the placeholder name `auto`.

*Call graph*: called by 1 (model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This looks up the full registered facts for one model id. It gives callers one reliable place to fail clearly if a model name is unknown.

**Data flow**: It receives a model id, reads the registry’s `specs` dictionary, and returns the matching `ModelSpec`. If no entry exists, it raises a clear `ValueError` instead of returning a missing or partial result.

**Call relations**: `ModelRegistry.client_for`, `ModelRegistry.provider_for`, and `ModelRegistry.model_key_env` all go through this lookup before using model facts. This keeps routing, provider reporting, and key checks tied to the same source of truth.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 44–69)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This creates the actual client object used to call a chosen model provider. It also finds and validates the API key needed for that model.

**Data flow**: It receives a model id, looks up that model’s spec, and checks whether the model needs a key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the right credential, which may come from a workspace BYOK slot, meaning “bring your own key,” or from an environment variable. If the key is missing, it raises a clear runtime error. If the key contains characters the provider connection cannot carry, it raises `CredentialValueInvalid`. On success, it returns a newly built `ModelClient`.

**Call relations**: When some higher-level flow is ready to make a model call, it asks this function for the client. This function first uses `ModelRegistry.spec` to get the model’s instructions, then calls `ws_current` to reach the active workspace’s credentials, and finally hands the spec and key to the model-specific client factory.

*Call graph*: calls 1 internal fn (spec); 2 external calls (__init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 71–75)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This answers which company or backend provides a model, such as OpenAI or Anthropic. The system can use that provider name for metering and reporting.

**Data flow**: It receives a model id, looks up the model spec through `ModelRegistry.spec`, and returns the spec’s provider string. If the model id is unknown, the lookup raises the same clear error used elsewhere.

**Call relations**: This function depends on `ModelRegistry.spec` so provider reporting uses the same registered facts as client creation. It is used when code needs to label model activity by backend without constructing a client.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 77–84)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This tells callers which workspace BYOK slot would hold the key for a model. It is intentionally gentle: unknown or keyless models return `None` instead of failing.

**Data flow**: It receives a model id and directly checks the registry’s `specs` dictionary. If the model is not present, or if the spec has no BYOK key slot, it returns `None`. Otherwise it returns the key slot name.

**Call relations**: Unlike the stricter lookup functions, this is designed for situations such as billing export or historical records, where an old model id might no longer be registered. It lets those flows label a call as platform-served rather than stopping the whole export.


##### `ModelRegistry.model_key_env`  (lines 86–96)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding which environment variable must be set before a selected model can run. It only does this for built-in provider families whose key names are known to core code.

**Data flow**: It receives a model name and the system `Config`. First it resolves `auto` to the real configured model, then looks up that model’s spec. If the provider is Anthropic, it returns the configured Anthropic key environment variable name. If the provider is OpenAI, it returns the configured OpenAI key environment variable name. For contributed or other providers, it returns `None` because those models resolve their keys later.

**Call relations**: This function calls `ModelRegistry.resolve` so `auto` points to a concrete model before any key check. It then calls `ModelRegistry.spec` to read the provider. It is part of the early “can this model be used?” check, while extension models are allowed to defer key resolution until a real turn runs.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 99–132)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the active `ModelRegistry` from configuration, built-in model specs, and extension manifests. It catches duplicate or missing model ids during startup.

**Data flow**: It receives the loaded `Config` and a tuple of `Manifest` objects from extensions. It asks `core_model_specs` for the built-in models, then adds those and every manifest-provided model into one dictionary keyed by model id. If two specs claim the same id, it raises an error. It also checks that the configured automatic, ambient reply, and background job models all exist. Finally it builds a merged pricing table with `pricing_from` and returns a frozen `ModelRegistry` containing the specs, prices, and default auto model.

**Call relations**: This is the startup builder for the registry. It pulls in core model definitions through `core_model_specs`, folds in manifest-contributed models, creates pricing through `pricing_from`, and finally constructs `ModelRegistry`. Later model routing, key lookup, provider metering, and billing all depend on the table this function creates.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### Web search backend
Establishes the common search interface and registers Exa as a trusted-host web search and page-fetching provider.

### `core/src/ufo/search.py`

`data_model` · `cross-cutting`

This file is a boundary, or “seam,” between the core system and real-world search services. The core project does not contain a built-in search engine and does not keep search API keys itself. Instead, an outside extension provides a search provider, and this file says exactly what that provider must accept and return.

The file defines small data containers for the search workflow. A SearchQuery describes what to look for, including the question, result count, date limits, allowed websites, and optional category. SearchResults contains ranked SearchHit items, and may also include a direct answer from a search service that can summarize results itself. FetchRequest describes reading a single web page, possibly with a prompt asking the backend to extract or summarize something. FetchedPage is the returned page text and optional summary.

The central piece is SearchProvider, a protocol. A protocol is like a contract: any backend can be used if it has the required property and methods. This keeps the core independent from specific vendors. It also matters for safety: provider keys stay in the host process, while sandboxed tools only call through this agreed interface. Without this file, search tools and search backends would have to know too much about each other, making the system harder to extend and easier to wire unsafely.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells the rest of the system whether this search provider can fetch and extract text from a specific web page. It prevents tools from asking for page fetching when the chosen backend only supports search.

**Data flow**: The provider already knows its own abilities. When this property is read, it returns a simple yes-or-no value: true if page fetching is available, false if it is not. It does not change any data.

**Call relations**: This is part of the SearchProvider contract. The research fetch tool checks this before calling SearchProvider.fetch, so a request for a full page is only handed to providers that say they can do it.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This method asks the selected search backend to run one web search. Someone uses it when they have a SearchQuery and need ranked results, and possibly a direct answer, without caring which search service is behind it.

**Data flow**: A SearchQuery goes in, containing the natural-language query and limits such as result count, date range, allowed domains, or category. The provider sends that request to its own backend and turns the response into SearchResults. What comes out is a consistent result object the rest of the system can read.

**Call relations**: This is the main search action promised by the SearchProvider protocol. Research tools reach the selected provider through the turn’s ToolContext and call this method when a user task needs web search.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This method asks the selected provider to read one URL and return extracted page text. It can also request a focused extraction or summary when the backend supports that.

**Data flow**: A FetchRequest goes in, carrying the URL and optional instructions such as a prompt, a maximum text length, or whether to bypass cached content. The provider retrieves and extracts the page through its backend. A FetchedPage comes out, containing the URL, page text, and optionally a summary.

**Call relations**: This method is only meant to be called when SearchProvider.supports_fetch is true. The research fetch_url tool checks that gate first, then hands the FetchRequest to the provider so page-reading stays behind the same provider boundary as search.


### `extensions/exa/ufo_ext_exa.py`

`io_transport` · `extension load and search/fetch request handling`

This file is the bridge between the project’s general search interface and Exa, an outside search API. The rest of the system can ask for a search or for the contents of a page without knowing Exa’s exact request format. This file translates those plain project requests into Exa API calls, then translates Exa’s answers back into the project’s standard result objects.

A key part of the design is safety around credentials. The Exa API key is read through a controlled credential access object on the host machine. It is sent only as an HTTP header to Exa, not passed into the sandboxed tool environment. In everyday terms, this file is like a receptionist who is allowed to use the office keycard, but never hands the keycard to visitors.

The main class, ExaSearchProvider, can do two things: search for pages and fetch the text of a specific page. It builds the right JSON request body, sends it to api.exa.ai using async HTTP calls, checks for errors, and turns successful responses into SearchResults, SearchHit, or FetchedPage objects. The manifest function advertises this extension to the wider system, including the credential slot it needs and the search provider name that can be selected in configuration.

#### Function details

##### `ExaSearchProvider.search`  (lines 54–56)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Exa and returns the results in the project’s standard search format. A caller uses this when it has a SearchQuery and wants a list of matching pages without dealing with Exa’s API details.

**Data flow**: It receives a SearchQuery with the search text, result count, and optional filters. It turns that query into an Exa request body, sends it to Exa, checks that the response contains a results list, converts each result item into a SearchHit, and returns a SearchResults object containing those hits.

**Call relations**: This is called by the project’s search-provider seam when the configured backend is Exa. Inside its flow, it asks _search_body to shape the request, _post to send it, _results to validate and extract Exa’s returned list, and _hit to turn each raw Exa item into a standard project result.

*Call graph*: calls 4 internal fn (_hit, _post, _search_body, _results); 1 external calls (__init__).


##### `ExaSearchProvider.fetch`  (lines 58–74)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches readable text for one URL through Exa’s contents endpoint. A caller uses this when it already has a page URL and wants page text, and optionally a summary tailored to a prompt.

**Data flow**: It receives a FetchRequest containing a URL, optional maximum text length, optional prompt, and a force-refresh flag. It builds a request body for Exa, limiting text size to a safe maximum, adding a summary request if there is a prompt, and asking Exa to crawl live if forced. It sends the request, extracts the first returned result if present, and returns a FetchedPage with the URL, text, and optional summary.

**Call relations**: This is used by the broader tool flow when a page’s content is needed after or instead of search. It hands the HTTP work to _post, validates the returned shape through _results, and uses _opt_str so optional summary data is included only when Exa really returned a string.

*Call graph*: calls 3 internal fn (_post, _opt_str, _results); 1 external calls (__init__).


##### `ExaSearchProvider._search_body`  (lines 77–94)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON body that Exa expects for a search request. It keeps the Exa-specific knobs in one place so the public search method can stay focused on the larger search flow.

**Data flow**: It receives a SearchQuery and starts with the search text and requested number of results. For normal searches, it asks Exa for text snippets and highlights, and adds allowed-domain or publication-date filters when present. For vertical searches, such as academic or people search, it uses shorter text and maps the project’s vertical name to Exa’s category name when possible. It returns a dictionary ready to be sent as JSON.

**Call relations**: ExaSearchProvider.search calls this before making the HTTP request. It does not call out to the network itself; it simply prepares the package that _post will later send to Exa.

*Call graph*: called by 1 (search).


##### `ExaSearchProvider._hit`  (lines 97–107)

```
def _hit(item: dict[str, object]) -> SearchHit
```

**Purpose**: Converts one raw Exa search result into the project’s SearchHit format. This gives the rest of the system a predictable object even though Exa’s response fields may be missing or oddly typed.

**Data flow**: It receives one result dictionary from Exa. It reads the URL, title, text, publication date, and highlights, replacing missing basic fields with empty strings and keeping highlights only if they are strings. It returns a SearchHit object.

**Call relations**: ExaSearchProvider.search calls this once for each valid raw result returned by _results. It uses _opt_str for the publication date so that non-string dates are treated as absent rather than passed along in a surprising form.

*Call graph*: calls 1 internal fn (_opt_str); called by 1 (search); 1 external calls (__init__).


##### `ExaSearchProvider._post`  (lines 109–117)

```
async def _post(self, path: str, body: dict[str, Json]) -> object
```

**Purpose**: Sends one authenticated HTTP POST request to Exa and returns the decoded JSON response. This is the single doorway through which search and fetch requests leave the process for Exa.

**Data flow**: It receives an API path, such as /search or /contents, and a JSON-ready request body. It reads the Exa API key from the credential store, creates an async HTTP client pointed at api.exa.ai, sends the body with the key in the x-api-key header, and reads the response. If Exa reports an HTTP error, it raises ExaError with the status and response text; otherwise it returns the parsed JSON body.

**Call relations**: Both ExaSearchProvider.search and ExaSearchProvider.fetch rely on this for the actual network call. It is where the host-side credential is used, and where an injected httpx transport can be used in tests instead of real network traffic.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_results`  (lines 120–124)

```
def _results(payload: object) -> list[dict[str, object]]
```

**Purpose**: Checks that an Exa response contains a usable results list and extracts the result objects. This prevents a malformed response from being mistaken for an empty successful answer.

**Data flow**: It receives the decoded response payload from Exa. If the payload is a dictionary with a results field that is a list, it keeps only the list entries that are dictionaries and returns them. If there is no proper results list, it raises ExaError so the caller sees a clear failure.

**Call relations**: Both search and fetch call this after _post returns. It acts as a safety checkpoint before search turns items into SearchHit objects or fetch chooses the first page result.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `_opt_str`  (lines 127–128)

```
def _opt_str(value: object) -> str | None
```

**Purpose**: Returns a value only when it is actually a string. It is a small guard used for optional text fields that should not accept numbers, objects, or other unexpected data.

**Data flow**: It receives any value. If that value is a string, it returns the string unchanged; otherwise it returns None. It does not change anything outside itself.

**Call relations**: ExaSearchProvider._hit uses it for publication dates, and ExaSearchProvider.fetch uses it for summaries. In both places, it keeps optional fields clean and predictable.

*Call graph*: called by 2 (_hit, fetch).


##### `manifest`  (lines 131–144)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It tells the system the extension’s name and version, which credential it needs, and how to build the Exa search provider.

**Data flow**: It takes no input. It creates a Manifest containing one credential slot for the Exa API key and one search provider specification named exa. That provider specification includes a small builder that receives credential access and returns an ExaSearchProvider. The finished Manifest is returned to the extension loader.

**Call relations**: The host calls this when discovering or loading the extension. The manifest is what lets configuration select the Exa backend later, and it is what connects the project’s search-provider interface to ExaSearchProvider.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Embedding and memory index backends
Registers OpenAI embeddings and Turbopuffer-backed semantic, keyword, and hybrid memory indexing.

### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup and embedding request handling`

This extension is the project’s built-in way to ask OpenAI for embeddings. An embedding is a list of numbers that represents the meaning of text, a bit like giving each sentence a coordinate on a map so similar sentences land near each other. Without this file, a deployment that relies on the default embedding backend would have no way to create those coordinates.

The file registers itself as the default embedding backend through its manifest. At startup, the system can discover this extension and build an embed client. The client does not require the OpenAI API key during startup. Instead, it reads OPENAI_API_KEY from the environment only when an embedding request is actually made. That lets a local development server start without a key, while still failing clearly if someone tries to embed text without one.

Before sending text to OpenAI, the file carefully limits request size. Each text item is clipped to a maximum length, then the items are grouped into batches that stay under item-count and character-count limits. This is like packing boxes for shipping: each box can hold only so many objects and only so much total weight. The actual OpenAI call is made with the asynchronous OpenAI client, meaning it can wait for the network response without blocking other work.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for safe sending to the embedding provider. It trims overly long text entries and groups them into batches that stay within configured size limits.

**Data flow**: It receives a tuple of text strings. For each string, it cuts the text down if it is longer than the allowed per-item limit, then adds it to the current batch unless that batch would become too large by item count or total characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send.

**Call relations**: OpenAIEmbedClient.embed calls this before contacting OpenAI. It acts as the packing step before the network request, so the client sends requests in provider-friendly chunks instead of one oversized payload.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This method turns a group of text strings into embedding vectors by calling OpenAI. It is the main runtime path used when the system needs numeric representations of text.

**Data flow**: It receives a tuple of text strings. It reads OPENAI_API_KEY from the environment, fails with a clear error if the key is missing, creates an asynchronous OpenAI client, splits the text into safe batches, sends each batch to OpenAI, sorts returned rows back into their original order, and returns a tuple of numeric vectors. It does not permanently store the key or the OpenAI client.

**Call relations**: The embedding core uses this method after the extension has been built. Inside the method, plan_embed_batches prepares the input, and openai.AsyncOpenAI performs the outside network call to OpenAI’s embedding service.

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client that the host system will use. It deliberately does not check the OpenAI key at build time, so startup can succeed even before embedding is needed.

**Data flow**: It receives an ExtensionContext, which represents the workspace or extension scope, but this backend does not need to read anything from it. It returns a new OpenAIEmbedClient configured with the default model.

**Call relations**: The manifest points to this function as the factory for the default embedding backend. When the core system chooses this backend during startup, it calls build, which hands back an OpenAIEmbedClient for later embedding requests.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–90)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It tells the system the extension name, version, required deploy key, and which embedding backend it provides.

**Data flow**: It takes no input. It creates an EmbedBackendSpec that names this backend as the default and connects it to the build function, then wraps that in a Manifest object along with the extension metadata and the OPENAI_API_KEY deploy-key requirement.

**Call relations**: The extension loader calls this to discover what the file offers. The returned manifest is how the rest of the system learns that this extension can supply the default embedding backend and that build should be called to construct it.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup registration, then indexing and recall request handling`

This extension is the bridge between UFO's internal memory system and Turbopuffer's HTTP API. Without it, choosing `memory.index_backend = "turbopuffer"` would not work: UFO would have no way to write chunks into Turbopuffer, search them later, or clean up old chunks.

The file treats each saved memory chunk like a document in a Turbopuffer namespace. A namespace is like a separate labeled drawer, one per workspace. Each document stores an id, its vector embedding (numbers that represent the meaning of the text), and plain attributes such as owner kind, owner id, subject, order, and text.

It supports two kinds of recall. Vector search finds chunks with similar meaning using approximate nearest neighbor search, or ANN, which is a fast way to find nearby vectors. Lexical search uses BM25, a word-based ranking method, to find chunks that match the query text. Both searches are filtered so they only look at the requested owner kind and subject set.

The file also protects the outside service from bad requests. It shortens SHA-256 chunk ids to Turbopuffer-friendly ids, trims full-text queries to Turbopuffer's byte limit, batches writes and deletes, and treats a missing namespace as simply empty. Authentication is done by reading the Turbopuffer API key from UFO's credential store and sending it as a Bearer token on each request.

#### Function details

##### `turbopuffer_id`  (lines 43–49)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: Turns UFO's chunk digest into a document id that Turbopuffer will accept. Standard `sha256:` digests are shortened into URL-safe base64 text; anything already in another form is left alone.

**Data flow**: It receives a chunk digest string. If the string looks like a SHA-256 digest, it converts the hex bytes into a shorter base64url id without padding; otherwise it returns the original string unchanged.

**Call relations**: When chunks are written, `upsert_body` uses this to prepare document ids. When chunks are removed, `TurbopufferIndex.delete` and `TurbopufferIndex.prune` use the same conversion so the delete request names the exact documents that were stored.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 52–61)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: Converts Turbopuffer's shortened document id back into UFO's normal chunk digest format. This keeps search results speaking UFO's language instead of Turbopuffer's internal id shape.

**Data flow**: It receives a document id from Turbopuffer. If it has the expected shortened length and can be decoded, it returns a `sha256:` digest; if not, it returns the id as it was.

**Call relations**: `hit_from_row` uses this when turning search rows into `Hit` objects. `_scope_chunks` also uses it while listing stored chunks before deleting or pruning them.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 64–80)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: Builds the JSON body sent to Turbopuffer when UFO wants to add or replace chunks. It arranges chunk data in the column-style format Turbopuffer expects.

**Data flow**: It receives a tuple of `Chunk` objects. It extracts ids, vectors, owner fields, subjects, order numbers, and text into parallel lists, declares cosine distance for vector search, and marks the text field as searchable for full-text search. It returns a dictionary ready to send as JSON.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch. Inside, it calls `turbopuffer_id` so UFO chunk digests become valid Turbopuffer document ids before the HTTP request is made.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 83–92)

```
def bm25_query(text: str) -> str
```

**Purpose**: Prepares a text query for Turbopuffer's BM25 word search. It trims very long input so Turbopuffer does not reject the request.

**Data flow**: It receives the user's query text, strips surrounding whitespace, and checks its byte length. If it is within Turbopuffer's limit, it returns it unchanged; if it is too long, it clips it and tries not to leave a half word at the end.

**Call relations**: `TurbopufferIndex.lexical` calls this before making a word-based search. It acts like cutting a note to fit on a form without ending in the middle of a word.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 95–99)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: Creates the filter used for normal search requests. It makes sure recall only searches chunks of the requested owner kind and allowed subjects.

**Data flow**: It receives an owner kind and a set of subjects. It returns a Turbopuffer filter expression saying: owner kind must match, and subject must be one of the sorted subject values.

**Call relations**: `TurbopufferIndex._query` uses this whenever lexical or vector search is sent to Turbopuffer. This keeps both kinds of search scoped the same way.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 102–109)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: Creates the filter used when looking at all chunks for one owner scope. This is mainly for checking, deleting, and pruning a specific owner's stored chunks.

**Data flow**: It receives an `IndexScope` and, optionally, an id to continue after. It returns a Turbopuffer filter expression for matching owner kind and owner id, with an extra `id greater than after_id` condition when paging through results.

**Call relations**: `TurbopufferIndex.has_chunks` uses it for a quick existence check. `_scope_chunks` uses it repeatedly while walking through a scope page by page.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 112–121)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: Turns one Turbopuffer result row into UFO's standard `Hit` object. A hit is the shape the rest of UFO expects when a memory search finds a chunk.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It converts the row id back into a chunk digest, reads the owner, subject, order, and text fields, and returns a `Hit` with those values and the score.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this after `_query` returns raw rows. It is the translator between Turbopuffer's response format and UFO's recall format.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 124–130)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: Computes a score for a vector search result where bigger means better. This gives UFO a consistent way to compare vector hits.

**Data flow**: It receives a Turbopuffer row, the row's position in the result list, and the total number of rows. If Turbopuffer supplied a cosine distance, it returns `1 - distance`; otherwise it falls back to a descending rank score based on position.

**Call relations**: `TurbopufferIndex.vector` calls this before converting rows into hits. The score then goes into `hit_from_row`, so later recall code can combine or rank results.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 144–155)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Writes new or updated chunks into Turbopuffer. It skips chunks without embeddings because Turbopuffer's vector index needs those numbers for meaning-based search.

**Data flow**: It receives a tuple of chunks, keeps only the ones with embeddings, reads the API key through `_auth`, splits the chunks into batches, builds each JSON body with `upsert_body`, and posts the batches to the workspace namespace path from `_path`. It returns nothing, but Turbopuffer is updated; HTTP errors are raised if the service rejects a batch.

**Call relations**: The core index system calls this when memory chunks need to be stored. It depends on `_auth` for credentials, `_path` for the namespace URL, and `upsert_body` for the request shape.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 157–165)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks for one owner scope. This is used when an owner should no longer have any stored search data.

**Data flow**: It receives an `IndexScope`, gets authorization headers, asks `_scope_chunks` to list every chunk currently stored for that scope, converts their digests into Turbopuffer ids, and sends batched delete requests. It returns nothing, but matching documents are removed from Turbopuffer.

**Call relations**: The wider index system calls this during cleanup or removal. It first relies on `_scope_chunks` to discover what exists, then uses `_path` and `turbopuffer_id` to send precise delete batches.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 167–177)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes stale chunks for one owner while keeping a known current set. This matters when an owner is re-chunked: old chunks that are no longer present should not remain searchable.

**Data flow**: It receives a scope and a set of chunk digests to keep. It authenticates, lists all stored chunks in that scope, filters out the keep-set, converts the remaining digests to Turbopuffer ids, and sends batched delete requests. The result is that only unwanted old chunks are removed.

**Call relations**: Index maintenance calls this after re-indexing an owner. It shares the same discovery path as `delete` through `_scope_chunks`, but its decision step is more selective.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 179–185)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether Turbopuffer already has at least one chunk for a scope. This is a lightweight way to know if an owner has indexed content.

**Data flow**: It receives an `IndexScope`, builds a query asking for just one matching id, sends it to the namespace with authorization, and returns `true` if any rows come back. If the namespace does not exist yet, it returns `false` instead of treating that as a failure.

**Call relations**: Callers use this as a quick presence check. It uses `scope_filters` to describe the target owner scope, `_auth` to authorize the request, and `_path` to choose the workspace namespace.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 187–196)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a word-based search over indexed chunks using BM25 ranking. This helps find chunks that contain the same important words as the query.

**Data flow**: It receives query text, allowed subjects, owner kind, and a result limit. It trims the query with `bm25_query`, returns no results if the query or subject set is empty, asks `_query` to rank by text BM25, and turns each returned row into a `Hit` with a simple rank-based score.

**Call relations**: Recall code calls this when it wants lexical matches. It delegates the shared HTTP search work to `_query` and then uses `hit_from_row` to return standard UFO hits.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 198–207)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a meaning-based search over indexed chunks using the query embedding. This finds text that is semantically close even when the exact words differ.

**Data flow**: It receives an embedding, allowed subjects, owner kind, and a result limit. If the embedding or subject set is empty, it returns no hits. Otherwise it asks `_query` for approximate nearest neighbor vector results, computes scores with `vector_score`, drops non-positive scores, and returns `Hit` objects.

**Call relations**: Recall code calls this when it has an embedding for the query. It uses `_query` for the Turbopuffer request, `vector_score` to make the ranking useful to UFO, and `hit_from_row` to translate rows into hits.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 209–222)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: Sends the shared Turbopuffer search request used by both lexical and vector search. It centralizes the common pieces: ranking rule, filters, returned attributes, and missing-namespace behavior.

**Data flow**: It receives a `rank_by` instruction, owner kind, subject set, and result limit. It builds a JSON query with included attributes and `query_filters`, posts it with authorization to the namespace query endpoint, and returns the response rows as dictionaries. If the namespace does not exist, it returns an empty list.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this. Those higher-level methods decide how to rank and how to score; `_query` takes care of the actual HTTP request.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 224–252)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: Lists all chunks currently stored for one owner scope. It is used before deletion so the code knows exactly which document ids to remove.

**Data flow**: It receives a scope and already-built authorization headers. It repeatedly queries Turbopuffer for pages of rows, filtered by owner kind and owner id, converting each row into a lightweight `Chunk`. It keeps going until a short page means there is nothing more, then returns the collected chunks; if the namespace is missing, it returns what it has, usually an empty list.

**Call relations**: `TurbopufferIndex.delete` calls this to remove everything in a scope. `TurbopufferIndex.prune` calls it to compare stored chunks with the keep-set. It uses `scope_filters`, `_path`, and `chunk_digest_from_id` while walking through pages.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 254–256)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: Builds the HTTP authorization header for Turbopuffer. It reads the API key at request time from UFO's credential access object.

**Data flow**: It reads the `turbopuffer_api_key` credential slot and returns a dictionary containing an `Authorization: Bearer ...` header. It does not change stored state.

**Call relations**: All methods that talk directly to Turbopuffer call this before sending requests: writes, deletes, checks, and searches. This keeps credential reading in one place.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 258–259)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: Builds the Turbopuffer API path for this workspace's namespace. A namespace is the service-side container that keeps one workspace's indexed chunks separate.

**Data flow**: It reads the workspace id from the credential context, prefixes it with `ufo-`, appends any optional suffix such as `/query`, and returns the path string.

**Call relations**: Every method that sends an HTTP request uses this to target the correct namespace. Search methods pass a query suffix, while write and delete methods use the base namespace path.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 262–282)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO so it can be discovered and selected as an index backend. It also declares the needed Turbopuffer API key credential.

**Data flow**: It creates a `Manifest` containing the extension name and version, a credential slot named `turbopuffer_api_key`, and an index backend spec named `turbopuffer`. The backend factory builds a `TurbopufferIndex` with the runtime credential access object and an async HTTP client pointed at Turbopuffer's API.

**Call relations**: UFO's extension loading system calls this during startup. The returned manifest is how the rest of the system learns that `memory.index_backend = "turbopuffer"` can create a working `TurbopufferIndex`.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Runtime carriers and transports
Selects the active sandbox backend and registers Redis-backed shared hub and terminal transport options.

### `core/src/ufo/sandbox/select.py`

`orchestration` · `startup`

A sandbox is the isolated place where agent code or tasks can run without freely touching the host machine. This file is the gatekeeper that picks exactly one sandbox “carrier,” meaning the backend that actually provides those isolated spaces. There is always a built-in local option, and extensions can add more options, such as Docker, a cloud runner, or another remote service.

The file first builds a menu of available carriers. It starts with the built-in local carrier, then adds carriers declared by extension manifests. If two carriers try to use the same backend name, it stops immediately, because choosing between duplicates would be ambiguous and dangerous.

Next it reads the configured sandbox backend name. If the name is not in the menu, it fails loudly instead of falling back silently. This matters because a silent fallback could run code somewhere the operator did not intend.

There is one extra safety check for remote sandboxes, called “off-cluster” here, meaning the sandbox runs somewhere outside the current process or local environment. Remote sandboxes must be given a public HTTPS proxy URL. That proxy is how outbound network access can be controlled, credential-injected, denied by default, and measured. Without it, a remote sandbox might bypass the intended controls. If all checks pass, the file creates the selected carrier and returns it along with its description.

#### Function details

##### `select_carrier`  (lines 12–51)

```
def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Chooses the sandbox backend named in the configuration and creates the matching carrier object. It also checks that the backend name is registered only once and that remote backends have a secure public proxy URL.

**Data flow**: It receives the application configuration and a set of extension manifests. It builds a dictionary of available sandbox backends, beginning with the built-in local backend and then adding the ones contributed by extensions. It looks up the configured backend name, rejects missing or duplicate choices, checks remote backend proxy settings when needed, and finally returns two things: the newly created carrier and the carrier specification that describes it.

**Call relations**: This function is called when the runtime is deciding what sandbox system to use. During that decision, it creates the built-in carrier specification, may create clear error objects when the requested backend is unknown, and uses URL parsing to verify that a remote backend's proxy address is really an HTTPS URL. After it returns, the rest of the runtime can use the chosen carrier without repeating these safety and registration checks.

*Call graph*: 3 external calls (__init__, __init__, urlparse).


### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup / extension registration`

This is the extension “menu card” for the Redis hub package. The core system can normally keep live frame delivery and terminal connections inside one running server process. That is simple, but it does not work well when several server instances, or pods, need to act like one fleet. This file advertises two Redis-based replacements: a hub backend named “redis” and a terminal transport named “redis”. Redis is an external service often used as a fast shared message store; here it lets different server instances pass live frames and terminal traffic between each other.

Both Redis pieces use the same setting, `hub.url`, which is the address of the Redis server. The file deliberately checks this setting early. If someone selects the Redis backend but forgets the URL, it raises a clear error during setup instead of failing later in the middle of real work.

The `manifest()` function returns a `Manifest`, which is like a registration form for the extension. It says the extension is called `redis_hub`, gives its version, and lists the hub and terminal transport builders. The builders, `_build_hub` and `_build_terminal`, are the small factories that turn configuration into working Redis-backed objects.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed live-frame hub. It is used when the system has been configured to use `hub.backend = "redis"`.

**Data flow**: It receives a Redis URL, or `None` if no URL was configured. If the URL is missing, it stops immediately with a clear error message. If the URL is present, it passes that address into `RedisStreamHub` and returns the newly created hub object.

**Call relations**: The manifest registers this function inside a `HubSpec`, so the core system can call it when the Redis hub backend is chosen. Its main handoff is to `RedisStreamHub.__init__`, which builds the actual Redis Streams-based hub.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This function creates the Redis-backed terminal transport. It is used when the system has been configured to use `terminal.backend = "redis"`.

**Data flow**: It receives a Redis URL and a blob store, which is shared storage for larger pieces of terminal-related data. If the URL is missing, it raises a clear setup error. If the URL is present, it gives both the Redis address and blob store to `RedisTerminals` and returns the resulting terminal transport.

**Call relations**: The manifest registers this function inside a `TerminalTransportSpec`, so the core system can call it when the Redis terminal transport is selected. It hands construction off to `RedisTerminals.__init__`, which creates the object that carries terminal traffic across server instances.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension’s registration information. It tells the host system the extension name, version, and which Redis-backed components it can build.

**Data flow**: It reads the module constants for the extension name, version, and backend names. It wraps `_build_hub` in a `HubSpec`, wraps `_build_terminal` in a `TerminalTransportSpec`, then places both into a `Manifest` object and returns it.

**Call relations**: This is the entry point the extension loader is expected to ask for when discovering what the package provides. It creates `HubSpec`, `TerminalTransportSpec`, and `Manifest` objects so the core system can later call the right builder when Redis is selected in configuration.

*Call graph*: 3 external calls (__init__, __init__, __init__).
