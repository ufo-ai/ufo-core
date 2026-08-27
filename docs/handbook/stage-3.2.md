# Provider Backend Registration  `stage-3.2`

This stage is the system’s plug-in counter. During startup, it registers the outside services and interchangeable backends that the rest of UFO may use later. Instead of every feature knowing every provider’s details, these files put names, settings, and factory functions in central places.

The feature flag doorway in flags.py lets code ask “is this on?” while a deployment can plug in Cloudflare Flagship through the Flagship extension. The model registry keeps the master catalog of AI models, including prices and the code needed to create a provider client; the Bedrock extension adds Amazon Bedrock-backed models to that catalog. The OpenAI embedding extension adds a service that turns text into number lists, called vectors, for search and memory.

For running workspaces, sandbox/select.py chooses the sandbox carrier, such as local or remote. Redis Hub registers Redis as shared live transport so multiple servers can share terminal and frame updates. The sources registry maps connector names to connector classes. The Pipedream manifest adds Pipedream connectors, sign-in routes, and a broker for running actions safely without leaking user secrets.

## Files in this stage

### Core backend gateways
Core registries and selectors define the common entry points used to resolve feature flags, AI models, and sandbox carriers.

### `core/src/ufo/flags.py`

`util` · `startup and feature checks`

Feature flags are like light switches for software features. They let a team turn something on for one workspace, leave it off for another, or disable it safely if the flag service is unavailable. This file keeps that logic in one place so the rest of the project does not need to know which outside flag provider is being used.

At startup, `init_flags` can connect a chosen OpenFeature provider. OpenFeature is a standard library interface for feature flags, so the project can swap one backend service for another without changing every call site. If no provider is given, the built-in no-op provider stays in place, meaning flags simply fall back to the defaults written in code.

When code wants to know whether a feature is available, it calls `flag_enabled`. That function asks the flag system for a true-or-false answer, using the current workspace ID as the “targeting key” so a backend can enable a feature for one workspace at a time. It also protects the running system: if the flag provider errors, or takes longer than two seconds, the function logs a warning and returns the caller’s default value. In other words, a broken flag service should not crash a user request or leave it waiting forever.

#### Function details

##### `init_flags`  (lines 28–33)

```
def init_flags(provider: FeatureProvider | None) -> None
```

**Purpose**: Connects the deployment’s chosen feature-flag provider to the process-wide OpenFeature API. If no provider is supplied, it deliberately does nothing, leaving the safe default provider in place.

**Data flow**: It receives either a provider object or `None`. If it gets `None`, nothing changes and future flag checks will use OpenFeature’s default no-op behavior. If it gets a provider, it gives that provider to OpenFeature so later flag lookups go through the selected backend.

**Call relations**: This is meant to be called during boot, after deployment configuration has decided whether there is a real flag backend. Its only handoff is to `openfeature.api.set_provider`, which installs the provider used later by `flag_enabled`.

*Call graph*: 1 external calls (set_provider).


##### `flag_enabled`  (lines 36–50)

```
async def flag_enabled(flag: str, *, default: bool) -> bool
```

**Purpose**: Answers whether a named feature flag is on for the current workspace. It is designed to be safe: if the flag system cannot answer quickly or cleanly, it returns the default chosen by the caller.

**Data flow**: It takes a flag name and a default true-or-false value. It reads the current workspace, builds an evaluation context from that workspace ID, and asks the OpenFeature client for the flag’s boolean value. If the answer arrives within the timeout, that answer is returned. If anything fails, it writes a warning with the flag name, default, and error details, then returns the default.

**Call relations**: Application code calls this when deciding whether to offer a feature. Inside, it gets the current workspace from `ufo.workspace.ws_current`, creates an OpenFeature `EvaluationContext`, uses `openfeature.api.get_client` to ask the configured provider, and wraps the call in `asyncio.timeout` so it cannot hang. On failure, it reports the problem through `ufo.o11y.warn` and hands the caller a safe fallback.

*Call graph*: 5 external calls (timeout, get_client, EvaluationContext, warn, ws_current).


### `core/src/ufo/models/registry.py`

`domain_logic` · `startup and model-call request handling`

This file is the project’s model switchboard. Other parts of the system may ask for a model by name, or use the special name `auto`, meaning “use the deployment’s default model.” The registry turns those names into concrete model facts: which provider serves it, what key it needs, how to create a client for it, and how to price its usage.

