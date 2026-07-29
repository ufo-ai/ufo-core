# Model and backend registration  `stage-2.3`

This stage is part of startup and shared setup. It builds the “catalogs” the rest of UFO consults when it needs a model or storage backend, like a phone book for services. The model specification file defines the standard information every AI model record must contain: name, provider, cost, limits, and special abilities. The built-in catalog fills that format with Anthropic and OpenAI models. The registry then gathers these records and gives other code one central place to look up pricing, required keys, and how to create a client connection.

Extensions add more entries to the same phone book. The Bedrock adapter registers Amazon-hosted models and their credentials. The OpenAI embedding extension registers a service that turns text into numeric vectors for meaning-based search. The Turbopuffer extension registers an index service that can store chunks and search them by meaning or by keywords. The Redis hub manifest tells UFO how to create a Redis-backed hub when that backend is selected.

## Files in this stage

### Model catalogs
Defines the built-in and Bedrock model offerings, exposes the combined registry, and anchors them in the shared model specification.

### `core/src/ufo/models/catalog.py`

`config` · `startup`

This file acts like a menu and price sheet for the AI models that ship with the core product. Without it, the rest of the system would not have one reliable place to ask: “What models are available?”, “Which company provides this model?”, “How much does it cost?”, “Which API key should I use?”, or “How do I connect to it?”

The file defines shared facts first: the environment variable names for API keys, default context windows, and a common reasoning setting. A context window is the maximum amount of text a model can consider at once. Reasoning support says these models can use reasoning features while also using tools.

Two small client factory functions, `_anthropic_client` and `_openai_client`, know how to turn an API key and a model description into a ready-to-use provider client. Two helper functions, `_anthropic` and `_openai`, build complete `ModelSpec` records for each provider, so the long catalog does not repeat the same setup details over and over.

The main function, `core_model_specs`, returns the full tuple of built-in models. Each entry includes pricing, knowledge cutoff, and special cases, such as one OpenAI model using the “responses” API surface because the chat endpoint would reject a certain request shape. At the bottom, the file builds ready-made constants for the default catalog, a price lookup table, and a pricing digest used to identify the exact pricing set.

#### Function details

##### `_anthropic_client`  (lines 24–25)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates a usable Anthropic model client from a model description and an API key. It is the bridge between the catalog’s plain model facts and the actual Anthropic software client that can make requests.

**Data flow**: It receives a `ModelSpec`, which describes the model, and a secret API key string. It first builds the lower-level Anthropic SDK client using that key, then wraps it together with the model spec inside an `AnthropicClient`. The result is a ready client object the rest of the system can use to talk to Anthropic for that specific model.

**Call relations**: This function is stored inside Anthropic `ModelSpec` objects by `_anthropic`. Later, when the system needs to use one of those Anthropic models, the spec can call this factory to create the actual provider client.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 28–29)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates a usable OpenAI model client from a model description and an API key. It lets the catalog describe OpenAI models without immediately opening a connection to OpenAI.

**Data flow**: It receives a `ModelSpec` and an API key. It uses the key to create the lower-level OpenAI SDK client, then combines that SDK client with the model spec inside an `OpenAIClient`. The output is a provider-specific client object ready to send requests for that model.

**Call relations**: This function is attached to OpenAI `ModelSpec` objects by `_openai`. When another part of the system chooses an OpenAI model and has the right key, this factory is what turns the stored model description into a working client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 32–51)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This helper builds a complete catalog entry for one Anthropic model. It fills in all the Anthropic-specific defaults so each model entry only needs to state what is unique, such as its id, price, cutoff date, and optional context size.

**Data flow**: It receives the model id, price, knowledge cutoff, API key environment variable name, and optionally a context window size. It combines those with fixed Anthropic facts: the provider name, the Anthropic client factory, the API key slot, reasoning support, and the chat API surface. It returns a `ModelSpec`, which is the system’s standard record for a model.

**Call relations**: `core_model_specs` calls this repeatedly while building the built-in Anthropic portion of the catalog. Each returned `ModelSpec` is one finished Anthropic entry that can later be registered, priced, and used to create a client.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 54–68)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds a complete catalog entry for one OpenAI model. It keeps the OpenAI setup details in one place, while allowing each catalog entry to supply its own id, price, cutoff date, and API style when needed.

