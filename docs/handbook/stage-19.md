# Model, Provider, Embedding, Search, and Feature-Flag Infrastructure  `stage-19` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It gives the rest of the system one reliable way to know which AI models, search services, and feature switches are available. The models package marker simply makes the model code importable. The model spec file is the master form for describing a model: provider, price, limits, supported features, and needed credentials. The interface file defines the common request and response shape used with providers, and it safely replaces images when a provider cannot accept them.

The registry is the lookup desk. Other code asks it what a model is, what it costs, and how to create a client. The catalog fills that desk with built-in Anthropic and OpenAI model facts. The Bedrock extension adds Amazon Bedrock-hosted models in the same format, so they fit the same machinery.

The search file defines a common contract for web search and page fetching, so the system is not tied to one search provider. The flags file is the main doorway for reading feature flags, and the Flagship extension connects those switches to Cloudflare, including simple admin updates.

## Files in this stage

### Model foundations
Core package, model specifications, provider interface, and registry code define the shared model vocabulary and lookup layer.

### `core/src/ufo/harness/models/__init__.py`

`data_model` · `import time`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that a folder should be treated as an importable package. Here, it allows code elsewhere in the project to refer to `core/src/ufo/harness/models` as a module path and import any model files placed inside that folder. Think of it like a label on a drawer: the label does not contain the tools, but it makes the drawer part of the organized workspace. Without this file, depending on the Python version and packaging setup, imports from this folder could be less predictable or fail in some environments. Because the file is empty, it does not define any types, functions, startup behavior, or shared settings.


### `core/src/ufo/harness/models/spec.py`

`data_model` · `model registry setup and per-request model preparation`

This file is like a passport office for models: every model gets one trusted record, and other parts of the system check that record instead of guessing. Without it, pricing, routing, prompt setup, credential errors, and reasoning settings could drift apart and fail later in confusing ways.

The main type is `ModelSpec`, a frozen data record, meaning its fields cannot be changed after creation. It says what the model is called, who provides it, how to build a client for it, how much it costs, how much text it can fit in one request, whether it accepts images, and which API surface it uses. It also records where to find the model’s API key, either from a workspace “bring your own key” slot or from an environment variable.

`ReasoningSupport` is a smaller record that describes whether a model can do extended reasoning, whether that reasoning can be used together with tools, and whether it can be turned off.

The file also protects the system from bad model entries early. When a `ModelSpec` is created, it checks that the knowledge cutoff is written as `YYYY-MM`, and that reasoning-related flags do not contradict each other. Later, it helps convert provider authentication failures into a clear credential error, and decides whether a request should send a reasoning setting at all.

#### Function details

##### `ReasoningSupport.internal_effort`  (lines 36–39)

```
def internal_effort(self) -> ReasoningEffort
```

**Purpose**: This function chooses the safe internal reasoning effort to use when the system needs a default. If the model does not support reasoning, or if reasoning can be turned off, it reports `off`; otherwise it falls back to the model’s minimum required effort.

**Data flow**: It reads the `ReasoningSupport` fields on the current object: whether reasoning is supported, whether it can be disabled, and what the minimum effort is. From those facts, it returns either `off` or the declared minimum effort. It does not change anything.

**Call relations**: No direct caller is shown in the supplied graph. In the larger model setup flow, this is the small decision point that turns a model’s reasoning policy into a concrete effort value other request-building code can use.


##### `ModelSpec.__post_init__`  (lines 66–76)

```
def __post_init__(self) -> None
```

**Purpose**: This function checks that a newly created model record makes sense. It catches invalid or contradictory model metadata immediately, before the model is used in a live request.

**Data flow**: After a `ModelSpec` is created, it reads fields such as `knowledge_cutoff` and `reasoning`. It verifies that the cutoff looks like a year and month, such as `2024-06`, and that the reasoning flags are consistent. If everything is valid, nothing visible is returned; if something is wrong, it raises a `ValueError` explaining the bad model entry.

**Call relations**: This is automatically run by the dataclass creation process when a `ModelSpec` is constructed. It acts as an early guardrail so later model routing, prompting, and request code can trust the record instead of re-checking these basic facts.


##### `ModelSpec.key_rejected`  (lines 78–89)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This function turns a provider’s rejected API key into a clear, project-specific credential error. It tells the user which environment variable or workspace key slot might contain the bad key and that it must be replaced.

**Data flow**: It reads the model’s ID, provider name, environment key name, and workspace key slot. It builds a human-readable error message from those facts, then creates and returns a `CredentialValueInvalid` error object. It does not contact the provider or change the key; it only explains the failure.

**Call relations**: The supplied graph shows this function handing off to `CredentialValueInvalid.__init__` to build the typed credential fault. It is meant to be used when a provider returns an authentication rejection, so the failure is reported as a fixable bad-key problem rather than as a vague stream or model error.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 91–105)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This function decides what reasoning setting, if any, should be sent with a model request. It respects the model’s limits, especially whether reasoning is supported and whether reasoning can be combined with tool use.

