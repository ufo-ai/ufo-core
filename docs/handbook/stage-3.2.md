# Backend provider and external service registration  `stage-3.2`

This stage is shared startup plumbing. It teaches the system what outside services and plug-in providers are available before the main work begins. The model registry is the central catalog: given a model name, it knows the provider, required key, client setup, and price. The Bedrock extension adds Amazon Bedrock-hosted models to that catalog. The OpenAI embedding extension adds the default service for turning text into number vectors used for search, but waits to contact OpenAI until work is needed.

Several files register connector options. Composio and Pipedream manifests announce which services they can connect to and which OAuth sign-in routes they use. The Composio resolver is a flexible front desk that can recognize many Composio toolkits by name and send them through one shared broker. Keyed connectors cover simpler services that use API keys, with rules for storing and sending those keys safely.

Flagship connects feature flags to Cloudflare so behavior can be switched on or off. Redis Hub registers Redis as shared live communication support for frames and terminal traffic.

## Files in this stage

### Model and embedding providers
Registers the central model catalog, Bedrock-hosted model clients, and OpenAI-backed embedding generation.

### `core/src/ufo/models/registry.py`

`domain_logic` · `startup for building and validation; request handling whenever model facts or clients are needed`

This file solves a coordination problem. Many parts of the system need to know about models: routing needs to choose the right provider, billing needs prices, and runtime calls need credentials. If each part guessed on its own, a typo or missing model could show up later as a failed API call, a wrong bill, or a confusing crash. The registry makes model lookup a single front door.

The main class, `ModelRegistry`, is a frozen data holder, meaning its fields are not meant to change after creation. It contains a table of model specifications keyed by exact model id, a combined pricing table, and the configured default model used when something asks for `auto` instead of naming a real model.

The file also knows how to create a model client at the moment it is needed. That matters because credentials may come from the current workspace's bring-your-own-key storage, or from platform environment variables. In everyday terms, it checks the right key ring only when someone actually opens that model's door.

The top-level `model_registry` function builds the table from built-in model definitions plus extension manifests. It refuses duplicate ids, and it checks that important configured model names are real during startup. That turns configuration mistakes into early, clear failures instead of surprises halfway through a user request.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model value `auto` into the concrete default model configured for this deployment. If the caller already supplied a specific model id, it leaves it alone.

**Data flow**: It receives a model name. If that name is the shared `auto` marker, it replaces it with `self.auto_model`; otherwise it returns the original name unchanged. It does not change the registry.

**Call relations**: Other methods use this before asking questions where `auto` would be too vague. `key_slot_for` uses it to find the real model whose key slot matters, and `model_key_env` uses it to check which provider key onboarding should ask for.

*Call graph*: called by 2 (key_slot_for, model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This looks up the full stored description for a model id. It exists so every part of the system fails in the same clear way when someone names a model that is not registered.

**Data flow**: It receives a model id and checks the registry's `specs` table. If the id exists, it returns the matching `ModelSpec`, which contains facts such as provider, key requirements, price, and client factory. If the id is missing, it raises a `ValueError` explaining that no model is registered for that id.

**Call relations**: `client_for`, `provider_for`, and `model_key_env` all go through this lookup rather than reading the table directly. That makes this method the common checkpoint before model-specific work continues.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 44–69)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This creates the actual client object used to call a model provider, such as OpenAI or Anthropic. It also finds and validates the credential the provider needs, so a missing or unusable key becomes a clear error before the provider call is attempted.

**Data flow**: It receives a model id, looks up that model's specification, and checks whether the model needs a key. If no key is required, it builds the client with an empty key. If a key is required, it asks the current workspace for the credential, allowing either a workspace-specific bring-your-own-key value or a platform environment value. If no key is found, it raises a helpful runtime error. If the key contains non-ASCII characters, which provider network protocols may not carry safely, it raises `CredentialValueInvalid`. On success, it returns a new model client built from the spec and key.

**Call relations**: When some later part of the system is ready to make a model call, it asks this method for the correct client. This method first relies on `spec` to confirm the model is known, then calls `ws_current` to reach the active workspace's credential lookup, and finally hands the finished spec-plus-key pair to the model's client factory.

*Call graph*: calls 1 internal fn (spec); 2 external calls (__init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 71–75)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This returns the provider name for a model, such as the backend company or service that serves it. That is useful for routing, metrics, and billing labels.

**Data flow**: It receives a model id, looks up the model specification through `spec`, and returns the provider field from that specification. If the model id is unknown, the lookup raises the shared clear error.

**Call relations**: Code that needs to record or split work by provider calls this method instead of duplicating model lookup rules. It delegates the hard part, checking that the model exists, to `spec`.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 77–88)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This tells the caller which workspace bring-your-own-key slot would pay for a model, if any. It is intentionally forgiving: for unknown historical models or keyless models, it returns `None` rather than crashing.