**Data flow**: It receives the model id, price, knowledge cutoff, API key environment variable name, and optionally the API surface to use. It adds fixed OpenAI facts: the provider name, OpenAI client factory, shared context window, API key slot, and reasoning support. It returns a `ModelSpec` describing that OpenAI model in the same format used across the system.

**Call relations**: `core_model_specs` calls this for every built-in OpenAI model. Most entries use the normal chat API surface, but one entry passes a different surface so later request-building code will use the legal OpenAI endpoint for that model.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 71–160)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds the full built-in model catalog for the core system. It is the single source of truth for which Anthropic and OpenAI models are included by default and what their prices and capabilities are.

**Data flow**: It receives the names of the environment variables that should contain the Anthropic and OpenAI API keys. It creates `ModelPrice` objects for each model, passes those prices and other model facts into `_anthropic` or `_openai`, and returns all resulting `ModelSpec` records as a tuple. Nothing is sent to the providers here; it only prepares descriptions and client-making instructions.

**Call relations**: This function is called at module load time to create `CORE_MODEL_SPECS`, the ready default catalog. The module then derives `CORE_PRICES`, `CORE_PRICING`, and `PRICE_DIGEST` from that catalog so pricing and ledger code can refer to the same model facts without rebuilding them.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `extension discovery and model setup`

This file is a provider plug-in for Amazon Bedrock Mantle. Think of it like a catalog page plus the instructions for dialing the right phone number. The catalog lists each supported Bedrock model, its price, its context window size, its knowledge cutoff date, and whether it supports reasoning features. The dialing instructions say how to create the correct client object when the system needs to actually talk to a model.

There are two model families here. Anthropic model IDs use Anthropic’s Bedrock Mantle client. OpenAI-compatible model IDs use an OpenAI-style client, but pointed at Bedrock Mantle’s AWS endpoint instead of OpenAI’s own servers. Both need an AWS Bedrock bearer token, read through a named credential slot, and both need an AWS region. The region comes from the usual environment variables, AWS_REGION or AWS_DEFAULT_REGION; if neither is set, the file deliberately stops with a clear error because it cannot build a valid endpoint.

At the end, the manifest function packages this all up into a Manifest. That manifest is what the larger UFO system reads when it discovers this extension: it learns the provider name, version, required credential, and available models.

#### Function details

##### `bedrock_region`  (lines 42–48)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock Mantle requests should use. This matters because Bedrock endpoints are regional, so the system cannot build a working web address without it.

**Data flow**: It reads AWS_REGION first, then AWS_DEFAULT_REGION from the process environment. If it finds a value, it returns that region string. If both are missing, it raises an error telling the user to set one of them.

**Call relations**: _anthropic_client and _openai_client call this when they are building provider clients. It supplies the region they need before they can create either the Anthropic Bedrock client or the OpenAI-compatible Bedrock URL.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 51–63)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds the client used to talk to Anthropic models through Amazon Bedrock Mantle. It turns a model description and API key into an AnthropicClient that the rest of UFO can use in a standard way.

**Data flow**: It receives a ModelSpec, which describes the model, and a Bedrock API key. It asks bedrock_region for the AWS region, creates an Anthropic Bedrock Mantle async client with that key, region, retry setting, and timeout, then wraps it in UFO’s AnthropicClient together with the model spec. The result is a ready-to-use client wrapper.

**Call relations**: This is the client factory attached to Anthropic-style model specs. When the system later needs to call one of those models, this function builds the actual Anthropic Bedrock connection, after first asking bedrock_region where that connection should go.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 66–73)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds the client used to talk to OpenAI-compatible models hosted through Bedrock Mantle. It chooses the correct Bedrock URL shape for the model’s API style and returns UFO’s standard OpenAIClient wrapper.

**Data flow**: It receives a ModelSpec and a Bedrock API key. It reads the AWS region, builds a base URL for either the OpenAI Responses-style endpoint or the Chat Completions-style endpoint, creates an OpenAI SDK client pointed at that URL, and wraps it with the model spec. The output is a client object ready for UFO to send requests through.

**Call relations**: This is the client factory attached to OpenAI-compatible model specs. It depends on bedrock_region for the regional endpoint and hands the finished SDK client to OpenAIClient so the wider system can treat these Bedrock models like other OpenAI-style models.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 76–94)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Creates one ModelSpec entry for an Anthropic model available through Bedrock. A ModelSpec is the system’s compact record of how to price, describe, authenticate, and call a model.