At startup, `model_registry` combines the built-in model definitions with any model definitions contributed by extensions. It refuses to start if two models claim the same id, or if important configured models point to ids that do not exist. That is important because a typo in a model name should fail immediately, not halfway through a user request.

`ModelRegistry` is the object created from that startup work. Think of it like a well-labeled cabinet: each drawer is keyed by exact model id, and inside is the model’s instruction card. When a model call happens, `client_for` looks up the card, finds the right API key either from the current workspace’s bring-your-own-key storage or from environment configuration, checks that the key can be sent safely over the provider’s wire protocol, and then builds the provider client. Pricing and provider lookup also go through this same registry, so the rest of the system has one trusted place to ask model questions.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name `auto` into the real default model configured for this deployment. If the caller already gave a specific model id, it leaves it unchanged.

**Data flow**: It receives a model name. If that name is the `auto` placeholder, it replaces it with `self.auto_model`; otherwise it returns the original name. It does not change the registry.

**Call relations**: Other methods use this before asking questions where `auto` would be too vague. `key_slot_for` uses it to find the real key slot, and `model_key_env` uses it to check the key needed by the model that will actually run.

*Call graph*: called by 2 (key_slot_for, model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This fetches the full registered description for a model id. It deliberately raises a clear error if the id is unknown, so mistakes are caught at the registry instead of failing later in unrelated code.

**Data flow**: It receives a model id, looks in the registry’s `specs` table, and returns the matching `ModelSpec`. If there is no entry, it turns the missing lookup into a `ValueError` that names the unknown model.

**Call relations**: This is the main doorway to model facts. `client_for` uses it before building a provider client, `provider_for` uses it to report the backend provider, and `model_key_env` uses it after resolving `auto` to decide which environment variable should contain the key.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 44–69)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This creates the actual client object used to talk to the provider for a chosen model. It also finds the API key at the last responsible moment, so workspace-specific keys and rotated platform keys are respected.

**Data flow**: It receives a model id, looks up that model’s spec, then decides whether a key is needed. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the right credential, reports a clear runtime error if none is set, checks that the key only contains ASCII characters, and then returns the provider client built from the spec and key.

**Call relations**: When code is ready to make a model call, it comes here to get the usable client. This method first relies on `ModelRegistry.spec` for the model’s facts, then asks `ws_current` for the active workspace so it can load the correct credential. If the key contains characters the provider connection cannot carry, it raises `CredentialValueInvalid` instead of letting the provider fail later.

*Call graph*: calls 1 internal fn (spec); 2 external calls (__init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 71–75)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This answers which provider, such as OpenAI or Anthropic, serves a given model. That lets usage, metering, and reporting group model calls under the correct backend.

**Data flow**: It receives a model id, fetches the registered spec for that id, and returns the spec’s provider name. If the model id is not registered, the lookup fails clearly through `spec`.

**Call relations**: Code that needs to label a model call by provider uses this small lookup. It delegates to `ModelRegistry.spec`, keeping unknown-model errors consistent with the rest of the registry.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 77–88)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This tells which workspace bring-your-own-key slot would pay for a model’s calls, if any. It is intentionally forgiving: old or unknown model ids return `None` instead of crashing, which is useful for billing exports and historical records.

**Data flow**: It receives a model name, first turns `auto` into the configured real model, then looks for that spec directly in the registry table. If the spec is missing or has no key slot, it returns `None`; otherwise it returns the key slot name.

**Call relations**: This method is used when the system needs to label whether usage was paid by a workspace key or by the platform. It calls `ModelRegistry.resolve` first because stored agent settings may still say `auto`, and billing needs the key slot for the concrete model that actually ran.

*Call graph*: calls 1 internal fn (resolve).


##### `ModelRegistry.model_key_env`  (lines 90–100)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding which environment variable should be set before a model’s first use. It only does this eager check for core providers whose key environment variables are known by configuration.

**Data flow**: It receives a model name and the system configuration. It resolves `auto` to the real model, looks up that model’s provider, and returns the configured Anthropic or OpenAI key environment variable when appropriate. For contributed or unknown-to-core providers, it returns `None` because their key lookup is handled later by their own model spec.