**Data flow**: It receives a model name, first resolving `auto` to the concrete configured model. It then does a soft lookup in the registry table. If there is no spec, or the spec has no key slot, it returns `None`. Otherwise it returns the key slot name stored on the spec.

**Call relations**: This method uses `resolve` because stored agent settings may say `auto`, but billing and key labeling need the real model behind it. Unlike `spec`, it avoids loud failure so exports or old ledger rows can still be described even if a model has since been removed.

*Call graph*: calls 1 internal fn (resolve).


##### `ModelRegistry.model_key_env`  (lines 90–100)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding which environment variable should be set before a first run for a given model. It only answers for built-in providers whose key environment names are known in core configuration.

**Data flow**: It receives a model name and the application configuration. It resolves `auto` to the configured real model, looks up that model's spec, and reads its provider. For Anthropic it returns the configured Anthropic key environment variable name; for OpenAI it returns the configured OpenAI key environment variable name; for other contributed providers it returns `None` because core may not know how that provider obtains credentials.

**Call relations**: Onboarding or setup checks call this when they want to warn early about missing keys. It uses `resolve` so `auto` points to the actual first model, and `spec` so a bad model id still fails clearly.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 103–136)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the complete model registry for the running system. It combines built-in model definitions with model definitions contributed by extension manifests, checks for conflicts and bad configuration, and produces the ready-to-use `ModelRegistry`.

**Data flow**: It receives the loaded configuration and a tuple of extension manifests. It asks `core_model_specs` for the built-in models, then adds those plus every manifest-provided model into one dictionary keyed by model id. If two specs claim the same id, it raises a `ValueError`. It then checks that the configured default, ambient reply, and background job models all exist. Finally it builds a combined pricing table from every spec's price and returns a new `ModelRegistry` containing the specs, pricing, and configured auto model.

**Call relations**: This is the startup assembly point for model knowledge. It gathers built-in specs through `core_model_specs`, folds in manifest contributions, uses `pricing_from` to prepare billing data, and constructs the `ModelRegistry` that the rest of the system consults during later model selection and calls.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `startup / provider discovery`

This file is a provider plug-in for Amazon Bedrock Mantle. A provider plug-in is like a catalog page plus connection instructions: it lists the models the system can use, says how large their context windows are, records pricing and knowledge cutoff dates, and tells the rest of the system how to build the right client when someone actually calls a model.

Bedrock Mantle exposes two kinds of model APIs here. Anthropic model IDs, such as Claude models, are connected through the Anthropic Bedrock Mantle client. OpenAI-style model IDs, such as GPT models, are connected through an OpenAI-compatible client. The file does not translate prompts or responses itself. Instead, each `ModelSpec` points to the core client code that already knows how to speak the right API shape.

The file also defines the credential and region rules. It expects an API key in `AWS_BEARER_TOKEN_BEDROCK`, and it expects an AWS region from `AWS_REGION` or `AWS_DEFAULT_REGION`. If no region is set, it stops with a clear error, because Bedrock endpoints are regional.

At the bottom, `manifest()` packages everything into a `Manifest`, which is the object the larger UFO system reads to discover this extension’s name, required credential, and supported models.

#### Function details

##### `bedrock_region`  (lines 46–52)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock Mantle should use. Bedrock URLs depend on region, so the system cannot safely create a client without this value.

**Data flow**: It reads the process environment, first looking for `AWS_REGION` and then `AWS_DEFAULT_REGION`. If it finds one, it returns that region string. If both are missing, it raises an error telling the user which environment variable to set.

**Call relations**: When either Bedrock client builder needs to create a real network client, it calls `bedrock_region` first. `_anthropic_client` uses the result for the Anthropic Bedrock Mantle client, and `_openai_client` uses it to build the correct OpenAI-compatible Bedrock Mantle base URL.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 55–67)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds the client used to call Anthropic models through Amazon Bedrock Mantle. Someone would use this indirectly when a `ModelSpec` for an Anthropic model needs a working API connection.

**Data flow**: It receives a model specification and an API key. It looks up the AWS region, creates an Anthropic Bedrock Mantle async client with that key, region, no automatic retries, and a provider timeout, then wraps it in UFO’s `AnthropicClient` together with the model specification. The result is a ready-to-use Anthropic model client.

**Call relations**: Anthropic model specs created by `_anthropic` store this function as their client builder. Later, when the core system wants to use one of those models, it calls this builder. The builder asks `bedrock_region` for the region, hands the key and region to Anthropic’s Bedrock Mantle client, and then hands that lower-level client to UFO’s `AnthropicClient` wrapper.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 70–77)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds the client used to call OpenAI-compatible models through Amazon Bedrock Mantle. It chooses the correct Bedrock Mantle URL depending on whether the model uses the chat completions style API or the newer responses style API.