**Data flow**: It takes the reasoning effort requested for this call and the tools being sent with the request. It reads the model’s reasoning capabilities. If the model cannot reason, or cannot reason while tools are present, it returns `None`, meaning no reasoning setting should be sent. If the request asks for `off` but the model has always-on reasoning that cannot be disabled, it returns the model’s minimum effort instead. Otherwise it returns the requested effort unchanged.

**Call relations**: No direct caller is shown in the supplied graph. In the larger request-building flow, this function is the gatekeeper that prevents the system from sending reasoning parameters a model or API surface does not accept.


### `core/src/ufo/harness/models/interface.py`

`data_model` · `request preparation and model streaming`

Different AI providers have different request shapes, streaming events, tool-call formats, reasoning blocks, and image limits. This file gives the project one shared set of message and event types so the rest of the code can think in one format before provider-specific clients translate it. It is like a standard order form used by several delivery companies: each company may route it differently, but everyone starts from the same fields.

Most of the file is data shapes: text blocks, image blocks, tool calls, tool results, reasoning blocks, model requests, and streaming response events. These are Pydantic models, meaning they are structured objects that also check that required fields and allowed values are correct. `ModelClient` is a protocol, which is a promise that any model client must offer a `complete` method that streams model events back.

The file also includes image cleanup helpers. Some providers cap how many images can appear in one message, in one request, or in total byte size. `trim_images` keeps the newest images that fit those limits and replaces older or oversized ones with a short note. `omit_images` does the same replacement for models that cannot accept images at all. This avoids hard provider failures while still telling the model that an image was present but removed.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool`  (lines 157–162)

```
def _forced_choice_names_an_offered_tool(self) -> 'ModelRequest'
```

**Purpose**: This checks that when a request forces the model to use a specific tool, that tool was actually offered in the same request. It prevents sending an impossible instruction to a model provider.

**Data flow**: A newly built `ModelRequest` comes in with its chosen tool name and its list of available tools. If no forced tool was requested, it leaves the request unchanged. If a forced tool was requested but no offered tool has that name, it raises an error; otherwise the validated request comes out unchanged.

**Call relations**: This is run automatically by Pydantic after a `ModelRequest` is created. It acts as a gatekeeper before any model client receives the request, so later provider-specific code can assume a forced tool choice is valid.


##### `ModelClient.complete`  (lines 209–209)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This defines the one method every model client must provide: send a model request and stream back events as the provider responds. It is a contract rather than an implementation.

**Data flow**: A `ModelRequest` goes in, containing the model name, system prompt, messages, tools, token budget, and reasoning settings. An asynchronous stream comes out, yielding pieces such as text, tool-call starts, tool-call JSON fragments, reasoning blocks, and usage information as they arrive.

**Call relations**: Provider-specific clients implement this method so the rest of the harness can call them through the same interface. The protocol lets orchestration code ask for a completion without caring whether the underlying provider is Anthropic, OpenAI, OpenRouter, or another service.


##### `trim_images`  (lines 217–248)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This reduces image-heavy message history so it fits the strictest provider image limits. It keeps the newest useful images and replaces removed images with a clear placeholder instead of silently deleting them.

**Data flow**: A tuple of messages goes in. The function finds every image, decides which ones fit per-message limits, per-request limits, and the total image byte budget, then builds new messages where dropped images become `[image omitted: over the provider image limit]`. If no images need dropping, the original messages come back unchanged.

**Call relations**: This is used before provider-specific translation so all clients start from a safe common message set. It calls `_image_positions` to locate images, `_image_data_len` to count image data against the byte budget, and `_trim_message` to create the final messages with replacement text.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `omit_images`  (lines 251–259)

```
def omit_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares messages for a model that only accepts text by replacing every image with an explanatory text marker. It preserves the conversation shape while making the request safe for text-only providers.

**Data flow**: A tuple of messages goes in. The function finds all image positions and, if any exist, returns new messages where each image is replaced with `[image omitted: model accepts text input only]`. If there are no images, it returns the original messages.

**Call relations**: This is the simpler companion to `trim_images`. It calls `_image_positions` to find images and `_trim_message` to perform the replacement, typically during request preparation for a text-only model client.

*Call graph*: calls 2 internal fn (_image_positions, _trim_message).


##### `_image_data_len`  (lines 262–273)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This measures the size of one image's encoded data so `trim_images` can enforce the total image byte limit. It works for both normal image blocks and images nested inside tool results.

**Data flow**: It receives the full messages plus a position that points to one image. It follows that position to the image block, reads the base64 image data string, and returns its length. If the position does not actually point to an image, it raises an error because the caller gave it an invalid address.

**Call relations**: `trim_images` calls this while walking backward through the images it hopes to keep. The returned lengths decide when the request-wide image byte budget has been used up.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 276–296)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This creates a map of where every image appears in the message history. It includes images placed directly in a message and images embedded inside a tool result.