**Call relations**: Startup or onboarding code can call this to warn users about missing keys before the first model turn. It uses `ModelRegistry.resolve` to avoid checking the placeholder `auto`, and `ModelRegistry.spec` to read the provider from the actual registered model.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 103–136)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the active `ModelRegistry` for the running deployment. It combines built-in models and extension-provided models, rejects duplicate ids, validates important configured model choices, and prepares pricing information.

**Data flow**: It receives the loaded configuration and a group of extension manifests. It asks for the built-in model specs, adds those and all manifest-provided specs into one dictionary keyed by model id, and raises clear errors for duplicate ids or configured model ids that do not exist. Finally, it builds a combined pricing table from the specs and returns a frozen `ModelRegistry` containing the specs, pricing, and configured automatic model.

**Call relations**: This is called during setup to create the single model registry used later by model calls, onboarding checks, pricing, and provider lookups. It hands off to `core_model_specs` for built-in definitions, to `pricing_from` to assemble pricing, and finally constructs the `ModelRegistry` object the rest of the system depends on.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### `core/src/ufo/sandbox/select.py`

`orchestration` · `startup/config load`

A sandbox can run on different backends, much like a package can be delivered by different couriers. This file is the place where the system reads the configured backend names, checks that they are valid, and builds the carrier objects that will be kept alive for the process.

It always starts with one built-in option: `local`, which runs sandboxes locally. Then it adds any carrier backends advertised by loaded extensions through their manifests. If two backends try to use the same name, the file stops immediately, because otherwise a name like `docker` could mean two different things.

The file also supports `resume_backends`: older backends that are no longer the default, but must stay available so existing saved sandbox handles can still be reopened. This lets a deployment move from one provider to another without abandoning old workspaces. The default backend and resume backends must be distinct, and resume names cannot repeat.

A key safety rule is enforced for remote carriers. If a carrier runs outside the local process or cluster, it must have a public HTTPS proxy URL configured. Without that, sandbox network traffic could bypass the system’s controlled egress path, where credentials, denial rules, and metering are applied.

#### Function details

##### `select_carriers`  (lines 26–50)

```
def select_carriers(config: Config, manifests: tuple[Manifest, ...]) -> DeployCarriers
```

**Purpose**: Builds the full set of sandbox carriers for this running deployment: the default carrier for new sandboxes, plus any extra carriers needed to resume older sandboxes. It also catches unsafe or ambiguous configuration early, before sandbox work begins.

**Data flow**: It receives the main configuration and the loaded extension manifests. It starts with the built-in `local` carrier, adds extension-provided carrier definitions, checks for duplicate names and invalid resume settings, then asks `_built` to create the actual carrier objects. It returns a `DeployCarriers` object containing the default carrier, its specification, and a name-to-carrier map for resume backends.

**Call relations**: This is the main selection routine in the file. `select_carrier` calls it when only the default backend is needed. During its work, it calls `_built` once for the default backend and once for each resume backend, so all configured carrier names are validated and constructed in one place.

*Call graph*: calls 1 internal fn (_built); called by 1 (select_carrier); 2 external calls (__init__, __init__).


##### `select_carrier`  (lines 53–56)

```
def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Returns only the default sandbox carrier, for callers that do not care about resume backends. It is a convenience wrapper around the fuller carrier selection process.

**Data flow**: It receives the same configuration and manifests as `select_carriers`. It calls `select_carriers`, then takes just the default carrier and its specification from the returned bundle. The result is a two-item pair: the carrier object and the `CarrierSpec` that describes where it came from.

**Call relations**: This function sits on top of `select_carriers`. It does not make separate decisions; it relies on the full selection path so the same validation rules are used even when a caller only asks for the default backend.

*Call graph*: calls 1 internal fn (select_carriers).


##### `_built`  (lines 59–79)

```
def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Turns one configured backend name into a live carrier object, while enforcing the safety checks needed for remote sandbox backends. It is the gatekeeper that makes sure a name refers to a real registered backend and that remote backends have a secure public proxy URL.

**Data flow**: It receives the known carrier specifications, the configuration, and one backend name. It looks up that name; if no carrier registered it, it raises an error. If the carrier is remote, it reads `proxy_public_url` from the sandbox configuration, checks that it exists, parses it as a URL, and requires it to be HTTPS with a hostname. If all checks pass, it calls the carrier factory and returns the new carrier together with its specification.