**Data flow**: It receives a model specification and an API key. It gets the AWS region, builds a base URL for Bedrock Mantle, creates an OpenAI SDK-style client pointed at that URL, and wraps it in UFO’s `OpenAIClient` with the model specification. The output is a ready-to-use OpenAI-compatible model client.

**Call relations**: OpenAI-compatible model specs created by `_openai` store this function as their client builder. When the larger system needs to call one of those models, this function is invoked. It relies on `bedrock_region` for the regional endpoint and delegates actual SDK client creation to `openai_sdk_client` before returning UFO’s `OpenAIClient` wrapper.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 80–99)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS) -> ModelSpec
```

**Purpose**: Creates a `ModelSpec` entry for one Anthropic model available through Bedrock. A `ModelSpec` is the catalog record that tells UFO how the model is named, priced, authenticated, and connected.

**Data flow**: It receives the Bedrock model ID, price information, knowledge cutoff date, and optional context-window and reasoning settings. It combines those with shared Bedrock constants such as provider name, credential slot, API key environment variable, and the `_anthropic_client` builder. It returns a complete `ModelSpec` for that Anthropic model.

**Call relations**: This helper is used while building `BEDROCK_MODEL_SPECS`, the file’s model catalog. It does not open a network connection itself. Instead, it records `_anthropic_client` inside each model spec so the real client can be created later, only when that model is used.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 102–116)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates a `ModelSpec` entry for one OpenAI-compatible model available through Bedrock. It keeps the repeated provider, credential, pricing, context, reasoning, and API-surface details consistent across these model entries.

**Data flow**: It receives the model ID, price, knowledge cutoff, context window size, and API surface name. It combines those with shared Bedrock constants and the `_openai_client` builder. It returns a complete `ModelSpec` that the rest of UFO can list and later use to create a client.

**Call relations**: This helper is used to populate `BEDROCK_MODEL_SPECS` with GPT-style Bedrock Mantle models. Like `_anthropic`, it only creates catalog data at import time. The actual network client is created later through `_openai_client` when the model is selected for use.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 197–208)

```
def manifest() -> Manifest
```

**Purpose**: Returns the extension manifest that tells UFO what this provider offers. The manifest includes the extension name and version, the credential the user must provide, and the complete list of Bedrock model specs.

**Data flow**: It creates a credential slot named `bedrock_api_key` with a human-readable description, then packages that credential requirement together with the provider name, version, and `BEDROCK_MODEL_SPECS`. The returned `Manifest` is the single object the host system reads to discover this extension.

**Call relations**: This is the public discovery point for the file. During provider discovery or startup, the larger system calls `manifest`; `manifest` hands back the model catalog built earlier by `_anthropic` and `_openai`, plus the credential requirement created with `CredentialSlot`.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and background embedding/indexing work`

This extension gives the project a ready-made embedding backend: it sends text to OpenAI’s `text-embedding-3-large` model and gets back vectors, which are long lists of numbers that capture meaning for search and indexing. Without this file, a deployment that expects the default embedding backend would not know how to create those vectors.

The file does three main things. First, it defines safety limits for embedding requests. OpenAI calls can fail or become too large if too much text is sent at once, so `plan_embed_batches` clips very long text items and groups them into batches that stay under size limits. Think of it like packing boxes for shipping: each box can only hold so many items and so much total weight.

Second, `OpenAIEmbedClient` performs the actual embedding call. It reads the deploy API key from the environment at the moment embedding is requested, not when the server starts. That means a local development server can boot without an OpenAI key, but if someone tries to embed without a key, it fails clearly.

Third, `manifest` advertises this extension to the host system. It says: “I provide the default embedding backend, and I need an OpenAI API key to do real work.”

#### Function details

##### `plan_embed_batches`  (lines 33–49)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for OpenAI embedding requests without exceeding request size limits. It shortens any single text that is too long, then groups texts into batches that are small enough to send safely.

**Data flow**: It receives a tuple of text strings. For each string, it keeps only the allowed maximum number of characters, then adds it to the current batch unless that batch would have too many items or too many total characters. It returns a tuple of batches, where each batch is a tuple of clipped text strings ready to send to OpenAI.

**Call relations**: When `OpenAIEmbedClient.embed` is about to call OpenAI, it first asks this function how to split the input. The returned batches control how many separate OpenAI requests are made and help prevent oversized payloads.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 63–75)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the main workhorse that turns text into embedding vectors using OpenAI. Someone uses it when the system needs numeric representations of text for indexing or search.

**Data flow**: It receives a tuple of text strings. It reads the OpenAI API key from the deployment environment, and if no key is available it raises a clear error. It creates an async OpenAI client, splits the text with `plan_embed_batches`, sends each batch to OpenAI, sorts the returned rows back into input order, converts the embedding values to plain floats, and returns all vectors as tuples.