**Data flow**: Messages go in, ordered from oldest to newest. The function skips plain text messages, scans structured content blocks, and returns a list of positions in oldest-first order. Each position records the message number, the block number, and, for nested tool-result images, the inner part number.

**Call relations**: Both `trim_images` and `omit_images` call this first because they need to know exactly which images exist before deciding what to remove. Its position list becomes the shared addressing system used by `_image_data_len` and `_trim_message`.

*Call graph*: called by 2 (omit_images, trim_images).


##### `_trim_message`  (lines 299–326)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]], replacement: str) -> Message
```

**Purpose**: This rewrites one message by replacing selected images with a text explanation. It leaves all other content in the message untouched.

**Data flow**: It receives one message, that message's index, a set of image positions to drop, and the replacement text. If the message is plain text, it returns it unchanged. Otherwise it walks through the content blocks, swaps matching image blocks for new `TextBlock` placeholders, updates nested tool-result content when needed, and returns a copied message with the revised content.

**Call relations**: `trim_images` and `omit_images` call this once per message after deciding which image positions should disappear. Inside, it creates replacement `TextBlock` objects and uses `Message.model_copy` to return updated Pydantic model objects without mutating the originals.

*Call graph*: called by 2 (omit_images, trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/harness/models/registry.py`

`domain_logic` · `startup and per model call`

This file is the central directory for AI model information. Without it, model choices, API keys, provider names, and prices could be checked in many different places, which would make mistakes show up late as confusing provider errors, broken rendering, or wrong billing.

The registry combines built-in model definitions with model definitions supplied by extensions called manifests. Each model must have a unique id, like a unique name on a contact list. If two models claim the same id, startup fails immediately. It also checks that configured default models really exist, so a typo in configuration is caught before any user turn begins.

Once built, ModelRegistry answers common questions. It can turn the special “auto” model choice into the configured real model. It can return a model’s specification, provider, key slot, and onboarding environment variable. Most importantly, it can build a ModelClient, which is the object that actually talks to the AI provider.

API keys are looked up only when a client is built. That matters because a workspace may bring its own key, and platform keys may rotate without restarting the service. For workspace-routed credentials, the file also wraps the client so that if a token is rejected before any streamed output is delivered, it can rebuild the client once and retry with refreshed credentials.

#### Function details

##### `_RebuiltOnRejection.complete`  (lines 48–61)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This runs a model request through an already-built client, but gives workspace credentials one safe chance to refresh if the provider rejects the token before any response has been shown. It exists so a long-running turn does not fail just because an access token expired right before use.

**Data flow**: It receives a ModelRequest and starts streaming ModelEvent items from the wrapped client. If events have already been delivered, any credential rejection is passed upward because replaying could duplicate visible output. If the credential is rejected before the first event, it asks the registry to build a fresh client for the same model, unwraps that fresh client if needed to avoid endless retry wrapping, and streams the retry’s events out to the caller.

**Call relations**: This wrapper is returned by ModelRegistry.client_for when a model call is tied to a workspace member credential. Later, whatever code uses the returned ModelClient calls complete as usual; the wrapper quietly adds the one-time rebuild behavior before handing events back.


##### `ModelRegistry.resolve`  (lines 75–78)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name AUTO_MODEL into the concrete default model configured for this deployment. If the caller already named a specific model, it leaves that name unchanged.

**Data flow**: It takes a model id string. If that string is the shared “auto” marker, it returns the registry’s configured auto_model value; otherwise it returns the original string.

**Call relations**: ModelRegistry.key_slot_for and ModelRegistry.model_key_env call this before answering questions where “auto” must mean the real model that will actually run.

*Call graph*: called by 2 (key_slot_for, model_key_env).


##### `ModelRegistry.spec`  (lines 80–86)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This retrieves the registered ModelSpec for an exact model id. It deliberately fails with a clear error if the id is unknown, so bad model names are caught at the registry boundary instead of later in unrelated code.

**Data flow**: It takes a model id and looks it up in the registry’s specs dictionary. On success it returns the matching ModelSpec; on failure it raises ValueError naming the missing id.

**Call relations**: ModelRegistry.client_for uses it before building a provider client. ModelRegistry.provider_for uses it to find the provider name. ModelRegistry.model_key_env uses it, after resolving “auto,” to decide which onboarding key should be required.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 88–116)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This builds the actual client object used to call the AI provider for a chosen model. It also finds the right API key at the moment of use, so workspace-owned keys and rotated platform keys are respected.

**Data flow**: It receives a model id, looks up that model’s spec, and checks whether the spec needs a key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the credential, reports a clear runtime error if none is set, rejects non-ASCII key values with CredentialValueInvalid, and then builds the provider client. If this is a workspace member-routed credential, it returns a _RebuiltOnRejection wrapper around the client; otherwise it returns the client directly.