**Call relations**: `select_carriers` calls this helper whenever it needs to construct a default or resume backend. `_built` delegates URL parsing to `urllib.parse.urlparse` and raises `NotRegisteredError` when a configured backend name cannot be found, so bad configuration fails during startup instead of later during sandbox creation.

*Call graph*: called by 1 (select_carriers); 2 external calls (__init__, urlparse).


### Model and embedding providers
Provider extensions add concrete Bedrock model offerings and OpenAI-backed embeddings to the central backend system.

### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `startup and provider discovery; client creation when a Bedrock model is selected`

This file is like a menu and connection guide for using large language models through Amazon Bedrock Mantle. Without it, the rest of the system would not know that these Bedrock model IDs exist, what API style they use, where to send requests, or which environment variables must be set before a request can work.

The file defines provider-wide names, the needed API key slot, and the AWS region environment variables. It then defines helper functions that build model descriptions called ModelSpec objects. A ModelSpec is a compact record saying: this is the model ID, this is the provider, this is the price, this is the context window size, this is the knowledge cutoff, and this is the function to call when it is time to make a real network client.

There are two paths because Bedrock Mantle exposes two API shapes. Anthropic models use an Anthropic Bedrock Mantle client. OpenAI-compatible models use an OpenAI-style client, with a different base web address depending on whether the model uses the older chat-style API or the newer responses-style API.

At the end, manifest() packages the provider name, version, credential requirement, and all model specs into a Manifest. That manifest is what the larger system can load to discover this extension.

#### Function details

##### `bedrock_region`  (lines 46–52)

```
def bedrock_region() -> str
```

**Purpose**: This function finds the AWS region that Bedrock Mantle requests should use. A region is the Amazon data-center area, such as where the service endpoint lives, and requests cannot be formed correctly without it.

**Data flow**: It reads the process environment, first looking for AWS_REGION and then AWS_DEFAULT_REGION. If it finds one, it returns that text. If neither is set, it stops with an error that tells the user which environment variables to set.

**Call relations**: The Anthropic and OpenAI client builders call this before creating a network client. It acts as the shared checkpoint that makes sure both API styles are aimed at the correct Amazon region.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 55–67)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function builds a ready-to-use client for Anthropic-style models served through Bedrock Mantle. The rest of the system uses it when a ModelSpec says a chosen Bedrock model should speak the Anthropic API.

**Data flow**: It receives a model description and an API key. It asks bedrock_region for the AWS region, creates an Anthropic Bedrock Mantle client with the key, region, timeout, and retry settings, wraps that client in UFO's AnthropicClient wrapper, and returns the wrapper. The wrapper carries both the network client and the model specification.

**Call relations**: Model specs created by _anthropic store this function as their client factory. When the wider UFO system needs to call one of those Anthropic Bedrock models, it comes back to this function to build the concrete client.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 70–77)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function builds a ready-to-use client for OpenAI-compatible models served through Bedrock Mantle. It hides the details of Bedrock Mantle's OpenAI-style web address from the rest of the system.

**Data flow**: It receives a model description and an API key. It gets the AWS region, chooses the correct base URL depending on whether the model uses the responses API or the chat API, creates an OpenAI SDK client for that URL, wraps it in UFO's OpenAIClient wrapper, and returns it.

**Call relations**: Model specs created by _openai store this function as their client factory. When the selected model is one of the OpenAI-compatible Bedrock models, the wider system uses this function to point the OpenAI client at Bedrock Mantle rather than at OpenAI directly.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 80–99)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS) -> ModelSpec
```

**Purpose**: This helper creates a ModelSpec for an Anthropic model available through Bedrock Mantle. It keeps the repeated setup for Anthropic models in one place, so each model entry only needs to state what is unique about that model.

**Data flow**: It takes a model ID, pricing information, a knowledge cutoff date, and optional context-window and reasoning settings. It combines those with Bedrock's provider name, credential details, the Anthropic client builder, and the chat API surface, then returns a complete ModelSpec.

**Call relations**: The BEDROCK_MODEL_SPECS list calls this repeatedly while the file is loaded. Those resulting specs later become part of the manifest that the extension exposes to the rest of UFO.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 102–116)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: This helper creates a ModelSpec for an OpenAI-compatible model available through Bedrock Mantle. It standardizes the common Bedrock credential, provider, reasoning, and client-factory settings for these models.

**Data flow**: It takes a model ID, price, knowledge cutoff, context-window size, and API surface name. It combines those with Bedrock's provider name, the OpenAI client builder, and the required API key location, then returns a complete ModelSpec.

**Call relations**: The BEDROCK_MODEL_SPECS list calls this for each OpenAI-compatible Bedrock model. The finished specs are then included in manifest(), so the larger system can discover and select these models.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 197–208)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension's public description: its name, version, required credential, and available models. It is the main thing the host system calls to learn what this provider offers.

**Data flow**: It creates a credential slot describing the Bedrock API key the user must provide. It then creates and returns a Manifest containing the extension name, version, that credential requirement, and the full list of Bedrock model specs.

**Call relations**: The extension loader calls this during provider discovery. The returned Manifest hands the larger UFO system everything it needs to show, validate, and later use the Bedrock Mantle models defined in this file.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup and embedding jobs`