**Data flow**: It receives the model ID, pricing, knowledge cutoff date, and optionally a context window size. It combines those with shared Bedrock settings, reasoning support, the Anthropic client factory, and the Bedrock credential information. It returns a ModelSpec that can be placed in the provider’s model list.

**Call relations**: The file uses this helper while building BEDROCK_MODEL_SPECS, so each Anthropic model is described in the same consistent way. The spec it creates points back to _anthropic_client, which will later create the live network client when the model is used.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 97–111)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates one ModelSpec entry for an OpenAI-compatible model available through Bedrock. It records the model’s identity, cost, API style, context size, and credential requirements.

**Data flow**: It receives the model ID, pricing, knowledge cutoff date, context window size, and API surface, meaning the request style the model expects. It adds shared Bedrock settings, reasoning support, the OpenAI-compatible client factory, and the Bedrock credential details. It returns a ModelSpec for the provider’s model list.

**Call relations**: The file uses this helper while building BEDROCK_MODEL_SPECS for OpenAI-compatible models. The resulting spec points to _openai_client, so when UFO later invokes that model, the correct Bedrock Mantle OpenAI-style endpoint can be created.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 182–193)

```
def manifest() -> Manifest
```

**Purpose**: Returns the extension manifest that advertises this Bedrock provider to the UFO system. The manifest is the package label: it says what the extension is, what credential it needs, and which models it offers.

**Data flow**: It uses the file’s constants and model list to build a Manifest. It also creates a CredentialSlot named for the Bedrock API key, with a human-readable description. The returned Manifest contains the provider name, version, credential requirement, and all Bedrock model specs.