**Call relations**: The wider embedding system calls this method through the `EmbedClient` interface when embeddings are needed. Inside the method, it relies on `deploy_env` to find the API key, `plan_embed_batches` to keep requests within limits, and `openai.AsyncOpenAI` to make the actual network calls to OpenAI.

*Call graph*: calls 1 internal fn (plan_embed_batches); 2 external calls (AsyncOpenAI, deploy_env).


##### `build`  (lines 78–83)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client object that the host system will use. It deliberately does not require an API key at construction time, so the service can start even before embedding is used.

**Data flow**: It receives an extension context from the host system, but this backend does not need to read anything from that context. It returns a new `OpenAIEmbedClient` instance with the default model settings.

**Call relations**: The extension manifest points to this function as the factory for the default embedding backend. During startup, the host system calls it to obtain a client, and later that client’s `embed` method performs the real OpenAI work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 86–92)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It tells the system the extension’s name and version, which deployment key it needs, and which embedding backend it provides.

**Data flow**: It takes no input. It builds and returns a `Manifest` object containing the extension metadata, the required OpenAI API key name, and an `EmbedBackendSpec` that connects the backend name `default` to the `build` function.

**Call relations**: The host system calls this during extension discovery or startup. The manifest is how the rest of the project learns that this file supplies the default embedding backend and should use `build` when that backend is requested.

*Call graph*: 2 external calls (__init__, __init__).


### Brokered connector providers
Declares broker-managed connector extensions and the dynamic Composio resolver used to route toolkit connections.

### `extensions/composio/ufo_ext_composio/manifest.py`

`config` · `startup / extension registration`

This file is the extension’s front desk. When the main system loads the Composio extension, it calls this file to ask: “What do you provide, and how should I reach it?” The answer is a `Manifest`, which is a package of registration information.

Composio is a service that can connect to many external tools, such as GitHub, and keep each user’s tokens on Composio’s servers. That matters because this deployment does not need to store those secrets itself. Most Composio tools can be found dynamically through a resolver, using just their Composio slug, which is like looking up a tool by its catalog name. A smaller set of explicitly listed connectors is also declared here, mainly for cases that need command-line credentials or a real provider host.

The file creates one shared `ComposioBroker`, which is the piece that later brokers access to server-side Composio accounts. It also creates a request forwarder for command-line credential use. For each known connector in `CONNECTORS`, it builds a `ConnectorProvider` with its OAuth sign-in description, user-facing label, broker, allowed transfer hosts, and optional CLI credential settings. Finally, it registers a browser route for the OAuth callback/bridge, so the user’s consent flow has a place to return to.

#### Function details

##### `manifest`  (lines 24–53)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Composio extension manifest, which is the object the host application reads to register this extension. It describes the available connectors, the dynamic connector resolver, and the web route used for OAuth sign-in.

**Data flow**: It starts with fixed extension constants, the `CONNECTORS` catalog, and Composio-specific helper classes. It creates a shared broker, creates a request forwarder, turns each connector specification into a `ConnectorProvider`, attaches an optional command-line credential when that connector has an environment variable configured, adds a resolver for dynamically discovered Composio tools, and adds the OAuth route. The result is a complete `Manifest` object returned to the host application.

**Call relations**: The host system calls this function when it loads the extension. Inside, it constructs the broker, OAuth provider objects, connector provider objects, resolver, route specification, and final manifest. Those constructed pieces are then used later by the wider system: the connector registry can list and resolve Composio connectors, the broker can support grants, the CLI credential can forward authorization when needed, and the registered route can receive the browser-based OAuth consent flow.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, items).


### `extensions/composio/ufo_ext_composio/resolver.py`

`domain_logic` · `connector discovery and connection setup`

Composio offers access to many outside services, called toolkits. Registering each one separately would be brittle and a lot of work, so this file creates an open resolver for them. A resolver is the part of the system that answers questions like: “Does this provider name belong here?”, “How should a user connect it?”, and “Where should tool calls go after connection?”

The central piece is `ComposioResolver`, a small frozen data class that keeps only one thing: a shared `ConnectorBroker`. The broker is the worker that later runs Composio-backed tools. The resolver itself stays mostly stateless, which matters because it asks for a fresh Composio client each time. That means tests or runtime configuration can swap the client behavior without stale connections hanging around.

When asked about a provider, the resolver first rejects names that are locally banned. For anything else, it asks Composio’s live catalog whether the toolkit is connectable. If it is, the resolver can build an OAuth description, create a connector entry with a readable label, and search Composio’s catalog for discoverable services. It also exposes Composio’s approved file-transfer hosts, so tool files can safely move through the sandbox while the actual service token remains with Composio.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property tells the rest of the system which Composio file-store hosts are allowed for file transfers. It matters because tools may read or write files, and the sandbox needs a clear allow-list of safe hosts.