This file is the bridge between the project and OpenAI’s embedding API. An embedding is a long list of numbers that represents the meaning of a piece of text, a bit like giving each sentence a location on a map of ideas. Without this file, deployments that rely on the default embedding backend would have no built-in way to create those vectors.

The file does three main things. First, it defines safe limits for requests sent to OpenAI: each text item is clipped if it is too large, and many texts are packed into batches without going over item-count or character-count limits. This keeps requests from becoming too large and failing unexpectedly.

Second, it defines `OpenAIEmbedClient`, which reads the deploy API key only when an embedding call actually happens. That means a development server can start without an OpenAI key, but the system will clearly fail if someone tries to embed text without one.

Third, it exposes a `manifest`, which tells the host system: “I am the default embedding backend, here is how to build me, and I need an OpenAI API key at deploy time.”

#### Function details

##### `plan_embed_batches`  (lines 33–49)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for OpenAI embedding requests without letting any single request get too large. It clips oversized text items and groups the remaining text into batches that stay under the configured limits.

**Data flow**: It receives a tuple of text strings. For each string, it trims it to the maximum allowed length, then adds it to the current batch unless doing so would exceed the maximum number of items or characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send to OpenAI.

**Call relations**: When `OpenAIEmbedClient.embed` is ready to contact OpenAI, it calls this function first. The batches produced here become the actual chunks of text sent in separate embedding API requests.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 63–75)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This asynchronous function turns text strings into embedding vectors using OpenAI’s `text-embedding-3-large` model. It is the main workhorse that the rest of the system uses when it needs searchable numeric representations of text.

**Data flow**: It receives a tuple of text strings. It first reads the deploy API key from the environment, using the project’s credential helper, and raises a clear error if no key is available. It then creates an OpenAI async client, splits the input text into safe batches, sends each batch to OpenAI, sorts the returned rows back into the original order, converts the embedding values to floats, and returns all vectors as tuples.

**Call relations**: This method relies on `deploy_env` to find the OpenAI key, `openai.AsyncOpenAI` to talk to OpenAI’s service, and `plan_embed_batches` to keep each request within size limits. It is called by the embedding core when the system needs vectors for indexing or serving memory-related features.

*Call graph*: calls 1 internal fn (plan_embed_batches); 2 external calls (AsyncOpenAI, deploy_env).


##### `build`  (lines 78–83)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client that the host system will use for this backend. It deliberately does not require an API key at startup, so a local or development server can boot even before embedding is used.

**Data flow**: It receives an extension context, which represents the workspace or environment the extension is being built for. It does not read data from that context here. It simply creates and returns an `OpenAIEmbedClient` instance.

**Call relations**: The manifest points to this function as the factory for the default embedding backend. During system startup, the core extension machinery calls it to build the client, and later that client’s `embed` method does the actual OpenAI work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 86–92)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It declares the extension’s name and version, says which deploy key it needs, and registers this file’s OpenAI client as the default embedding backend.

**Data flow**: It takes no input. It builds an `EmbedBackendSpec` that links the backend name to the `build` function, then wraps that in a `Manifest` object along with metadata and required deploy keys. The returned manifest is what the host system reads to discover and load this extension.

**Call relations**: The extension loader calls this during discovery or startup. The manifest it returns tells the core system that the backend named `default` can be constructed by calling `build`.

*Call graph*: 2 external calls (__init__, __init__).


### Feature and live transport backends
Infrastructure extensions register Cloudflare Flagship for feature decisions and Redis Hub for shared live terminal transport.

### `extensions/flagship/ufo_ext_flagship.py`