**Call relations**: It starts by calling ModelRegistry.spec to get the model facts. It reads the active workspace through ws_current to resolve credentials and routing behavior. When member-routed credentials are involved, it hands the finished client to _RebuiltOnRejection so a pre-output credential rejection can trigger one rebuild.

*Call graph*: calls 1 internal fn (spec); 3 external calls (__init__, __init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 118–122)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This tells callers which provider, such as OpenAI or Anthropic, serves a model. That provider name can then be used for metering, reporting, or routing decisions.

**Data flow**: It takes a model id, asks spec for the matching ModelSpec, and returns the spec’s provider field. If the model id is unknown, the lookup fails loudly through spec.

**Call relations**: This is a small read path through the registry. It relies on ModelRegistry.spec so provider lookups share the same “unknown model” behavior as client creation and key checks.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 124–135)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This answers which workspace “bring your own key” slot would pay for a model’s calls, if any. It is intentionally forgiving: if the model is unknown or has no workspace key slot, it returns None instead of breaking billing-style reads.

**Data flow**: It receives a model id, first resolving AUTO_MODEL to the configured real model. It then looks directly in the specs table. If there is no spec or the spec has no key_slot, it returns None; otherwise it returns that key slot name.

**Call relations**: It calls ModelRegistry.resolve because stored agent settings may say “auto,” but billing or labeling needs the real model behind that choice. Unlike stricter paths that call spec, this function avoids raising for old or removed model ids so reporting can continue.

*Call graph*: calls 1 internal fn (resolve).


##### `ModelRegistry.model_key_env`  (lines 137–147)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding which environment variable should be set before using a model, when the provider is one of the built-in core providers. For extension-contributed providers, it returns None because the registry cannot know their key rules ahead of time.

**Data flow**: It receives a model id and the system Config. It resolves “auto” to the configured real model, looks up that model’s spec, checks the provider, and returns the configured Anthropic or OpenAI key environment variable name when applicable. For any other provider, it returns None.

**Call relations**: It calls ModelRegistry.resolve first so checks match the model that will actually run. It then calls ModelRegistry.spec to get the provider, using the same strict registry lookup as other model facts.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 150–183)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the ModelRegistry used by the rest of the system. It combines built-in models and extension-provided models, checks for conflicts and bad configured defaults, and prepares the merged pricing table.

**Data flow**: It receives the Config and a tuple of Manifest objects. It asks core_model_specs for the built-in model specs, then adds every model spec contributed by the manifests. As it builds the id-to-spec table, it raises ValueError if two specs use the same id. It then verifies that auto_model, ambient_reply_model, and background_jobs_model from config all name registered models. Finally it creates and returns a ModelRegistry containing the specs, pricing_from-derived pricing data, and the configured auto model id.

**Call relations**: This is the construction step for the registry, normally used during setup. It calls core_model_specs to get core definitions, pricing_from to build cost information from all specs, and then creates the ModelRegistry object that later serves client creation, provider lookup, key lookup, and pricing users.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### Model catalogs and providers
Built-in and external provider catalogs register concrete Anthropic, OpenAI, and Bedrock-backed models with pricing, credentials, and client construction details.

### `core/src/ufo/harness/models/catalog.py`

`config` · `startup and model/pricing lookup`

This file is like the project’s official price list and model menu. When the rest of the system needs to know whether a model exists, how much it costs, how large a conversation it can accept, or how to create the right provider client for it, this catalog supplies the answer.

The file defines constants for Anthropic and OpenAI key locations, context-window sizes, and reasoning support. A context window is the maximum amount of text a model can consider at once. Reasoning support means the model can use a provider feature for more deliberate step-by-step work, and in these specs it is also marked as compatible with tools.

Small helper functions build provider-specific clients and model descriptions. The Anthropic path checks whether the key is a normal API key or an OAuth credential. The OpenAI path checks whether the key represents a ChatGPT/Codex-style account and chooses the matching client.

The main function, `core_model_specs`, returns one `ModelSpec` per built-in model. Each spec combines the model id, provider, price, knowledge cutoff, context window, key source, and API surface. The note about GPT-5.6 is important: although those models may accept more text, this catalog uses the smaller window where the listed price is accurate, so the ledger does not undercharge large requests. At import time, the file also builds ready-to-use pricing tables and a digest for detecting the exact price set in use.

#### Function details

##### `_anthropic_client`  (lines 32–35)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates the Anthropic client object used to talk to an Anthropic model. It also marks whether the supplied credential is an OAuth credential, because that affects how the provider connection should behave.

**Data flow**: It receives a model description and a key string. It builds a low-level Anthropic SDK client from the key, checks what kind of credential the key is, then wraps both together with the model description in an `AnthropicClient`. The result is a ready-to-use client object for that model.

**Call relations**: This function is stored inside Anthropic `ModelSpec` objects by `_anthropic`. Later, when code needs to actually call an Anthropic model, the spec can use this function to create the provider client with the correct key and credential type.