**Data flow**: It takes no outside input beyond the resolver object. It reads the shared Composio transfer-host list and returns it unchanged as a tuple of host names. It does not change any state.

**Call relations**: When the connector system needs to know which external hosts are permitted for a Composio-backed connection, it asks this property. The answer is handed back directly to the surrounding sandbox or connector flow so file inputs and outputs can pass through approved Composio storage.


##### `ComposioResolver.claims`  (lines 35–38)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function decides whether a given provider name should be treated as a Composio toolkit. It prevents banned names from being accepted and checks Composio’s live catalog before saying yes.

**Data flow**: It receives a provider slug, such as a short service name. First it lowercases the name and compares it with the local banned list; if it is banned, the result is `False`. Otherwise it creates or retrieves a Composio client, asks whether that toolkit is connectable, and returns `True` only if Composio reports a matching connectable toolkit.

**Call relations**: The wider connector registry calls this when no explicitly registered connector has already claimed the provider. If the provider passes this check, later connection steps can ask this same resolver for an OAuth descriptor and connector entry; if it fails, the name is left for other resolvers or rejected.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 40–41)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the connection description used for OAuth, the standard flow where a user grants access to an outside service. For Composio, it creates a descriptor that points to the toolkit slug but leaves the provider host blank because Composio keeps and uses the account token on its own side.

**Data flow**: It receives a provider slug. It places that slug into a `ComposioOAuthProvider` object and sets the host to an empty string. The result is an OAuth provider description that the rest of the connection flow can use.

**Call relations**: After `claims` has confirmed that a slug belongs to Composio, the connect flow can call this to learn how to start the authorization process. It hands off to `ComposioOAuthProvider`, which packages the provider information in the shape expected by the connector system.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 43–46)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the connector entry that represents a Composio toolkit inside the system. It gives the provider a human-friendly label and attaches it to the shared Composio broker that will later run its tools.

**Data flow**: It receives a provider slug. It keeps the original slug as the provider id, turns underscores into spaces and title-cases the result for display, and combines that with the resolver’s broker in a new `ConnectorEntry`. The returned entry is ready for the connector registry or UI to use.

**Call relations**: Once a provider has been accepted as a Composio toolkit, the registry or connection flow calls this to create the concrete connector record. That record points future tool execution toward the one shared `ConnectorBroker`, rather than creating a separate broker for every possible toolkit.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 48–51)

```
async def catalog(self, query: str, limit: int=TOOLKIT_SEARCH_LIMIT, after: str | None=None) -> CatalogPage
```

**Purpose**: This function searches Composio’s toolkit catalog so users or discovery tools can find services they may connect. It keeps the system from advertising random names by relying on Composio’s own list of connectable toolkits.

**Data flow**: It receives a search query, a maximum number of results, and optionally an `after` marker used to fetch the next page of results. It gets a Composio client, asks that client to list matching toolkits, and returns the resulting catalog page. It does not store the results itself.

**Call relations**: Discovery features call this when they need to show or search available Composio-backed services. The function delegates the actual lookup to the Composio client, then passes the catalog page back to the caller so the caller can display results or continue paging.

*Call graph*: 1 external calls (composio_client).


### `extensions/pipedream/ufo_ext_pipedream/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s “front desk sign.” When the main application loads extensions, it asks each one for a manifest, which is a plain declaration of what the extension provides. Here, the Pipedream extension declares a set of connectors, such as services whose accounts are authorized through Pipedream rather than directly through this app.

The file creates one shared PipedreamBroker. A broker is the server-side helper that knows how to use connected accounts without handing secret tokens to the agent or browser. For every connector listed in CONNECTORS, the manifest builds a ConnectorProvider. Each provider includes three important things: an OAuth provider, which describes how the user grants access; a human-friendly label; and the shared broker that later performs actions and credential-backed work.

It also registers one browser-facing route for the OAuth bridge. OAuth is the common “let this app access my account” consent process. This route is where the consent flow redirects so the app can finish connecting the account to the correct workspace.

In short, this file does not perform Pipedream actions itself. It declares the menu of available Pipedream-backed connections and the route needed to finish login, so the rest of the system can discover and use them safely.

#### Function details

##### `manifest`  (lines 25–47)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Pipedream extension’s manifest, which is the object the host application reads to discover this extension’s connectors and routes. Someone would use it during extension loading so the app can add Pipedream-backed services to its connection registry.

**Data flow**: It starts with the connector catalog from CONNECTORS and creates one shared PipedreamBroker. For each catalog entry, it turns the provider name and connector details into a ConnectorProvider with an OAuth setup, display label, broker, and allowed transfer hosts. It then adds a route for the OAuth bridge and returns one Manifest object containing the extension name, version, connector list, and route list.