`orchestration` · `startup`

Feature flags are switches that let a deployed product turn features on or off without changing code. This file connects those switches to Cloudflare Flagship through OpenFeature, which is a common interface for reading flags from different providers. Think of OpenFeature like a universal power adapter: the core app plugs into one shape, and this extension makes Cloudflare fit that shape.

At startup, the extension checks whether the deploy has the three required Cloudflare environment values: the Flagship app ID, the Cloudflare account ID, and an API token allowed to evaluate flags. If any are missing, it does not create a provider. That is intentional: the product then falls back to each flag’s built-in default instead of crashing or guessing.

If all keys are present, it builds a Flagship provider with a short request timeout, no retries, and a configurable cache lifetime. That means a flag lookup should either get an answer quickly, often from cache, or safely fall back through the wider flag system. This file does not create, edit, or delete flags. The actual flag definitions live in infrastructure files, so changing what an environment offers is treated like a normal code review change.

#### Function details

##### `build`  (lines 42–63)

```
def build(cache_ttl_seconds: float) -> FeatureProvider | None
```

**Purpose**: This function tries to create the Cloudflare Flagship feature-flag provider that the core system can use through OpenFeature. If the deploy is missing the needed Cloudflare settings, it returns nothing so the system can use each flag’s code default instead.

**Data flow**: It starts with a cache lifetime value from the caller. It then reads three deploy environment values: the Flagship app ID, the Cloudflare account ID, and the Flagship API token. If any value is absent, it records a warning showing which pieces were present and returns None. If all values are present, it passes them into Cloudflare’s FlagshipServerProvider, along with a one-second timeout, zero retries, and the requested cache lifetime, and returns that provider.

**Call relations**: This function is the bridge from this extension into the actual Cloudflare SDK. It calls deploy_env to read deploy-only settings, calls warn when the extension cannot be safely configured, and otherwise hands the gathered settings to FlagshipServerProvider so the rest of the app can read flags through the OpenFeature provider interface.

*Call graph*: 3 external calls (FlagshipServerProvider, deploy_env, warn).


##### `manifest`  (lines 66–72)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It tells the host the extension’s name and version, which deploy keys it needs, and how to build its feature-flag provider.

**Data flow**: It takes no input. It packages fixed constants from this file into a Manifest: the extension name, version, required environment keys, and a FlagProviderSpec that says the backend is called flagship and should be built by the build function. The result is a Manifest object the extension system can read.

**Call relations**: This is the registration point for the file. It creates a FlagProviderSpec so the host knows which builder to use for the flagship backend, then wraps that in a Manifest so the extension can be discovered and wired into the larger feature-flag setup.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup / extension registration`

This is the extension’s “menu card” for the rest of the system. The core application can ask extensions what backends they provide, and this file answers: it provides a hub backend named `redis` and a terminal transport named `redis`.

A hub is the part that spreads live frames or messages to the right places. Normally that may happen inside one running process. This extension swaps that for Redis Streams, a Redis feature for ordered message streams, so several server pods or processes can all take part. The terminal transport does a related job for connected user terminals: if a user’s connection is held by one pod, but work is admitted on another pod, Redis plus the shared blob store help route the terminal traffic back to the right machine.

Both pieces use the same setting, `hub.url`, which is the Redis connection address such as `redis://host:6379/0`. A key safety detail is that this file fails early and clearly if Redis was selected but no URL was configured. That is like checking for fuel before starting a trip, instead of discovering the empty tank on the highway.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed hub when the system has been configured to use `hub.backend = "redis"`. It also protects the system from starting with an incomplete Redis setup by requiring a Redis URL.

**Data flow**: It receives a possible Redis URL. If the URL is missing, it raises a clear error explaining that `hub.url` is required. If the URL is present, it passes that address into `RedisStreamHub` and returns the new hub object that the rest of the system can use.

**Call relations**: The manifest gives this function to `HubSpec` as the builder for the Redis hub option. When the core system chooses that backend, it calls this builder, which then hands off to `RedisStreamHub.__init__` to create the real Redis Streams hub.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This function creates the Redis-backed terminal transport when the system has been configured to use `terminal.backend = "redis"`. It makes sure the terminal routing layer has both a Redis address and access to the blob store used for larger shared data.