*Call graph*: 3 external calls (__init__, anthropic_sdk_client, is_oauth_credential).


##### `_openai_client`  (lines 38–42)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates the OpenAI client object used to talk to an OpenAI model. It chooses between a normal OpenAI API client and a Codex-style client depending on what kind of account information is encoded in the key.

**Data flow**: It receives a model description and a key string. It first asks whether the key contains a ChatGPT account id. If not, it builds a normal OpenAI SDK client and wraps it in an `OpenAIClient`. If an account id is found, it builds a Codex SDK client for that account and marks the wrapper as using Codex. The output is the correct client wrapper for the given key.

**Call relations**: This function is attached to OpenAI `ModelSpec` objects by `_openai`. When the system later needs to send a request to an OpenAI-backed model, the spec can call this function so the right kind of OpenAI transport is used.

*Call graph*: 4 external calls (__init__, chatgpt_account_id, codex_sdk_client, openai_sdk_client).


##### `_anthropic`  (lines 45–65)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS_WITH_TOOLS) -> ModelSpec
```

**Purpose**: This helper builds one complete catalog entry for an Anthropic model. It keeps the repeated Anthropic-specific settings in one place so each model row only needs to provide the facts that differ, such as name, price, cutoff date, and context size.

**Data flow**: It takes a model id, price, knowledge cutoff, API-key environment variable name, and optional context-window and reasoning settings. It combines those with fixed Anthropic details: the provider name, Anthropic client factory, key slot, and chat API surface. It returns a `ModelSpec`, which is the project’s structured description of one model.

**Call relations**: `core_model_specs` calls this repeatedly while building the built-in Anthropic section of the catalog. The returned specs later become part of the shared model registry and pricing table used by the rest of the system.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 68–82)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds one complete catalog entry for an OpenAI model. It prevents every OpenAI row from repeating the same provider, key, context, reasoning, and client setup details.

**Data flow**: It takes a model id, price, knowledge cutoff, API-key environment variable name, and optionally which OpenAI API surface to use. It combines those inputs with the standard OpenAI context window, reasoning settings, key slot, and client factory. It returns a `ModelSpec` describing that OpenAI model.

**Call relations**: `core_model_specs` calls this for each built-in OpenAI model. Some GPT-5.6 entries pass the `responses` API surface so later request-building code uses the provider endpoint that accepts the needed combination of reasoning and tools.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 85–190)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds the full list of model specifications that core ships with. It is the single place where built-in Anthropic and OpenAI model facts are written down.

**Data flow**: It receives the names of the environment variables that should contain Anthropic and OpenAI keys. It creates many `ModelPrice` objects, then feeds those prices and other model facts into `_anthropic` and `_openai`. It returns a tuple of `ModelSpec` objects, one for each built-in model.

**Call relations**: This is the top-level builder for the file’s catalog. At module load time, it is called to create `CORE_MODEL_SPECS`; those specs are then turned into `CORE_PRICES`, `CORE_PRICING`, and `PRICE_DIGEST`, which other parts of the system can use for model lookup, cost accounting, and price-set identification.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `provider discovery and model client creation`

This file is like a catalog card for Amazon Bedrock Mantle models. Without it, the rest of the system would not know that these Bedrock models are available, what environment variable contains the API key, which AWS region to use, or whether a model should be called through an Anthropic-style or OpenAI-style API.

The file starts by naming the provider and defining the required credential: an Amazon Bedrock bearer token stored in the AWS_BEARER_TOKEN_BEDROCK environment variable. It also requires an AWS region, read from AWS_REGION or AWS_DEFAULT_REGION. That region matters because Bedrock endpoints are regional, like choosing the correct branch office before sending a package.

The main work is building ModelSpec objects. A ModelSpec is a compact description of one model: its ID, provider name, pricing, knowledge cutoff, context window, reasoning support, API style, and client-building function. Anthropic model IDs are connected to AnthropicClient through Bedrock Mantle’s Anthropic endpoint. OpenAI-compatible model IDs are connected to OpenAIClient, using either the chat completions route or the newer responses route depending on the model.

Finally, manifest() packages the provider name, version, credential slot, and all model specs into a Manifest. UFO can load that manifest to discover and use these Bedrock models.

#### Function details

##### `bedrock_region`  (lines 46–52)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock should use. This is needed because the Bedrock Mantle API address depends on the region.

**Data flow**: It reads AWS_REGION first, then AWS_DEFAULT_REGION from the process environment. If either value exists, it returns that region string. If neither is set, it stops with a clear error telling the user to set one of those environment variables.

**Call relations**: The Anthropic and OpenAI client builders call this before creating their network clients. It gives them the region they need to form the correct Bedrock Mantle endpoint.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 55–67)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Creates a UFO AnthropicClient for a Bedrock-hosted Anthropic model. Someone uses this indirectly when they select one of the Anthropic model IDs in this provider.

**Data flow**: It receives a model description and an API key. It looks up the AWS region, builds an Anthropic Bedrock Mantle async client with that key, region, timeout, and no automatic retries, then wraps it in UFO’s AnthropicClient together with the model spec.

**Call relations**: This function is stored inside Anthropic ModelSpec objects as their client factory. When the system later needs to call one of those models, the spec uses this function to build the actual client. It relies on bedrock_region to choose the right regional endpoint.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 70–77)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Creates a UFO OpenAIClient for a Bedrock-hosted model that speaks an OpenAI-compatible API. It chooses the right Bedrock Mantle URL shape for either chat completions or responses.

**Data flow**: It receives a model description and an API key. It reads the AWS region, builds a base URL using that region, chooses the responses endpoint when the spec asks for it and the chat endpoint otherwise, then creates an OpenAI SDK client and wraps it in UFO’s OpenAIClient.

**Call relations**: This function is stored inside OpenAI-compatible ModelSpec objects as their client factory. When one of those models is selected, the system calls this function to make the client that will send requests. It hands the actual low-level SDK setup to openai_sdk_client.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 80–99)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS) -> ModelSpec
```