**Call relations**: When the host system loads this extension, it calls manifest to ask, “What do you provide?” The function creates PipedreamOAuthProvider objects for each connector, wraps them in ConnectorProvider entries, creates a RouteSpec for the OAuth redirect path, and hands everything to Manifest so the wider connector and routing systems can register them.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, items).


### Direct-key connector providers
Registers services that authenticate through user-supplied API keys and outbound-proxy-safe credential handling.

### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / extension manifest load`

Some outside services cannot be connected through a broker-style “click to authorize” flow. Instead, the workspace already owns an API key, like a special password for that service. This file describes those services in a controlled way so the agent can call them without ever seeing the real key.

The main idea is a table of supported providers, such as Datadog, PostHog, Mercury, Apollo, and PandaDoc. Each row says which web host may be contacted, which HTTP header carries the key, what environment variable the sandbox should use, and what text to show when asking a workspace admin for the credential. For providers whose API host depends on region or account, the file lists the allowed hosts and asks the member to choose one. That prevents a key meant for one region from being sent somewhere else.

The safety trick is a “sentinel”: the sandbox receives a harmless placeholder value in an environment variable. When the sandbox makes an outgoing request, the egress proxy replaces that placeholder with the real secret only for the approved host and header. Like handing someone a claim ticket instead of the actual valuables, the agent can use the credential path without being able to read or copy the secret.

At the end, the file builds a manifest: the package of credential slots plus a prompt section explaining how agents should use these keyed providers.

#### Function details

##### `KeyedSecret.__post_init__`  (lines 56–61)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a declared API key uses only an authentication prefix the outbound proxy knows how to safely replace. It protects the provider table from accidentally describing a kind of header value the system cannot swap correctly.

**Data flow**: After a KeyedSecret object is created, it reads its own scheme field. If there is no scheme, it accepts the secret as a plain header value. If there is a scheme, it compares it with the small approved set, and raises an error if the scheme is not supported. Nothing new is returned; the object is either accepted or rejected.

**Call relations**: This runs automatically whenever the provider table creates a KeyedSecret. Later, KeyedProvider.slots relies on these already-checked secrets when it creates credential slots and injection rules, so bad schemes are caught early instead of becoming unsafe proxy behavior.


##### `KeyedProvider.__post_init__`  (lines 79–88)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider describes its API address in exactly one safe way: either one fixed host, or a closed list of selectable hosts. It also makes sure region-based providers include the extra information needed to ask the user for the right host and pass that choice into the sandbox.

**Data flow**: After a KeyedProvider object is created, it reads its host, sites, host_env, and site_description fields. If both a fixed host and selectable sites are present, or neither is present, it raises an error. If selectable sites are used but the environment variable or user-facing description is missing, it also raises an error. Otherwise the provider declaration is left unchanged and considered valid.

**Call relations**: This runs as the KEYED_PROVIDERS table is built. KeyedProvider.target_host and KeyedProvider.slots depend on the provider having a clear host shape, so this validation keeps the later manifest-building code simple and safe.


##### `KeyedProvider.target_host`  (lines 91–100)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This turns a provider’s host declaration into the exact form the rest of the manifest needs. For a simple provider it returns the fixed hostname; for a region-based provider it returns a HostChoice, meaning a user must choose from approved hostnames.

**Data flow**: It reads the provider’s sites, host, provider name, site description, and host environment variable. If there are no selectable sites, it outputs the fixed host string. If there are selectable sites, it creates and returns a HostChoice containing the credential slot name, description, allowed hosts, default host, and environment variable name.

**Call relations**: KeyedProvider.slots calls this when building credential slots, because each secret must be tied to the right approved host. KeyedProvider.usage also calls it when writing human-readable instructions, so the examples show either a fixed host or an environment variable for the selected host.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 102–120)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This converts one provider declaration into the credential slots the system exposes to users. Each slot tells the system what secret to ask for and exactly where that secret may be injected into an outgoing request.

**Data flow**: It starts with a KeyedProvider and reads its secrets plus its target host. For every secret, it creates a CredentialSlot with a name, a user-facing description, and an InjectionTarget saying the allowed host, HTTP header, sentinel placeholder, sandbox environment variable, and request-counting dimension. If the host is selectable, it also adds a separate credential slot for the host choice. The output is a tuple of all slots for that provider.

**Call relations**: The top-level manifest function calls this for every provider in KEYED_PROVIDERS and gathers the results into the extension manifest. It hands off to CredentialSlot and InjectionTarget objects from the SDK, which are the shared format the rest of the system understands.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 122–135)

```
def usage(self) -> str
```

**Purpose**: This writes a short instruction line explaining how to call one provider from the sandbox. It is meant for the prompt text shown to the agent, so the agent knows which slots exist and how to place the environment variables into a REST API call.