**Call relations**: The larger extension-loading system calls this function when it wants to discover what this file provides. manifest does not create model clients itself; instead, it hands over model specs that contain the client-building functions to use later.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/models/registry.py`

`domain_logic` · `startup and model request handling`

This file solves a coordination problem: many parts of the system need to know which AI model is being used, how much it costs, which provider runs it, and what secret key is needed to call it. Instead of letting each part guess, this file creates one shared registry, like a front desk with a card for every approved model.

The registry starts with the built-in model definitions, then adds model definitions contributed by extension manifests. Each model must have a unique id. If two models claim the same id, startup fails clearly, so one model cannot quietly replace another. It also checks that the configured “auto” model points to a real registered model. “Auto” is a placeholder meaning “use the deployment’s default model.”

Once built, `ModelRegistry` can look up a model specification, turn `auto` into the concrete model id, build the right `ModelClient` for making calls, and answer which bring-your-own-key slot belongs to a model. A bring-your-own-key slot is where a workspace may store its own provider API key. If a model needs a key, the registry first asks the current workspace for it, falling back to the configured environment variable. Missing keys fail with a clear error only when that model is actually used.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name `auto` into the real default model configured for this deployment. If the caller already chose a specific model id, it leaves it unchanged.

**Data flow**: It receives a model name. If that name is the shared `auto` placeholder, it returns the registry’s configured `auto_model`; otherwise it returns the original name. It does not change the registry.

**Call relations**: When onboarding checks which API key a model will need, `ModelRegistry.model_key_env` calls this first so the check is based on the real model that will run, not the placeholder name.

*Call graph*: called by 1 (model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This fetches the registered facts for one model id. It gives the rest of the system one reliable place to fail if someone asks for a model that does not exist.

**Data flow**: It receives a model id and looks it up in the registry’s `specs` table. If found, it returns that model’s `ModelSpec`; if not, it raises a clear `ValueError` saying no model is registered for that id.

**Call relations**: Both `ModelRegistry.client_for` and `ModelRegistry.model_key_env` rely on this lookup before they can do their work. That keeps unknown-model errors centralized instead of appearing later as provider errors, rendering errors, or billing mistakes.

*Call graph*: called by 2 (client_for, model_key_env).


##### `ModelRegistry.client_for`  (lines 44–61)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This builds the client object that can actually talk to the provider for a chosen model. It also finds the right secret key for that model at the moment the model is used.

**Data flow**: It receives a model id, looks up its `ModelSpec`, then checks whether that model needs an API key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the key slot or environment-backed value. If the key is missing, it raises a helpful runtime error; otherwise it returns a `ModelClient` built from the spec and key.

**Call relations**: This function first calls `ModelRegistry.spec` to get the model’s facts. It then calls `ufo.workspace.ws_current` to access the active workspace, because workspace-specific bring-your-own-key secrets may override platform defaults. It is the bridge from a registered model description to a ready-to-use provider client.

*Call graph*: calls 1 internal fn (spec); 1 external calls (ws_current).


##### `ModelRegistry.key_slot_for`  (lines 63–70)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This answers which workspace key slot would be used for a model, if any. It is designed to be safe for reporting or billing code, even when old records mention models that are no longer registered.

**Data flow**: It receives a model id and checks the registry table without raising an error. If the model is missing, or if the model does not use a workspace key slot, it returns `None`. Otherwise it returns the key slot name.

**Call relations**: Unlike `ModelRegistry.spec`, this lookup is intentionally forgiving. That matters for flows such as billing exports, where an old ledger row should be labeled as platform-served or unknown rather than crashing the export.


##### `ModelRegistry.model_key_env`  (lines 72–82)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding which environment variable must be set before a model can run, for the built-in Anthropic and OpenAI providers. For extension-provided models whose key rules are not known here, it returns no eager environment check.

**Data flow**: It receives a model name and the loaded configuration. It first resolves `auto` to the real default model, then looks up that model’s spec and reads its provider. If the provider is Anthropic, it returns the configured Anthropic key environment variable; if OpenAI, it returns the configured OpenAI key environment variable; otherwise it returns `None`.

**Call relations**: This function calls `ModelRegistry.resolve` so `auto` is checked against the model that will actually run. It then calls `ModelRegistry.spec` to read the provider. It supports early setup checks, while leaving contributed models to resolve their keys later through `client_for`.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 85–106)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the complete `ModelRegistry` from built-in model definitions, extension manifest model definitions, and configuration. It is the startup step that turns scattered model information into one checked table.

**Data flow**: It receives the loaded configuration and a tuple of manifests. It asks the catalog for the core model specs, adds all manifest-provided specs, rejects duplicate model ids, checks that the configured `auto_model` exists, builds a merged pricing table from every spec’s price, and returns a frozen `ModelRegistry` containing the specs, pricing, and default model id.

**Call relations**: During setup, this function calls `core_model_specs` to get built-in models and `pricing_from` to create the shared price table. It then constructs `ModelRegistry`, which later serves lookups, client creation, key checks, and pricing access throughout model-related runtime flows.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### `core/src/ufo/models/spec.py`

`data_model` · `model registry setup and request preparation`

This file is the system’s model fact sheet. Each supported AI model gets a frozen `ModelSpec`, meaning a record that cannot be changed after it is created. That matters because many parts of the system need the same facts: which provider owns the model, how to build its client, what API style it uses, how much it costs, what its context window is, and whether it supports “reasoning” mode.

Without this shared record, different parts of the app could disagree. One part might try to call a model using the wrong API, another might bill it incorrectly, and another might build a prompt with the wrong knowledge cutoff date. This file helps failures happen early and clearly instead of later in the middle of a user request.

The file also defines `ReasoningSupport`, a small record that says whether a model can use extended reasoning, and whether that reasoning can be combined with tool calls. Tool calls are when the model is allowed to ask the system to run a helper, such as a search or calculation tool.

`ModelSpec` validates important promises when it is created. The knowledge cutoff must look like `YYYY-MM`, and a model cannot claim it supports reasoning with tools if it does not support reasoning at all. It also decides, for a specific request, whether the requested reasoning level should actually be sent or forced off.

#### Function details

##### `ModelSpec.__post_init__`  (lines 54–62)

```
def __post_init__(self) -> None
```

**Purpose**: This runs right after a `ModelSpec` is created and checks that the model facts make sense. It catches bad registry entries early, before the system tries to call or bill a model using invalid information.

**Data flow**: A newly created `ModelSpec` goes in, including its knowledge cutoff text and reasoning capability flags. The function checks that the cutoff is in year-month form, such as `2024-06`, and checks that the model is not claiming tool-compatible reasoning without reasoning support. If everything is valid, nothing is changed; if something is wrong, it raises an error with a clear message.

**Call relations**: This is called automatically by the dataclass machinery whenever a `ModelSpec` is constructed. It protects later code that reads model facts from the registry, because those later callers can trust that these basic rules were already enforced.


##### `ModelSpec.default_reasoning`  (lines 64–74)

```
def default_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort
```

**Purpose**: This decides the reasoning effort that should actually be sent to the model for one request. It respects the user or system’s requested reasoning level only when the model and the current tool setup can safely support it.

**Data flow**: The function receives a requested reasoning effort and the tools included in the request. It reads the model’s reasoning support settings from the `ModelSpec`. If the model does not support reasoning, it returns `off`; if tools are present but this model cannot combine tools with reasoning, it also returns `off`; otherwise it returns the requested effort unchanged.

**Call relations**: Request-building code can call this before sending work to a model. It acts like a small safety gate between the registry’s model facts and the final API call, preventing the system from asking a model for a reasoning-and-tools combination that the model has declared unsupported.


### Embedding and index backends
Registers the OpenAI embedding provider and the Turbopuffer index backend used for semantic and lexical retrieval.

### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and background embedding work`