**Data flow**: It receives a possible Redis URL and a `BlobStore`, which is shared storage for data that should not live only in one process. If the URL is missing, it raises a clear error. If the URL is present, it gives the URL and blob store to `RedisTerminals` and returns the resulting terminal transport.

**Call relations**: The manifest gives this function to `TerminalTransportSpec` as the builder for the Redis terminal option. When the core system selects that terminal backend, this builder is called and it hands off to `RedisTerminals.__init__` to create the Redis-based terminal routing object.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension description that the core application reads at startup. It says the extension is named `redis_hub`, gives its version, and advertises the Redis hub and terminal transport choices.

**Data flow**: It takes no input. It builds a `Manifest` object containing one `HubSpec` for the `redis` hub backend and one `TerminalTransportSpec` for the `redis` terminal backend. The finished manifest is returned to the extension-loading system.

**Call relations**: This is the entry point the core extension loader uses to discover what this file provides. Inside, it creates `HubSpec`, `TerminalTransportSpec`, and `Manifest` objects, linking the backend names to the builder functions `_build_hub` and `_build_terminal` so they can be used later if selected.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### External connector brokers
Connector registries and manifests map external source backends and expose Pipedream as a brokered connector runtime.

### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup / import time`

This file solves a simple but important problem: when the system sees a source that says it uses a certain backend, it needs to know which connector class should be used to fetch data from that service. Instead of searching the codebase at startup to discover connectors automatically, this file lists them explicitly. That makes startup more predictable and makes adding a new provider a deliberate change: import the connector and add it to the registry list.

The main object created here is `CONNECTORS`, a dictionary. A dictionary is like a lookup table: given a connector name, it returns the connector class for that name. The file imports many provider connector classes, then passes them into `_connector_registry`, which builds the lookup table.

There is also a safety check. If two connectors claim the same `name`, the registry raises an error immediately. That matters because the name is used in several places: it identifies the backend for a source row, points to the right credential slot, and helps name source bindings elsewhere in the system. If two connectors shared a name, the system would not know which one was meant, like two shops using the same street address.

#### Function details

##### `_connector_registry`  (lines 61–69)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the connector lookup table from a fixed list of connector classes. It also protects the system from ambiguous names by rejecting duplicate connector names.

**Data flow**: It receives a tuple of connector classes. It reads each class’s `name` value, checks whether that name has already been used, and stores the class under that name in a dictionary. It returns the finished dictionary, unless it finds a duplicate name, in which case it raises an error instead of producing an unsafe registry.

**Call relations**: This function is called when the module is loaded to create `CONNECTORS`. The rest of the source-sync system can then use `CONNECTORS` as the shared lookup table for turning a backend name into the connector class that should be run.


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension load`

This file is the extension’s “front desk sign.” When the application loads extensions, it asks this file for a manifest, which is a structured description of what the extension adds to the system. Here, the extension adds a set of Pipedream connectors, such as services that need OAuth sign-in. OAuth is the common web flow where a user grants access without giving the app their password.

The important idea is that Pipedream keeps each user’s service token on its own servers. This project only receives enough information to ask Pipedream to perform actions, so private tokens do not have to live inside this deployment. That matters especially for providers with stricter access rules, such as Gmail, or for services where another connector provider is not available.

The file creates one shared `PipedreamBroker`, which is the component later used to run actions and server-side connector work. Then it loops through the known connector catalog and creates a `ConnectorProvider` for each entry. Each provider gets a user-facing label, a Pipedream OAuth provider for sign-in, the shared broker, and the allowed transfer hosts. Finally, it registers a browser bridge route. That route is used during the consent flow, when the user is redirected back after approving access.

#### Function details

##### `manifest`  (lines 24–46)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the main application uses to discover Pipedream connectors and their OAuth callback route. Someone would use this when loading the extension so the rest of the system knows what connector tools and web routes are available.

**Data flow**: It starts with the connector catalog and creates one shared Pipedream broker. For each catalog entry, it turns the stored connector details into a registered connector provider, including the sign-in provider, display label, broker, and allowed transfer hosts. It also adds a GET route for the OAuth bridge, then returns a completed manifest object containing the extension name, version, connectors, and route.

**Call relations**: During extension loading, the system calls this function to ask, “What do you provide?” The function creates the broker and provider objects, using the Pipedream connector catalog as its source of truth. It hands the finished manifest back to the host application, which can then put these connectors in the connect registry and expose the OAuth bridge route for browser-based sign-in.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).