**Data flow**: It reads the provider name, label, secrets, schemes, headers, environment variable names, and host information. It builds a curl-style example using the right headers and either the fixed host or the selected host environment variable. It returns one formatted string naming the slots and showing the request pattern.

**Call relations**: The module uses this while building SECTION_BODY, the prompt section included in the manifest. It depends on KeyedProvider.target_host so its instructions match the same host rules used by KeyedProvider.slots.


##### `manifest`  (lines 268–274)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s public assembly point. It packages the keyed-provider credential slots and the explanatory prompt text into a Manifest object that the larger system can load.

**Data flow**: It reads the extension name, version, provider table, and prepared prompt body. It asks each provider for its credential slots, flattens them into one tuple, creates a PromptSection with the keyed-connector guidance, and returns a Manifest containing all of that. It does not mutate the provider table or store secrets.

**Call relations**: The extension loader calls this when it wants to discover what this extension contributes. Inside, it calls KeyedProvider.slots for the credential declarations and constructs SDK Manifest and PromptSection objects so the rest of the platform receives the data in its standard extension format.

*Call graph*: 2 external calls (__init__, __init__).


### Feature flag backend
Connects feature-flag reads and operator writes to the Cloudflare Flagship service.

### `extensions/flagship/ufo_ext_flagship.py`

`io_transport` · `startup for flag reads; CLI flag-write commands for admin changes`

Feature flags let the product change behavior without shipping new code. This file makes Cloudflare Flagship the place where those choices live. At startup, the main system asks this extension for an OpenFeature provider. OpenFeature is a common interface for reading flags, so the rest of the code can ask “is this flag on?” without knowing it is backed by Cloudflare.

The file expects deploy-level environment values: the Flagship app id, the Cloudflare account id, and a token that is allowed to evaluate flags. If any of these are missing, it does not crash the product. Instead, it logs a warning and returns no provider, so flags fall back to their code defaults. That means a missing or unreachable flag service makes features behave as if they are off or at their safe default.

There is a separate `FlagshipAdmin` path for writes, used by `ufoctl flags set`. This uses a different token because changing flags is more powerful than reading them. To change one flag, it first reads the full current flag record from Cloudflare, changes only the default served variation, and sends the whole record back. This is like editing one line on a form while carefully copying the rest unchanged, so Terraform-owned settings are not accidentally erased.

#### Function details

##### `build`  (lines 52–73)

```
def build(cache_ttl_seconds: float) -> FeatureProvider | None
```

**Purpose**: Creates the Cloudflare Flagship provider that the rest of the system uses to read feature flags. If the deploy is missing the needed Cloudflare settings, it returns nothing so the system uses each flag’s built-in default instead.

**Data flow**: It reads the Flagship app id, account id, and read token from deploy environment variables. If any are absent, it writes a warning that says which pieces are missing and returns `None`. If all are present, it builds a `FlagshipServerProvider` with the app details, a short timeout, no retries, and the requested cache lifetime, then returns that provider.

**Call relations**: This function is registered in the extension manifest as the builder for the `flagship` flag backend. During startup, core flag setup calls it through that registration; it then hands back either a ready OpenFeature provider or `None` to signal safe fallback behavior.

*Call graph*: 3 external calls (FlagshipServerProvider, deploy_env, warn).


##### `FlagshipAdmin.serve`  (lines 95–103)

```
def serve(self, key: str, *, on: bool) -> None
```

**Purpose**: Changes which variation of one Flagship flag is served by default, choosing either the configured “on” or “off” variation. It is meant for operator-driven writes, not for normal per-request flag checks.

**Data flow**: It receives a flag key and a desired boolean state. First it asks Cloudflare for the current full flag record. It checks that the record is readable and that the requested variation, either `on` or `off`, really exists. Then it copies the current record while leaving out read-only answer fields, swaps in the new `default_variation`, and sends the updated flag back to Cloudflare. It returns nothing if the write succeeds, and raises an error if the flag cannot be read or changed safely.