This extension is the project’s built-in way to create embeddings, which are lists of numbers that represent the meaning of text. Those vectors let the system compare text by similarity, a bit like putting ideas on a map so nearby ideas can be found together.

The file registers itself as the default embedding backend, so if the rest of the system asks for embeddings and no other backend is chosen, this one is used. It does not require an OpenAI key just to start the app. Instead, it reads `OPENAI_API_KEY` from the environment only when an embedding call actually happens. That means local development can boot without setup, but a real embedding request fails clearly if the key is missing.

Before sending text to OpenAI, the file trims very large items and groups texts into safe-sized batches. This protects the provider call from being too large. The client then sends each batch to OpenAI’s async API, keeps the returned vectors in the same order as the input texts, and returns them as plain tuples of floats.

The file also exposes a `manifest`, which is how the larger extension system discovers this backend and learns that it should be called `default`.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for OpenAI by cutting each item down to the allowed size and grouping items into batches that stay under both item-count and character-count limits. It is used to avoid sending requests that are too large.

**Data flow**: It receives a tuple of text strings. For each string, it keeps only the first allowed number of characters, then adds it to the current batch unless that would make the batch too large. It returns a tuple of batches, where each batch is a tuple of clipped strings ready to send.

**Call relations**: When `OpenAIEmbedClient.embed` is about to call OpenAI, it first asks this function to split the input texts into safe chunks. The embedding client then sends those chunks one at a time.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This async method turns input texts into OpenAI embedding vectors. Someone uses it when the system needs searchable numeric representations of text.

**Data flow**: It receives a tuple of texts. It reads `OPENAI_API_KEY` from the environment; if the key is missing, it raises a clear error. It creates an OpenAI async client, splits the texts into safe batches, sends each batch to OpenAI, sorts the provider’s response back into input order, converts each embedding value to a float, and returns all vectors as tuples.

**Call relations**: The wider indexing system calls this method through the embedding client interface. Inside, it relies on `plan_embed_batches` to shape the request safely, then hands each batch to `openai.AsyncOpenAI` so the external OpenAI service can produce the embeddings.

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client object that the extension system will use. It deliberately does not check for the OpenAI API key at startup, so the app can boot even before embeddings are needed.

**Data flow**: It receives an `ExtensionContext`, which represents the workspace or extension environment, but this backend does not need to read anything from it. It returns a new `OpenAIEmbedClient` configured with the default model.

**Call relations**: The extension manifest points to this function as the factory for the default embedding backend. When the core system resolves that backend during startup, it calls `build`, which creates the client used later for actual embedding calls.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–89)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what this extension is and what embedding backend it provides. It is the discovery card for the extension.

**Data flow**: It uses the file’s constants, such as the extension name, version, and backend name, to build a `Manifest`. That manifest contains an `EmbedBackendSpec` saying that the backend named `default` should be created by `build`.