**Purpose**: Builds a ModelSpec for one Anthropic model offered through Bedrock Mantle. It avoids repeating the same provider, credential, API style, and client setup details for every Anthropic model entry.

**Data flow**: It receives the model ID, pricing, knowledge cutoff, and optional context-window and reasoning settings. It combines those with shared Bedrock settings, such as the provider name, key slot, key environment variable, chat API style, and Anthropic client factory, and returns a complete ModelSpec.

**Call relations**: The BEDROCK_MODEL_SPECS list calls this repeatedly while the module is loaded. Each result becomes one advertised Anthropic model in the provider manifest.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 102–116)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Builds a ModelSpec for one OpenAI-compatible model offered through Bedrock Mantle. It keeps the repeated OpenAI-style model setup in one place.

**Data flow**: It receives the model ID, pricing, knowledge cutoff, context-window size, and API surface. It adds shared Bedrock settings, including the credential location, provider name, reasoning support, and OpenAI client factory, then returns a complete ModelSpec.

**Call relations**: The BEDROCK_MODEL_SPECS list calls this for each OpenAI-compatible Bedrock model. Those specs later tell UFO how to display the model and how to create a client for it.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 197–208)

```
def manifest() -> Manifest
```

**Purpose**: Returns the provider manifest that UFO uses to discover this extension. The manifest is the public summary of the provider: its name, version, required credential, and available models.

**Data flow**: It creates a credential slot describing the Bedrock API key requirement, then combines that with the provider name, version, and prepared model specs. The result is a Manifest object returned to the caller.

**Call relations**: The extension loading system calls this when it wants to learn what this provider offers. It hands back the complete catalog built earlier in the file so the rest of UFO can show and use these Bedrock models.

*Call graph*: 2 external calls (__init__, __init__).


### Search contract
The runtime search abstraction defines how the core system requests web search and page-fetching behavior from pluggable providers.

### `core/src/ufo/runtime/search.py`

`data_model` · `cross-cutting; used after startup during research/search tool calls`

This file is the boundary between the core runtime and any real web search service. The core project does not include a built-in search engine, and it does not keep provider API keys itself. Instead, an extension supplies a `SearchProvider`, which is like a plug-in adapter: the rest of the system can ask it to search the web or fetch a page, while the adapter hides the details of the actual service.

The file defines small, frozen data shapes for the information that moves across this boundary. A `SearchQuery` says what to search for, how many results to return, date limits, allowed domains, and an optional category such as academic or video. A `SearchResults` contains ranked `SearchHit` items and may also include a direct answer from the provider. A `FetchRequest` asks for one URL, optionally with a prompt for extraction or summarization, and a `FetchedPage` returns the page text plus an optional summary.

The important design choice is separation. Research tools can depend on this stable interface, while each search backend handles its own keys, network calls, and special features outside the sandbox. Without this file, the core code would either have to know every search provider directly or risk exposing private credentials to places that should not see them.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether the selected search provider can fetch and extract the contents of a specific web page. It matters because not every search service that can return search results can also read full pages.

**Data flow**: The caller reads this property from a provider instance. The provider returns a simple true-or-false answer. Nothing else is changed; the value is used to decide whether it is safe to call `fetch`.

**Call relations**: Before a research tool tries to fetch a URL, it checks this capability on the selected provider. If the provider says fetching is supported, the tool can continue to `SearchProvider.fetch`; otherwise, it must avoid that path or report that fetching is unavailable.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This asynchronous method asks the selected provider to run a web search. It is the standard doorway through which the rest of the system gets search results, regardless of which backend service is installed.

**Data flow**: A `SearchQuery` goes in, containing the search sentence and optional limits such as result count, dates, domains, or category. The provider turns that request into whatever its outside search service needs, waits for the network response, and returns `SearchResults` containing page hits and possibly a direct answer.