**Call relations**: This method is the public action on `FlagshipAdmin`. A command such as `ufoctl flags set` would call it after `build_admin` creates the admin client. It relies on `FlagshipAdmin._call` for both the read request and the write request, so all Cloudflare communication and error parsing stays in one place.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin._call`  (lines 105–120)

```
def _call(self, method: str, path: str, body: dict[str, object] | None=None) -> dict[str, object]
```

**Purpose**: Sends one HTTP request to the Cloudflare Flagship flags API and turns Cloudflare’s response into either a usable dictionary or a clear runtime error. It centralizes the low-level web request details for admin writes.

**Data flow**: It takes an HTTP method such as `GET` or `PUT`, a flag API path, and an optional JSON body. It builds the full Cloudflare URL from the stored account id and app id, adds the bearer token for authorization, and sends the request through the stored `httpx` client. It parses the JSON response when present. If Cloudflare reports failure through the HTTP status or response body, it raises an error containing the method, path, status code, and Cloudflare’s error details. Otherwise it returns the parsed response dictionary.

**Call relations**: It is called by `FlagshipAdmin.serve` whenever that method needs to read or update a flag. Because `serve` calls this helper twice, `_call` is the shared doorway to Cloudflare for the admin path.

*Call graph*: called by 1 (serve).


##### `build_admin`  (lines 123–140)

```
def build_admin() -> FlagshipAdmin
```

**Purpose**: Creates the write-capable Flagship admin client used by flag-setting commands. Unlike read setup, it fails loudly if required write credentials are missing, because an operator asked for a change and needs to know why it cannot happen.

**Data flow**: It reads the Cloudflare account id, Flagship app id, and write token from deploy environment variables. It collects the names of any missing values. If anything is missing, it raises an error naming the missing settings. If everything is present, it returns a `FlagshipAdmin` instance loaded with those credentials.

**Call relations**: This function is the setup step for the flag-writing command path, such as `ufoctl flags set`. After it returns a `FlagshipAdmin`, that caller can invoke `FlagshipAdmin.serve` to actually change which variation a flag serves.

*Call graph*: 2 external calls (__init__, deploy_env).


##### `manifest`  (lines 143–149)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, required deploy environment keys, and the feature-flag provider it offers. This is how the wider project discovers and plugs in the Flagship backend.

**Data flow**: It uses the constants in this file to create a `Manifest`. The manifest lists the deploy keys needed for reading flags and registers a `FlagProviderSpec` that says the backend is named `flagship` and should be built with `build`. The finished manifest is returned to the extension loader.

**Call relations**: The extension system calls this during discovery or startup. The returned manifest points the core feature-flag setup toward `build`, which then creates the actual OpenFeature provider if the deploy has the needed Cloudflare credentials.

*Call graph*: 2 external calls (__init__, __init__).


### Shared Redis transports
Registers Redis as the shared backend for live-frame hub traffic and terminal transport.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup/config load`

This is the extension’s registration card. The main system does not automatically know that a Redis-backed hub or Redis-backed terminal transport exists, so this file describes them in a standard shape called a manifest. A manifest is like a menu entry: it gives the extension a name and version, then says, “if the user asks for backend redis, build this object.”

The problem it solves is coordination across more than one running server instance. The normal in-process hub only works inside one process. By selecting the Redis hub backend, live frames can be shared through Redis Streams, so different server instances can participate. The terminal transport does a related job for connected user terminals: if a turn is accepted by a server that does not directly hold the user’s terminal connection, Redis and the blob store help route the terminal data to the right place.

Both builders require `hub.url`, the Redis connection address. They deliberately fail immediately if that URL is missing. This is important because a bad deployment should break clearly at startup or configuration time, not later during a user request when the system first tries to send a frame or reach a terminal.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: Builds the Redis-backed live-frame hub when the system has been configured to use `hub.backend = "redis"`. It also checks that the Redis URL was actually provided, so the system does not start with a half-configured backend.

**Data flow**: It receives a Redis URL, or `None` if no URL was configured. If the URL is missing, it raises a clear error explaining that `hub.url` is required. If the URL is present, it creates and returns a `RedisStreamHub`, which is the object that will use Redis Streams to share hub messages across server instances.

**Call relations**: The manifest points the core system to this builder through a hub specification. When the core sees that the selected hub backend is `redis`, it calls this function, and this function hands off the actual hub work to `RedisStreamHub`.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: Builds the Redis-backed terminal transport when the system has been configured to use `terminal.backend = "redis"`. This lets terminal traffic reach the right connected user even when different server instances are involved.

**Data flow**: It receives a Redis URL and a blob store, which is shared storage for larger pieces of terminal-related data. If the URL is missing, it raises a clear configuration error. If the URL is present, it creates and returns `RedisTerminals`, giving it both the Redis address and the blob store it needs to coordinate terminal delivery.

**Call relations**: The manifest registers this function as the builder for the Redis terminal transport. When the core selects the `redis` terminal backend, it calls this function, which then delegates the real transport behavior to `RedisTerminals`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest that the main system reads to discover what this Redis extension provides. It declares the extension name, version, hub backend, and terminal transport backend.

**Data flow**: It takes no input. It packages the constants from this file together with two builder functions: one for the Redis hub and one for the Redis terminal transport. It returns a `Manifest` object that the host application can inspect during extension loading.

**Call relations**: This is the public entry point for the extension metadata. The extension loader calls it to learn that the backend name `redis` is available, and the returned hub and terminal specifications tell the core system which builder functions to call later when those backends are selected.

*Call graph*: 3 external calls (__init__, __init__, __init__).