**Call relations**: The extension loader calls `manifest` when discovering available extensions. The returned manifest hands the system an embedding backend specification, which later leads to `build` being called when the default embedding backend is needed.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup registration, then indexing and search requests`

This file is the bridge between UFO’s internal idea of an index and Turbopuffer’s HTTP API. UFO works with chunks: small pieces of text with an ID, owner information, subject, order number, optional embedding, and text. Turbopuffer stores those chunks as documents inside a workspace-specific namespace, like giving each workspace its own labeled filing cabinet.

The file does three main jobs. First, it registers the backend through `manifest`, so the rest of the system can select it by name. Second, it translates UFO objects into Turbopuffer request bodies. For example, it turns chunk digests into shorter document IDs, builds filter expressions, and formats rows returned by Turbopuffer back into UFO `Hit` objects. Third, `TurbopufferIndex` performs the real work over HTTP: inserting chunks, deleting a whole owner scope, pruning old chunks after re-chunking, checking whether chunks exist, and running lexical or vector searches.

Authentication is done on each request by reading the workspace’s Turbopuffer API key from a credential slot and sending it as a Bearer token. A missing Turbopuffer namespace is treated as empty for reads, which makes first-time use smoother. Without this file, UFO could not use Turbopuffer as a storage and retrieval backend for memory search.

#### Function details

##### `turbopuffer_id`  (lines 42–48)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: This turns UFO’s chunk digest into a document ID that Turbopuffer can store comfortably. Standard SHA-256 digests are shortened using URL-safe base64, while non-standard IDs are left alone.

**Data flow**: It receives a chunk digest string. If the string looks like `sha256:` followed by 64 hex characters, it converts the raw bytes into a shorter base64url string and removes the padding; otherwise it returns the original string unchanged.

**Call relations**: When chunks are written, `upsert_body` uses this to prepare Turbopuffer document IDs. When chunks are removed, `TurbopufferIndex.delete` and `TurbopufferIndex.prune` use the same conversion so they delete the exact IDs that were stored.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 51–60)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: This reverses the shortened Turbopuffer ID back into UFO’s normal chunk digest form when possible. It keeps search results and exported chunks using the same IDs UFO originally understands.

**Data flow**: It receives a document ID from Turbopuffer. If it has the expected shortened base64url length and can be decoded, it returns a `sha256:` digest; if not, it returns the ID unchanged.

**Call relations**: Rows coming back from Turbopuffer pass through this during result conversion. `hit_from_row` uses it for search hits, and `_scope_chunks` uses it when listing chunks for delete or prune work.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 63–79)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: This builds the JSON body used to insert or update a batch of chunks in Turbopuffer. It arranges the data in Turbopuffer’s expected column-style format.

**Data flow**: It receives a tuple of `Chunk` objects. It extracts IDs, embeddings, owner fields, subject, order, and text into parallel lists, then returns a dictionary that also tells Turbopuffer to use cosine distance for vectors and enable full-text search on the text field.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch. Inside this helper, `turbopuffer_id` prepares each chunk’s storage ID before the HTTP request is sent.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `query_filters`  (lines 82–86)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: This creates the Turbopuffer filter used during searches. It limits results to the requested owner type and the allowed subjects, so searches do not pull unrelated memory.

**Data flow**: It receives an owner kind and a set of subjects. It sorts the subjects and returns a filter expression meaning: owner kind must match, and subject must be one of these values.

**Call relations**: `TurbopufferIndex._query` uses this whenever lexical or vector search is performed. It is the shared guardrail that keeps both search modes scoped to the same recall area.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 89–96)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: This builds a Turbopuffer filter for one exact index scope: a specific owner kind and owner ID. It can also continue after a previous document ID when paging through many chunks.

**Data flow**: It receives an `IndexScope` and optionally an `after_id`. It returns a filter expression requiring the matching owner kind and owner ID, and adds an `id greater than after_id` condition when continuing a paged listing.

**Call relations**: `TurbopufferIndex.has_chunks` uses this for a small existence check. `_scope_chunks` uses it repeatedly while walking through all chunks in a scope for delete and prune operations.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 99–108)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: This turns one Turbopuffer result row into UFO’s `Hit` object. A `Hit` is the common result shape the rest of UFO expects from any index backend.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It converts the stored ID back to a chunk digest, reads the owner fields, subject, ordinal, and text, and returns a `Hit` with that data and score.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this after receiving rows from `_query`. It is the final translation step from Turbopuffer’s format back into UFO’s format.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 111–117)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: This converts a vector-search result into a score where larger means better. That matters because UFO’s later ranking steps expect higher scores to mean stronger matches.

**Data flow**: It receives a Turbopuffer row, that row’s position in the result list, and the total number of rows. If Turbopuffer supplied a cosine distance, it returns `1 - distance`; otherwise it falls back to a descending rank-based score.

**Call relations**: `TurbopufferIndex.vector` uses this before creating `Hit` objects. It provides a consistent score even if Turbopuffer does not include the distance field in the response.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 131–142)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This stores new or changed chunks in Turbopuffer. It skips chunks without embeddings because this backend stores searchable vector documents.

**Data flow**: It receives a tuple of chunks. It filters out chunks with no embedding, reads authentication headers, splits the remaining chunks into batches, turns each batch into an upsert body, and sends each body to the workspace namespace. It returns nothing, but Turbopuffer is updated.

**Call relations**: This is called when UFO needs the index to learn about chunks. It relies on `_auth` for the API key, `_path` for the namespace URL, and `upsert_body` for the request format.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 144–152)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This deletes all indexed chunks for one owner scope. It is used when a whole item’s indexed memory should be removed.

**Data flow**: It receives an `IndexScope`. It gets authorization, lists all chunks in that scope, converts their chunk digests into Turbopuffer IDs, sends delete requests in batches, and leaves Turbopuffer without those documents.

**Call relations**: This operation first asks `_scope_chunks` to enumerate what exists. It then uses `turbopuffer_id`, `_path`, and the shared authenticated HTTP client to remove the matching documents.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 154–164)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes old chunks from a scope while keeping a supplied set of current chunk digests. It is useful after re-chunking, when new chunks replace older ones and leftovers must not stay searchable.

**Data flow**: It receives a scope and a keep-set of chunk digests. It lists all chunks in the scope, selects only those not in the keep-set, converts them to Turbopuffer IDs, and sends batched delete requests. The result is that only desired chunks remain.

**Call relations**: Like `delete`, it depends on `_auth`, `_scope_chunks`, `_path`, and `turbopuffer_id`. The difference is that it deletes selectively instead of wiping the whole scope.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 166–172)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This quickly checks whether a scope has any indexed chunks. It asks Turbopuffer for just one matching row instead of listing everything.

**Data flow**: It receives an `IndexScope`. It builds a one-row query sorted by ID, sends it with authentication, treats a missing namespace as empty, and returns `true` if any row is found or `false` otherwise.

**Call relations**: This is a lightweight read used before or during index workflows that need to know whether data exists. It uses `scope_filters` to describe the target scope, plus `_auth` and `_path` for the HTTP request.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 174–182)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This runs a word-based search over chunk text using BM25, a ranking method that favors documents containing important query terms. It returns UFO `Hit` objects for matching chunks.

**Data flow**: It receives a text query, allowed subjects, owner kind, and result limit. If the query is blank or there are no subjects, it returns no hits. Otherwise it asks `_query` to rank by text BM25, then converts each row into a `Hit` with a rank-based score.

**Call relations**: This is one of the public search paths used by UFO recall. It delegates the HTTP query details to `_query` and then uses `hit_from_row` to produce backend-neutral results.

*Call graph*: calls 2 internal fn (_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 184–193)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This runs a meaning-based search using an embedding, which is a list of numbers representing the meaning of text. It finds chunks with nearby vectors and returns them as UFO `Hit` objects.

**Data flow**: It receives an embedding, allowed subjects, owner kind, and result limit. If the embedding or subject set is empty, it returns no hits. Otherwise it queries Turbopuffer using approximate nearest neighbor vector search, scores each row, drops non-positive scores, and returns hits.

**Call relations**: This is the vector-search partner to `lexical`. It calls `_query` to talk to Turbopuffer, `vector_score` to normalize scores, and `hit_from_row` to return UFO’s standard hit format.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 195–208)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: This is the shared low-level search helper for lexical and vector queries. It builds the common Turbopuffer query request and returns raw rows.

**Data flow**: It receives a `rank_by` instruction, owner kind, subject set, and result limit. It builds a request asking for the wanted attributes, applies scope filters, sends it to `/query`, returns an empty list if the namespace does not exist, or returns the response rows after checking for errors.

**Call relations**: `lexical` and `vector` both use this so their HTTP behavior stays consistent. It gets authorization through `_auth`, builds the namespace path through `_path`, and uses `query_filters` to keep results within the allowed memory area.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 210–238)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: This lists all chunks that belong to one owner scope. It is mainly used before deleting or pruning, because Turbopuffer deletion needs document IDs.

**Data flow**: It receives a scope and already-prepared HTTP headers. It repeatedly queries Turbopuffer for pages of rows sorted by ID, converts each row into a lightweight `Chunk`, and advances using the last ID until fewer than a full page is returned. If the namespace is missing, it returns whatever has been collected, usually nothing.

**Call relations**: `delete` and `prune` call this before deciding which IDs to remove. It uses `_path` for the query endpoint, `scope_filters` for paging and scoping, and `chunk_digest_from_id` to restore UFO chunk digests.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 240–242)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: This prepares the HTTP authorization header for Turbopuffer. It reads the workspace’s API key at request time and formats it as a Bearer token.

**Data flow**: It reads the `turbopuffer_api_key` credential from the credential access object. It returns a dictionary with an `Authorization` header containing that key.

**Call relations**: Most operations call this before sending HTTP requests: upsert, delete, prune, existence checks, and shared queries. It keeps credential reading in one place instead of spreading API-key formatting throughout the file.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 244–245)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: This builds the Turbopuffer API path for the current workspace namespace. The namespace name is prefixed so UFO’s data is grouped predictably.

**Data flow**: It receives an optional suffix such as `/query`. It combines the fixed namespace prefix, the workspace ID from credentials, and the suffix into a path like `/namespaces/ufo-<workspace>/query`.

**Call relations**: Every HTTP operation uses this to target the right workspace namespace. Upsert, delete, prune, `has_chunks`, `_query`, and `_scope_chunks` all rely on it before calling Turbopuffer.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 248–268)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to UFO and tells the system how to build the Turbopuffer index backend. It also declares the credential slot where the Turbopuffer API key must be provided.

**Data flow**: It creates and returns a `Manifest` with the extension name, version, one credential requirement, and one index backend specification. The backend factory receives a context, takes its credential access object, creates an async HTTP client pointed at Turbopuffer, and returns a `TurbopufferIndex`.

**Call relations**: The core system calls this during extension discovery or startup. The returned manifest is how UFO learns that `memory.index_backend = "turbopuffer"` can be satisfied by constructing `TurbopufferIndex`.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Hub backend manifest
Declares the Redis hub extension so the main system can construct the configured hub backend.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup / config load`