**Call relations**: Research tools call this through the turn's tool context, so they do not need to know the provider's name, API format, or key. The concrete backend implementation does the real work behind this protocol method and hands normalized results back to the core-facing tool code.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This asynchronous method asks the selected provider to retrieve and extract text from one web page. It is used when the system needs the contents of a specific URL, not just a list of search results.

**Data flow**: A `FetchRequest` goes in with a URL and optional instructions, such as an extraction prompt, a maximum text length, or a request to bypass cached data. The provider fetches or recrawls the page as appropriate, extracts usable text, and returns a `FetchedPage` with the URL, page text, and possibly a summary.

**Call relations**: This method is only supposed to be called after `SearchProvider.supports_fetch` says the provider can fetch pages. The research fetch tool uses that check first, then hands the request to the concrete provider implementation, which performs the outside network work and returns the normalized page result.


### Feature flags
Feature-flag entry points and the Cloudflare Flagship extension provide shared flag reads plus administrative variation updates.

### `core/src/ufo/flags.py`

`domain_logic` · `startup and feature checks during normal runtime`

This file keeps feature-flag use safe and consistent. Instead of letting many parts of the project talk directly to the OpenFeature software library, it gives the project one setup function and one reading function. That matters because flag providers can fail, be missing, answer slowly, or return values the code does not understand. Without this file, those problems could leak into normal program flow and break a user request or leave it waiting on a network call.

At startup, the deployment may provide a feature-flag backend. If it does, this file binds it to OpenFeature’s process-wide API. If not, the built-in no-op behavior remains, so every flag falls back to the default chosen by the caller.

When code asks whether a flag is enabled, the file builds an evaluation context using the current workspace ID. In plain terms, it tells the flag service, “Answer for this workspace.” It asks for a string value, because the supported backends serve flags as the strings "true" and "false", not as real boolean values. The call has a two-second timeout. If the provider raises an error, reports an error, takes too long, or returns anything other than those two exact strings, the function logs a warning and returns the caller’s default. This is called “failing closed”: when the system cannot know whether a feature is allowed, it withholds or preserves the default rather than risking unsafe behavior.

#### Function details

##### `init_flags`  (lines 37–42)

```
def init_flags(provider: FeatureProvider | None) -> None
```

**Purpose**: This function connects the chosen feature-flag provider to the OpenFeature library for the whole process. If no provider is supplied, it deliberately does nothing, leaving the safe default provider in place.

**Data flow**: It receives either a provider object or None. If the input is None, nothing changes and future flag reads will use OpenFeature’s no-op fallback behavior. If a provider is present, the function gives it to OpenFeature so later flag lookups go through that provider.

**Call relations**: This is used during application setup, after deployment configuration has selected a flag backend. Its only handoff is to OpenFeature’s provider-setting call, which makes that backend available to later calls to flag_enabled.

*Call graph*: 1 external calls (set_provider).


##### `flag_enabled`  (lines 45–75)

```
async def flag_enabled(flag: str, *, default: bool) -> bool
```

**Purpose**: This function answers the practical question, “Is this feature switched on for the current workspace?” It protects callers from slow, broken, missing, or confusing flag-provider responses by returning the caller’s default value in those cases.

**Data flow**: It takes a flag name and a default true-or-false value. It reads the current workspace ID, builds a request context from it, converts the default into the string form expected by the flag service, and asks OpenFeature for the flag’s string value with a two-second limit. If the lookup fails, reports an error, or returns anything other than "true" or "false", it writes a warning and returns the default. If the value is valid, it returns True for "true" and False for "false".

**Call relations**: Other code calls this whenever it needs to decide whether to offer a flagged feature. Inside, it asks the workspace runtime for the current workspace, uses OpenFeature’s client to evaluate the flag, uses asyncio’s timeout tool to avoid waiting too long, and sends warnings to the observability layer when the flag cannot be trusted.

*Call graph*: 5 external calls (timeout, get_client, EvaluationContext, warn, ws_current).


### `extensions/flagship/ufo_ext_flagship.py`

`orchestration` · `startup, flag reads, and CLI flag administration`

Feature flags let the product turn behavior on or off without shipping new code. This file makes Cloudflare Flagship the outside service that answers those flag questions, through OpenFeature, which is a common interface for feature-flag tools. The rest of the system can ask “is this flag on?” without knowing that Cloudflare is behind it.

At startup, the extension looks for deploy-level environment keys: the Flagship app ID, Cloudflare account ID, and a token that is allowed to evaluate flags. If any are missing, it does not install a Flagship provider. That is intentional: flags then fall back to their built-in code defaults instead of crashing the product.

For normal reads, the file creates a `FlagshipServerProvider`, with a short timeout and no retries. In plain terms, if Cloudflare is slow or unreachable, a caller waits only briefly and then gets the safe default.

The file also defines `FlagshipAdmin`, used by the `ufoctl flags set` command. This is for writes, not normal serving. It uses a separate write token and calls Cloudflare’s API to fetch a flag, change only its default variation to `on` or `off`, and write the full flag back. That careful “read, adjust one field, write back” pattern avoids accidentally deleting rollout rules or Terraform-owned fields.