The core system has a “hub” seam: a place where live frames are published and shared. By default, that may work only inside one running server process. This extension offers a Redis Streams version, which uses Redis as a shared message channel so multiple server instances can receive the same live frames. Redis is an external in-memory data store often used for fast messaging and coordination.

This file is the extension’s front desk. It gives the extension a name and version, declares that it supports the backend name "redis", and tells the core system what function to call when that backend is selected. The important safety behavior is that it refuses to start without a Redis URL. That means a missing `hub.url` is caught immediately during setup, instead of failing later when the first frame is published.

In everyday terms, this file is like a plug adapter label and instruction card: it says “I am the Redis hub plug-in,” “ask for me using the name redis,” and “to build me, you must provide the Redis address.”

#### Function details

##### `_build_hub`  (lines 17–22)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: Builds the actual Redis-backed hub object from a Redis URL. It also protects the system from starting with an incomplete Redis hub setup by raising a clear error if no URL was provided.

**Data flow**: It receives a URL value, which may be a real Redis address or may be missing. If the URL is missing, it stops immediately with a runtime error explaining that `hub.url` is required. If the URL is present, it passes that address into `RedisStreamHub` and returns the new hub object.

**Call relations**: This function is not the public manifest itself; it is handed to the core system inside a `HubSpec`. Later, when the core sees that the configured hub backend is "redis", it calls this builder. The builder then hands off to `RedisStreamHub.__init__` to create the real Redis Streams hub.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 25–30)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest that the core system reads to discover this Redis hub backend. It packages the extension name, version, backend name, and builder function into one registration object.

**Data flow**: It starts from the constants in this file: the extension name, version, and backend key "redis". It creates a `HubSpec` saying that the "redis" backend should be built with `_build_hub`, then wraps that in a `Manifest` and returns it to the extension loader.

**Call relations**: This is the entry point the extension loader calls when it scans available extensions. It constructs a `HubSpec` and a `Manifest`, which tell the core system that choosing `hub.backend = "redis"` should eventually route hub construction through `_build_hub`.

*Call graph*: 2 external calls (__init__, __init__).