#### Function details

##### `build`  (lines 52–73)

```
def build(cache_ttl_seconds: float) -> FeatureProvider | None
```

**Purpose**: Creates the Cloudflare Flagship provider that the rest of the app uses to read feature flags. If the deploy is not configured with the required Cloudflare keys, it returns nothing so the system uses code defaults instead.

**Data flow**: It reads three deploy environment values: the Flagship app ID, Cloudflare account ID, and read/evaluate token. If any value is missing, it logs a warning showing which pieces are present and returns `None`. If all are present, it builds a `FlagshipServerProvider` with the given cache lifetime, a short request timeout, and no retries, then returns that provider.

**Call relations**: This is handed to the system through the extension manifest as the builder for the `flagship` flag backend. During startup, core code can call it when it wants to bind OpenFeature to a real provider. It calls `deploy_env` to read configuration, `warn` to report missing setup, and Cloudflare’s `FlagshipServerProvider` when setup is complete.

*Call graph*: 3 external calls (FlagshipServerProvider, deploy_env, warn).


##### `FlagshipAdmin.serve`  (lines 95–103)

```
def serve(self, key: str, *, on: bool) -> None
```

**Purpose**: Changes one Flagship flag so it serves either the `on` variation or the `off` variation by default. It is used for operator-driven writes, such as a command-line flag change.

**Data flow**: It receives a flag key and a desired boolean state. First it asks Cloudflare for the current full flag record. It checks that the flag is readable and that the wanted variation, `on` or `off`, actually exists. Then it copies the existing flag data, removes fields that Cloudflare only returns for reading, changes `default_variation`, and sends the updated flag back. It returns nothing if the update succeeds, and raises an error if the flag cannot safely be changed.

**Call relations**: This is the high-level write action on `FlagshipAdmin`. It relies on `FlagshipAdmin._call` twice: once to fetch the current flag with `GET`, and once to send the updated flag with `PUT`. The split keeps the careful business rule here, while `_call` owns the HTTP details.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin._call`  (lines 105–120)

```
def _call(self, method: str, path: str, body: dict[str, object] | None=None) -> dict[str, object]
```

**Purpose**: Sends one authenticated request to Cloudflare’s Flagship API and turns Cloudflare failures into clear Python errors. It is the low-level HTTP helper for admin writes.

**Data flow**: It receives an HTTP method such as `GET` or `PUT`, an API path, and optionally a JSON body. It builds the full Cloudflare URL using the admin object’s account ID and app ID, adds the bearer token, sends the request, and reads the JSON response. If Cloudflare reports failure, either through an HTTP error code or a `success: false` body, it raises a `RuntimeError` with the returned error details. Otherwise it returns the decoded response body.

**Call relations**: This function is called by `FlagshipAdmin.serve` whenever the admin flow needs to talk to Cloudflare. It does not decide what a flag should become; it only performs the request and reports whether Cloudflare accepted it.

*Call graph*: called by 1 (serve).


##### `build_admin`  (lines 123–140)

```
def build_admin() -> FlagshipAdmin
```

**Purpose**: Creates the admin client used to change Flagship flag values. Unlike read setup, it fails loudly if write credentials are missing, because an operator explicitly asked to make a change.

**Data flow**: It reads the Cloudflare account ID, Flagship app ID, and write token from deploy environment values. It collects the names of any missing values. If anything is missing, it raises an error that names the missing keys. If all are present, it returns a `FlagshipAdmin` containing the IDs and write token.

**Call relations**: This is the factory for command-line write flows such as `ufoctl flags set`. It calls `deploy_env` to gather configuration and then constructs `FlagshipAdmin`, whose `serve` method performs the actual flag update.

*Call graph*: 2 external calls (__init__, deploy_env).


##### `manifest`  (lines 143–149)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, required deploy keys, and the flag provider it offers. This is how the project discovers that Cloudflare Flagship is available as a feature-flag backend.

**Data flow**: It takes no input. It creates a `Manifest` containing the extension name and version, lists the environment keys needed for read-time flag evaluation, and registers a `FlagProviderSpec` that says the `flagship` backend should be built by `build`. It returns that manifest to the extension loader.

**Call relations**: The extension system calls this when discovering or loading extensions. The manifest points later startup work toward `build`, which actually creates the OpenFeature provider if the deploy has the needed Cloudflare credentials.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-search-provider-catalog` — The common search and page-fetching service state used when the system needs outside web information.
- `reg-provider-rate-limit-budgets` — Shared per-provider throttle, retry, and backoff budget state for model, search, connector, and external API calls so workers avoid overrunning provider limits.
- `reg-turn-context-token-budget` — The active per-turn context-window and token/image budget accounting used to choose prompt contents, trigger compaction, constrain model rounds, and reconcile usage.
