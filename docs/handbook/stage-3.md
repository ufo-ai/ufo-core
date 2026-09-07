# Configuration, packs, flags, and model catalog loading  `stage-3`

This stage happens during startup, before the system begins serving users. Its job is to decide what “bundle” of abilities to run, connect feature switches, and build the menu of AI models the rest of UFO may call. The main configuration file, checked by core/src/ufo/config.py, supplies safe deployment settings. The pack files are like preset toolboxes: assistant_dev, assistant_hosted, assistant_billing, assistant_eval, DSQA, GDPVal, and sample packs each name the extensions, skills, and test or hosted features that should be loaded together.

Once a pack is chosen, the model registry is filled. catalog.py lists built-in models, spec.py defines what every model record must say, and registry.py checks and stores the final list. Provider plugins such as Bedrock add extra model choices and connection rules.

Feature flags are runtime on/off switches. core flags.py gives the app one simple way to ask about them. flags_open answers “on” for local testing, while flagship connects to Cloudflare’s real flag service. proxy_serve.py supplies shared startup rules for provider access and owner database connections.

## Files in this stage

### Model catalog and registry
Built-in and plugin model declarations feed the central registry, with shared model specifications defining the facts every provider entry must expose.

### `core/src/ufo/harness/models/catalog.py`

`config` · `startup and model/pricing lookup`

This file is like the price list and product shelf for the system’s built-in language models. Without it, the rest of the system would not have one trusted place to ask: “What models are available, what do they cost, how much text can they take, and which service should I call?”

It defines shared constants for Anthropic and OpenAI, such as environment variable names for API keys and default context windows. A context window is the maximum amount of text a model can consider at once. It also defines default reasoning support, meaning whether a model can use extra step-by-step thinking features and whether tools can be used at the same time.

The small helper functions build `ModelSpec` objects. A `ModelSpec` is a structured description of one model: its name, provider, price, API style, key location, and client factory. The client factory is the recipe used later to create a live connection to Anthropic or OpenAI.

The main function, `core_model_specs`, returns the full tuple of built-in model specs. At the bottom, the file creates global catalog values from that tuple, including a model-to-price table, a pricing object, and a digest that can identify the exact pricing set in use.

#### Function details

##### `_anthropic_client`  (lines 32–35)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Creates a usable Anthropic model client from a model description and an Anthropic credential. It also detects whether the credential is an OAuth-style credential, so the client can be marked correctly.

**Data flow**: It receives a `ModelSpec` and a key string. It turns the key into an Anthropic SDK client, checks what kind of credential the key is, and wraps both of those together with the model spec in an `AnthropicClient`. The result is a ready-to-use client object for that Anthropic model.

**Call relations**: This function is used as the client-making recipe stored inside Anthropic `ModelSpec` entries created by `_anthropic`. When some later part of the system needs to actually talk to Anthropic, this recipe calls the Anthropic SDK builder and the credential checker, then hands back the wrapped client.

*Call graph*: 3 external calls (__init__, anthropic_sdk_client, is_oauth_credential).


##### `_openai_client`  (lines 38–42)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Creates a usable OpenAI model client from a model description and an OpenAI credential. It chooses between the normal OpenAI API client and a Codex-style client depending on whether the key identifies a ChatGPT account.

**Data flow**: It receives a `ModelSpec` and a key string. First it asks whether the key contains or maps to a ChatGPT account id. If not, it builds a normal OpenAI SDK client and wraps it in an `OpenAIClient`. If an account id is found, it builds a Codex SDK client for that account and marks the wrapper as `codex=True`. The output is the right kind of OpenAI client for that credential.

**Call relations**: This function is stored as the client-making recipe inside OpenAI `ModelSpec` entries created by `_openai`. Later, when the system needs to call an OpenAI-backed model, this recipe decides which OpenAI transport to use before returning the wrapped client.

*Call graph*: 4 external calls (__init__, chatgpt_account_id, codex_sdk_client, openai_sdk_client).


##### `_anthropic`  (lines 45–65)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS_WITH_TOOLS) -> ModelSpec
```

**Purpose**: Builds the catalog entry for one Anthropic model. It keeps the repeated Anthropic defaults in one place so each model row only has to state what is special about that model.

**Data flow**: It receives a model id, pricing, knowledge cutoff date, API-key environment variable name, and optional settings such as context window and reasoning support. It combines those with Anthropic defaults: the Anthropic provider name, the Anthropic client factory, the key slot, and the chat API surface. It returns a `ModelSpec` describing that Anthropic model.

**Call relations**: `core_model_specs` calls this repeatedly while assembling the built-in catalog. `_anthropic` does the common packaging work and hands back finished `ModelSpec` objects that can later be registered, priced, and used to create Anthropic clients.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 68–82)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: Builds the catalog entry for one OpenAI model. It centralizes the common OpenAI settings so the main catalog stays readable and consistent.

**Data flow**: It receives a model id, pricing, knowledge cutoff date, API-key environment variable name, and optionally which OpenAI API style to use. It adds OpenAI defaults: the provider name, OpenAI client factory, default context window, reasoning support, key slot, and selected API surface. It returns a `ModelSpec` describing that OpenAI model.

**Call relations**: `core_model_specs` calls this for every built-in OpenAI model. `_openai` turns each row of model facts into a finished `ModelSpec`, including whether the model should use the chat API or the responses API.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 85–199)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: Returns the complete list of model specifications that core ships with. This is the main source of truth for built-in model names, prices, knowledge cutoffs, context windows, reasoning support, and API routing.

**Data flow**: It receives the environment variable names that should be used for Anthropic and OpenAI keys. It creates many `ModelPrice` values, one per model, then passes each model’s facts into `_anthropic` or `_openai`. The output is a tuple of `ModelSpec` objects, ready for the registry and pricing ledger to use.

**Call relations**: This is the top-level builder for the catalog in this file. It calls `_anthropic` for Anthropic-backed models and `_openai` for OpenAI-backed models. The module then calls `core_model_specs` once to create `CORE_MODEL_SPECS`, and uses those specs to build the core price table and pricing digest.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `startup and model/client selection`

This file is like a product catalog plus a set of connection instructions for Amazon Bedrock Mantle. It lists the Bedrock model names the system can use, their prices, their knowledge cutoff dates, their context window sizes, and whether they support reasoning. A “context window” is how much text a model can read at once.

The file supports two kinds of Bedrock models. Anthropic model IDs are connected through Anthropic’s Bedrock Mantle client. OpenAI-compatible model IDs are connected through the OpenAI-style client, using either the chat endpoint or the newer responses endpoint depending on the model spec. This file does not translate requests itself; it points each model at the right existing client from the core SDK.

It also defines how credentials and region settings are found. The API key comes from a named credential slot or the AWS_BEARER_TOKEN_BEDROCK environment variable. The AWS region must come from AWS_REGION or AWS_DEFAULT_REGION. If no region is set, the file deliberately fails early with a clear error, because Bedrock URLs are region-specific.

At the end, manifest() packages all of this into a Manifest so the wider system can discover the provider and its models.

#### Function details

##### `bedrock_region`  (lines 46–52)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock requests should use. This matters because Bedrock endpoints are tied to a region, so the system cannot build a correct URL without it.

**Data flow**: It reads the process environment, first looking for AWS_REGION and then AWS_DEFAULT_REGION. If it finds a value, it returns that region string. If neither variable is set, it raises an error explaining what the user must configure.

**Call relations**: When either kind of Bedrock client is being created, _anthropic_client and _openai_client call this function first. It supplies the region they need before they can build a provider-specific client or endpoint URL.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 55–67)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds the client object used to talk to Anthropic models through Amazon Bedrock Mantle. Someone uses this indirectly when a Bedrock Anthropic model spec needs a real network client for making requests.

**Data flow**: It receives a ModelSpec, which describes the chosen model, and an API key string. It asks bedrock_region for the AWS region, creates an Anthropic Bedrock Mantle async client with that key, region, timeout, and no automatic retries, then wraps it in the SDK’s AnthropicClient together with the model spec.

**Call relations**: This function is stored inside Anthropic ModelSpec entries created by _anthropic. Later, when the system needs to call one of those models, the spec can use this builder to create the actual AnthropicClient. It relies on bedrock_region before handing off to Anthropic’s Bedrock client and the SDK wrapper.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 70–77)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds the client object used to talk to OpenAI-compatible models hosted on Bedrock Mantle. It chooses the correct Bedrock Mantle URL shape for either the chat API or the responses API.

**Data flow**: It receives a ModelSpec and an API key string. It reads the AWS region through bedrock_region, builds a base URL using that region, chooses the responses path when the spec asks for the responses API, otherwise chooses the chat-style path, creates an OpenAI SDK client for that URL, and wraps it in the SDK’s OpenAIClient.

**Call relations**: This function is stored inside OpenAI-compatible ModelSpec entries created by _openai. When one of those models is selected for use, the system calls this builder so requests go to the correct Bedrock Mantle OpenAI-compatible endpoint.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 80–99)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS) -> ModelSpec
```

**Purpose**: Creates a complete model listing for an Anthropic model on Bedrock. It keeps the repeated setup in one place so each Anthropic model can be added with only its model ID, price, cutoff, and any special limits.

**Data flow**: It takes a model ID, price information, knowledge cutoff, and optional context window and reasoning settings. It returns a ModelSpec filled with the Bedrock provider name, the Anthropic client builder, credential details, API style, price, context size, and reasoning support.

**Call relations**: This helper is used while BEDROCK_MODEL_SPECS is assembled in this file. Each call produces one Anthropic model entry that later appears in the manifest, and each entry points back to _anthropic_client for actual request-time connection setup.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 102–116)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates a complete model listing for an OpenAI-compatible model on Bedrock. It avoids repeating the same provider, credential, reasoning, and client-builder settings for every OpenAI-style model.

**Data flow**: It takes a model ID, price information, knowledge cutoff, context window size, and API surface name. It returns a ModelSpec containing those details plus the Bedrock provider name, the OpenAI-compatible client builder, the shared Bedrock credential slot, and the environment variable used for the API key.

**Call relations**: This helper is used while BEDROCK_MODEL_SPECS is built in this file. Each produced spec becomes part of the provider manifest, and each spec points to _openai_client so the system can create the right OpenAI-compatible Bedrock client when needed.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 204–215)

```
def manifest() -> Manifest
```

**Purpose**: Returns the extension manifest that tells the UFO system what this Bedrock extension provides. This is the main discovery point for the provider name, version, needed credential, and available models.

**Data flow**: It creates a CredentialSlot describing the Bedrock API key the user must supply. It then returns a Manifest containing the extension name, version, credential slot, and the full tuple of Bedrock model specs.

**Call relations**: The wider extension-loading system calls manifest when it wants to discover this provider. The function packages the static model catalog and credential requirement into the standard Manifest shape that the rest of UFO understands.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/harness/models/registry.py`

`domain_logic` · `startup and model-call routing`

The model registry is the single place the rest of the system asks, “What is this model, and how do I call it?” Without it, model facts would be scattered across the codebase: one part might route a request, another might price it, another might look up an API key, and mistakes would show up later as hard-to-understand provider errors or wrong billing.

At startup, `model_registry` gathers the built-in model definitions and any model definitions contributed by extension manifests. It stores them by exact model ID, refuses duplicates, and checks that configured default models really exist. It also builds one combined pricing table.

During a run, `ModelRegistry` can turn the special `auto` model choice into the configured real model, look up a model’s specification, find its provider, find which “bring your own key” slot pays for it, and build a working `ModelClient`. A `ModelClient` is the object that actually talks to the AI provider.

The file also contains safeguards for member-owned accounts. If a provider rejects a token before any streamed response has arrived, `_RebuiltOnRejection` rebuilds the client once, which gives refreshed credentials a chance to work. If a member has more than one connected provider account, `ServingModel` and `MemberAccounts` can move a turn to the next account, but only if the billing payer has not silently changed.

#### Function details

##### `_RebuiltOnRejection.complete`  (lines 57–72)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This wraps a model client so one provider authentication failure can be retried after rebuilding the client. It exists because a long AI turn can outlive an access token, and rebuilding may refresh that token.

**Data flow**: It receives a model request and starts streaming events from the already-built client. If events have already been delivered, any credential failure is passed upward because replaying would duplicate part of the answer. If the credential is rejected before the first event, it asks the registry to build a fresh client for the same model, checks that the funding source and payer are unchanged, and then streams the retry’s events back to the caller.

**Call relations**: This wrapper is created by `ModelRegistry.client_for` for member-routed calls. If the retry would switch who pays, it raises `ModelFundingChanged` instead of continuing, so billing and access rules stay consistent.

*Call graph*: 1 external calls (__init__).


##### `MemberAccounts.next`  (lines 95–105)

```
async def next(self) -> tuple[ModelSpec, ModelClient]
```

**Purpose**: This chooses the next member-owned account to try when the current provider account cannot continue. It lets a user who connected multiple paid accounts keep working without falling back to a workspace or platform key by accident.

**Data flow**: It reads the remaining alternate model IDs. If none are left, it raises the prepared “all accounts exhausted” error. Otherwise it removes the next model ID from the list, looks up that model’s specification, builds a client for it, and verifies that the funding type and payer still match the member-owned account expected for that model. It returns the new model specification and client.

**Call relations**: `ServingModel.move` calls this when a turn is allowed to move to another member account. It uses the registry for model facts and client construction, and checks the current workspace through `ws_current` so the move cannot secretly change who pays.

*Call graph*: 2 external calls (__init__, ws_current).


##### `ServingModel.move`  (lines 130–137)

```
async def move(self) -> bool
```

**Purpose**: This moves an active turn from its current model client to the next available member account, if such accounts exist. It keeps the turn’s model ID, model facts, and client in sync after the move.

**Data flow**: It checks whether this serving model has a `MemberAccounts` helper. If not, it returns `False`, meaning this turn cannot move. If it does, it asks for the next account, replaces its stored model specification and client, updates the model ID to match, and returns `True`.

**Call relations**: Higher-level turn logic can call this after a provider refuses a round before streaming any output. The method delegates the account choice and payer checks to `MemberAccounts.next`, then updates the single holder that the rest of the turn reads from.


##### `ModelRegistry.resolve`  (lines 151–154)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name `auto` into the concrete default model configured for the deployment. If the caller already named a real model, it leaves it alone.

**Data flow**: It receives a model string. If that string is the `auto` sentinel, it returns the registry’s configured `auto_model`; otherwise it returns the original string unchanged.

**Call relations**: `ModelRegistry.key_slot_for` and `ModelRegistry.model_key_env` call this before answering questions where `auto` must mean the model that will actually run.

*Call graph*: called by 2 (key_slot_for, model_key_env).


##### `ModelRegistry.spec`  (lines 156–162)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This looks up the registered facts for one exact model ID. It provides one clear failure point for unknown models instead of letting different parts of the system fail later in different ways.

**Data flow**: It receives a model ID and looks in the registry’s `specs` table. If found, it returns the `ModelSpec`, which describes the model’s provider, pricing, key needs, and client builder. If not found, it raises a clear `ValueError` naming the missing ID.

**Call relations**: `ModelRegistry.client_for`, `ModelRegistry.provider_for`, and `ModelRegistry.model_key_env` all call this before using model facts, so they share the same strict unknown-model check.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 164–199)

```
async def client_for(self, model: str) -> ResolvedModelClient
```

**Purpose**: This builds a ready-to-use client for calling a specific model, with the right credential and billing payer attached. It is called each time so changed or refreshed keys can take effect without restarting the process.

**Data flow**: It receives a model ID, looks up its specification, and decides whether the model needs an API key. If no key is needed, it builds a platform-funded client with an empty key. If a key is needed, it asks the current workspace for the correct credential, reports a clear error if the key is missing, rejects non-ASCII key text because providers cannot carry it safely, and builds the provider client. For member-routed calls, it wraps the client in `_RebuiltOnRejection` so an expired token can be refreshed once. It returns a `ResolvedModelClient` containing the client, funding type, and payer.

**Call relations**: This is the main path from a model ID to an object that can actually call the provider. It calls `ModelRegistry.spec` for model facts, uses `ws_current` for workspace credentials and routing rules, may create `_RebuiltOnRejection`, and returns the resolved client used by serving turns and account failover.

*Call graph*: calls 1 internal fn (spec); 4 external calls (__init__, __init__, __init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 201–205)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This answers which provider, such as Anthropic or OpenAI, serves a given model. That matters for routing, metrics, and billing labels.

**Data flow**: It receives a model ID, looks up its model specification, and returns the provider field from that specification. If the model ID is unknown, the shared specification lookup raises a clear error.

**Call relations**: It relies on `ModelRegistry.spec` so provider lookups follow the same strict registry rules as client creation and configuration checks.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 207–218)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This tells which workspace “bring your own key” slot would pay for a model, if any. It is intentionally forgiving for old or unknown model IDs so billing exports can still label historical rows instead of crashing.

**Data flow**: It receives a model name, first resolving `auto` to the configured real model. It then checks the registry table directly. If no spec exists, or the spec has no key slot, it returns `None`; otherwise it returns the key slot name.

**Call relations**: It calls `ModelRegistry.resolve` because stored agents may still say `auto`. Unlike `ModelRegistry.spec`, it does not raise for missing models, since its job includes reading historical or removed model names safely.

*Call graph*: calls 1 internal fn (resolve).


##### `ModelRegistry.model_key_env`  (lines 220–230)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding which environment variable should be set before the first turn for a core model. It only gives eager checks for known built-in providers; extension-provided models may resolve their keys later.

**Data flow**: It receives a model name and the global configuration. It resolves `auto`, looks up the model specification, checks the provider, and returns the configured Anthropic or OpenAI key environment variable name. For other providers, it returns `None`.

**Call relations**: It uses `ModelRegistry.resolve` so `auto` points to the real first-turn model, and `ModelRegistry.spec` so a bad model ID fails clearly during setup.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 233–266)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the active model registry for the process. It combines built-in model definitions with extension-contributed definitions, checks for configuration mistakes, and prepares the shared pricing table.

**Data flow**: It receives the global configuration and a tuple of manifests. It asks for the built-in model specs, adds every manifest’s model specs, and stores them by ID while rejecting duplicates. It then verifies that the configured automatic model, ambient reply model, and background jobs model all name registered specs. Finally it creates and returns a `ModelRegistry` with the spec table, merged pricing, and configured automatic model.

**Call relations**: This is the startup builder for the rest of the file’s runtime services. It calls `core_model_specs` to get built-in models, `pricing_from` to build the price lookup, and constructs the `ModelRegistry` that later code uses for routing, credentials, pricing, and model facts.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### `core/src/ufo/harness/models/spec.py`

`data_model` · `model registry setup and per-request model calling`

Think of a ModelSpec as a passport for an AI model. It says who provides the model, which client code can call it, what it costs, how much conversation it can fit, whether it supports images, whether it can use extended reasoning, and where to find its API key. Without this file, different parts of the system might each keep their own partial model tables, which could lead to confusing failures: one area might route a model, another might format an invalid request, and billing might silently miss it.

The file also defines small supporting records. ReasoningSupport describes whether a model can do extra step-by-step thinking and whether that feature works together with tool calls. RepeatedToolCompaction describes when repeated tool-heavy conversations should be shortened so they do not overflow the model's context window, which is the amount of text the model can see at once.

ModelSpec checks its own facts as soon as it is created. For example, the knowledge cutoff must look like a year and month, compaction limits must be sensible, and a model cannot claim reasoning-with-tools if it does not support reasoning at all. It also translates provider errors into project-specific errors for rejected keys and rate limits, so callers get a clear message instead of raw provider-specific failures.

#### Function details

##### `ReasoningSupport.internal_effort`  (lines 38–41)

```
def internal_effort(self) -> ReasoningEffort
```

**Purpose**: This decides what reasoning setting should be used internally when the system needs a safe baseline. If the model does not support reasoning, or reasoning can be turned off, it returns "off"; otherwise it returns the model's minimum required reasoning level.

**Data flow**: It reads the ReasoningSupport fields on this object: whether reasoning is supported, whether it can be disabled, and the minimum allowed effort. From those facts it produces one reasoning value, either "off" or the minimum effort. It does not change anything.

**Call relations**: This is a helper on the reasoning description itself. Other model setup or request-building code can ask it for a safe internal setting instead of repeating the same rule in several places.


##### `RepeatedToolCompaction.__post_init__`  (lines 51–55)

```
def __post_init__(self) -> None
```

**Purpose**: This validates the rule for shortening transcripts after repeated tool use. It prevents impossible or unsafe settings, such as triggering after only one turn or using a percentage outside the meaningful range.

**Data flow**: After a RepeatedToolCompaction record is created, it reads the requested number of consecutive turns and trigger percentage. If either value is invalid, it stops creation by raising a ValueError. If both values are sensible, the object remains unchanged and is ready to use.

**Call relations**: This runs automatically when a repeated tool compaction policy is created. ModelSpec can include one of these policies, and this validation ensures later transcript-shortening code receives a policy that already makes sense.


##### `ModelSpec.__post_init__`  (lines 85–106)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a model's specification is self-consistent before the rest of the system trusts it. It catches bad registry data early, instead of letting it fail later during a model call, prompt rendering, or billing.

**Data flow**: After a ModelSpec is created, it reads fields such as the knowledge cutoff date, reasoning settings, compaction settings, and context window size. If a rule is broken, it raises a ValueError with a clear message naming the model and the bad setting. If everything is valid, the spec remains unchanged.

**Call relations**: This runs automatically when the model registry creates ModelSpec records. Downstream code can then read facts from the spec with confidence, because basic contradictions have already been rejected.


##### `ModelSpec.key_rejected`  (lines 108–119)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This turns a provider's rejected-key response into a clear project-specific credential error. It tells the user which environment variable or workspace bring-your-own-key slot may contain the bad key and that it must be replaced.

**Data flow**: It reads the model id, provider name, key environment name, and key slot from the spec. It builds a human-readable message explaining that the provider refused the key. It returns a CredentialValueInvalid error object and does not change the spec.

**Call relations**: When model-calling code sees a provider authentication failure, such as an HTTP 401 status, it can call this method to produce the system's standard credential fault. This method hands off the final message to CredentialValueInvalid so callers do not need to understand each provider SDK's own error type.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.rate_limited`  (lines 121–129)

```
def rate_limited(self) -> ModelAccountRateLimited
```

**Purpose**: This turns a provider's rate-limit response into a clear project-specific account-capacity error. It explains that the account serving this model has no available capacity right now.

**Data flow**: It reads the model id and provider name from the spec. It builds a message saying the model was rate limited by that provider. It returns a ModelAccountRateLimited error object and does not change the spec.

**Call relations**: After a model client has used up its own retries and still receives a rate-limit response, such as an HTTP 429 status, calling code can use this method to report the standard system error. This keeps rate-limit reporting consistent across different providers.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 131–145)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This decides what reasoning setting, if any, should actually be sent to the model provider for one request. It protects the system from asking for reasoning in combinations the model does not support, especially when tools are involved.

**Data flow**: It receives the requested reasoning effort and the tools included in the request. It reads the model's reasoning rules from the spec. If the model does not support reasoning, or if tools are present but this model cannot combine tools with reasoning, it returns None, meaning no reasoning setting should be sent. If reasoning is requested as "off" but the model is default-on and cannot disable it, it returns the model's minimum effort instead. Otherwise it returns the requested effort unchanged.

**Call relations**: Request-building code uses this when preparing the actual provider call. It sits between the user's or system's desired reasoning level and the provider wire format, making sure the outgoing request matches what this specific model can accept.


### Deployment configuration and flags
Core deployment settings, proxy setup, and feature-flag adapters establish the runtime configuration surface used by the rest of the system.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project’s configuration rulebook. A running UFO service needs many outside pieces: a database, a blob store for large files, model providers, sandbox settings, extension choices, feature flags, and more. Rather than letting each part guess at settings later, this file gathers all of those knobs into typed sections and validates them as soon as the config is loaded.

It uses Pydantic, a Python library that turns plain data into checked objects. In everyday terms, it is like a checklist at the door: the service cannot enter with an incomplete filesystem blob store, a vague “auto” model where a real model ID is required, or a public sandbox URL that would drop security-sensitive browser cookies onto plain HTTP.

Most classes in the file are small config sections, such as `DatabaseConfig`, `BlobConfig`, `ModelsConfig`, and `SandboxConfig`. The top-level `Config` class combines them into one object. Some defaults are filled in automatically, such as common ports and default model names. Other values are deliberately not guessed, because guessing would be dangerous.

At the bottom, `config_path` chooses where to read the file from, and `load_config` reads TOML text, parses it, and validates it into a `Config`. Without this file, startup would be scattered and fragile: mistakes might only appear later, during a user request or background job.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 41–54)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the separate DBOS system database URL when the operator did not write one explicitly. DBOS is the system store used for workflow state, and this keeps it paired with the application database by default.

**Data flow**: It starts with the database section, especially `url` and possibly `system_url`. If `system_url` is already set, it leaves everything alone. If it is missing, it splits the main database URL, derives a sibling database name ending in `_dbos`, adjusts the driver name for SQLite or PostgreSQL where needed, stores that derived value back on the config object, and returns the updated object.

**Call relations**: This runs during validation of `DatabaseConfig`, usually as part of building the top-level `Config` in `load_config`. It does not call other project helpers; it prepares a complete database section before the rest of the service tries to connect.


##### `BlobConfig._backend_complete`  (lines 76–81)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step checks that the blob storage settings match the selected storage type. It prevents the service from starting with a storage backend that cannot possibly work.

**Data flow**: It receives the blob config after basic fields have been read. If the backend is `filesystem`, it requires a local `root` directory setting. If the backend is `s3`, it requires a `bucket` name. If the needed setting is absent, it raises an error; otherwise it returns the same config object unchanged.

**Call relations**: This runs when `BlobConfig` is validated, normally while `load_config` is turning the TOML file into a full `Config`. Its job is to stop bad blob storage setup before transcript files, artifacts, or shared records need to be written.


##### `ModelsConfig._models_concrete`  (lines 104–115)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step makes sure every deploy-level model setting names a real model, not the placeholder value `auto`. That matters because these settings decide which paid model calls the service will actually make.

**Data flow**: It reads the three configured model names: `auto_model`, `ambient_reply_model`, and `background_jobs_model`. For each one, it rejects an empty value or the special placeholder `auto`. If all three are concrete model IDs, it returns the config object unchanged.

**Call relations**: This runs during `ModelsConfig` validation as part of loading the full config. It protects later agent turns, ambient reply checks, and background jobs from reaching the model layer with an unresolved placeholder.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 236–272)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validation step checks that the public sandbox ingress URL is safe and shaped exactly the way the rest of the system expects. The ingress URL is used to make browser-accessible links to sandbox-hosted sites, so a small mistake here could break links or weaken session security.

**Data flow**: It starts with `ingress_public_url`. If it is not set, it accepts the config. If it is set, it breaks the URL into parts with `urlsplit`, then checks for a host, an allowed scheme, and the absence of path, query string, fragment, username, or password. It allows normal public deployments only over HTTPS, while `plain_local` permits local development addresses that never leave the machine. It returns the config if the URL is acceptable, or raises a clear error if not.

**Call relations**: This runs while `SandboxConfig` is being validated during config loading. It calls `urllib.parse.urlsplit` to inspect the URL and `ufo.sdk.http.plain_local` to recognize safe localhost-style development URLs. Later sandbox ingress and link-building code can then rely on this value being just a clean base address.

*Call graph*: 2 external calls (plain_local, urlsplit).


##### `config_path`  (lines 419–420)

```
def config_path() -> Path
```

**Purpose**: This small helper decides which configuration file path to use. It lets an operator override the default `ufo.toml` path with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that variable exists, its value becomes the path. If not, it uses the default `ufo.toml`. It wraps the chosen string as a `Path` object and returns it.

**Call relations**: `load_config` calls this when the caller did not provide a path directly. This keeps the “where is the config file?” rule in one place instead of repeating it across startup code.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 423–429)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This is the main entry for reading the deployment config from disk. It opens the TOML file, parses it, and turns it into a checked `Config` object.

**Data flow**: It receives an optional path. If no path is provided, it asks `config_path` for the configured or default location. It then checks that the file exists; if not, it raises a helpful `FileNotFoundError`. If the file exists, it reads the text, parses the TOML using `tomllib.loads`, validates the resulting data through `Config`, and returns the completed config object.

**Call relations**: Startup code calls this when the service or tool needs its deployment settings. It hands off path selection to `config_path` and TOML parsing to Python’s `tomllib`, then relies on the config classes in this file to fill defaults and reject unsafe or incomplete settings.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/flags.py`

`util` · `startup and feature checks during normal runtime`

Feature flags are switches that let a deployment turn features on or off without changing code. This file is the project’s single doorway to that system. That matters because different deployments may use different flag services, but the rest of the code should not need to know which one is in use.

At startup, `init_flags` can attach a provider to OpenFeature, which is a common interface for talking to feature-flag services. If no provider is given, OpenFeature’s no-op provider stays in place, so every flag simply uses the default value chosen by the code.

When code wants to check a flag, it calls `flag_enabled`. The check is tied to the current workspace, so a feature can be switched on for one workspace and off for another. The flag service is expected to answer with the strings `"true"` or `"false"`, not actual boolean values, because the supported backends store flag variations as strings.

The important safety rule is “fail closed.” If the provider is missing, slow, broken, returns an error, or sends an unreadable value, this file logs a warning and returns the caller’s default. It also limits each lookup to two seconds, so a stuck network call cannot hold up the user’s work.

#### Function details

##### `init_flags`  (lines 37–42)

```
def init_flags(provider: FeatureProvider | None) -> None
```

**Purpose**: This function connects the deployment’s chosen feature-flag provider to the process-wide OpenFeature API. If no provider is supplied, it deliberately does nothing, leaving the safe default no-op behavior in place.

**Data flow**: It receives either a feature provider object or `None`. If it gets `None`, nothing changes and future flag checks will resolve to their code defaults. If it gets a provider, it gives that provider to OpenFeature so later flag reads use it.

**Call relations**: This is used during startup, after the deployment has decided which flag backend, if any, should be active. It hands the provider to `openfeature.api.set_provider`, so later calls to `flag_enabled` can read through the same OpenFeature client without knowing which backend was selected.

*Call graph*: 1 external calls (set_provider).


##### `flag_enabled`  (lines 45–75)

```
async def flag_enabled(flag: str, *, default: bool) -> bool
```

**Purpose**: This function answers whether a named feature flag is on for the current workspace. It protects callers by returning their chosen default if the flag system cannot answer clearly and quickly.

**Data flow**: It takes a flag name and a boolean default. It reads the current workspace, builds an evaluation context from that workspace ID, converts the default into the string form the flag service expects, and asks OpenFeature for the flag’s string value. If the lookup times out, raises an error, reports an error code, or returns anything other than `"true"` or `"false"`, it logs a warning and returns the default. If the value is readable, it returns `True` for `"true"` and `False` for `"false"`.

**Call relations**: Application code calls this whenever it needs to decide whether to offer a feature. The function uses `ws_current` to aim the question at the active workspace, `EvaluationContext` to package that targeting information for OpenFeature, `api.get_client` to ask the configured provider, `asyncio.timeout` to keep the call from waiting too long, and `warn` to record cases where the flag could not be resolved safely.

*Call graph*: 5 external calls (timeout, get_client, EvaluationContext, warn, ws_current).


### `core/src/ufo/proxy_serve.py`

`config` · `startup`

This file is a small but important “front desk” for shared service configuration. It answers two questions that must be answered correctly before the service can run safely. First: which outside model-provider hosts may a sandbox contact, and what real API key should be used when it does? Second: what database connection string should the shared ingress service use when it needs the database owner role?

The model side is about controlled network access. A sandbox should not be able to call arbitrary internet hosts. Instead, this file looks at the configured environment variable names for supported model providers, checks whether those secrets are actually present in the deployment environment, and turns the available providers into egress rules. “Egress” means outgoing network traffic. If no provider key is present, it stops immediately, because the sandbox would otherwise have no valid route to make model calls.

The database side is about using a powerful connection deliberately. The shared service serves many workspaces, so it uses an owner database URL that can bypass row-level security, meaning database rules that normally restrict rows per user or workspace. Because that is dangerous if used casually, callers are expected to scope queries explicitly by workspace. This helper reads that owner URL from an environment variable or config, fails loudly if neither exists, and adjusts the URL so the async PostgreSQL driver is used.

#### Function details

##### `model_rule_base`  (lines 18–37)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the common outgoing-network rule set that lets sandboxes reach only the configured model providers. It also ensures that real provider keys exist before the sandbox is allowed to start, so a missing secret becomes an obvious startup failure instead of a confusing runtime problem.

**Data flow**: It receives the application Config and reads the configured environment variable names for Anthropic and OpenAI keys. For each configured key name, it asks the deployment environment for the actual secret; when a key is present, it derives the provider-specific egress rules and gathers the allowed host names into one shared scope rule. It returns a tuple of rules that can be attached to a sandbox, or raises an error if no usable model-provider key was found.

**Call relations**: This helper is the place where model-provider configuration becomes enforceable network policy. It relies on deploy_env to read the real deployment secret, asks derive_model_rules to translate a model probe and key into concrete access rules, and wraps the collected host list in a ScopeRule so later sandbox setup can apply one clear outgoing-access boundary.

*Call graph*: 3 external calls (__init__, deploy_env, derive_model_rules).


##### `owner_dsn`  (lines 40–52)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string for the shared service’s owner-level database connection. It makes sure the service cannot start without this powerful database credential being provided deliberately.

**Data flow**: It receives the application Config, first checks the UFO_OWNER_DSN environment variable, and falls back to the configured database owner_url if the environment variable is missing. If neither source has a value, it raises an error explaining that the shared service needs the owner role and must scope queries by workspace. If it finds a URL, it rewrites the beginning from postgresql:// to postgresql+psycopg:// so the async PostgreSQL driver is selected, then returns that rewritten string.

**Call relations**: This function is meant to be called during shared service setup before opening the database connection. Unlike model_rule_base, it does not call other project helpers; it reads the process environment and the config directly, then hands back a ready-to-use owner database URL for the ingress side of the service.


### `extensions/flags_open/ufo_ext_flags_open.py`

`domain_logic` · `config load and flag evaluation`

In production, the system reads feature flags from a service called Flagship. If that service is absent, normal flag checks would fall back to safe defaults, which often means features stay hidden. That is useful for safety, but frustrating for local development or evaluation stacks where people want to try everything that has been built.

This file supplies an OpenFeature provider. OpenFeature is a shared interface for asking, “Is this flag on?” Code elsewhere can ask the same question no matter which flag backend is plugged in. The provider here answers most boolean flags as true, and most string flags with the project’s standard “served true” spelling. It is like a light switch panel where every switch is flipped on by default.

There is one important exception. Some declared flags are not “unlock this feature” switches, but choices between two product shapes. Those can be marked with `open=False` in their `FlagSpec`. For those keys, this provider deliberately answers false so the development stack matches the shape served by the fleet. Undeclared keys are treated as open, because only a declared flag can explicitly ask to stay closed here.

For integer, float, and object flags, this provider does not invent values. It simply returns the caller’s default value.

#### Function details

##### `OpenProvider.__init__`  (lines 34–35)

```
def __init__(self, closed: frozenset[str]) -> None
```

**Purpose**: Creates an open flag provider and remembers which flag keys must stay closed. This is how the otherwise “everything on” provider keeps a small exception list.

**Data flow**: It receives a frozen set of flag names that should not be opened. It stores that set on the provider. After that, every flag lookup can check whether the requested key is in this closed list.

**Call relations**: The `build` function calls this when the flag backend is being constructed from declared flag specifications. Later, the provider’s resolve methods use the stored closed set to decide whether a particular flag should read on or off.

*Call graph*: called by 1 (build).


##### `OpenProvider.get_metadata`  (lines 37–38)

```
def get_metadata(self) -> Metadata
```

**Purpose**: Reports the provider’s identity to OpenFeature. This lets the flag system know that the active backend is the project’s `open` backend.

**Data flow**: It does not need caller input beyond the provider itself. It creates and returns a metadata object whose name is the backend name used by this extension.

**Call relations**: OpenFeature calls this when it needs information about the provider it is using. The function hands off to OpenFeature’s `Metadata` type to package the name in the form that framework expects.

*Call graph*: 1 external calls (Metadata).


##### `OpenProvider.resolve_boolean_details`  (lines 40–49)

```
def resolve_boolean_details(self, flag_key: str, default_value: bool, evaluation_context: EvaluationContext | None=None) -> FlagResolutionDetails[bool]
```

**Purpose**: Answers a true-or-false feature flag request. It returns true unless the flag key is one of the declared exceptions that should stay closed.

**Data flow**: It receives a flag key, a default value, and optionally an evaluation context with extra caller information. It checks whether the key is absent from the closed set. It returns a flag result containing the chosen boolean value, an `on` or `off` variant label, and a reason saying the answer is static, meaning it did not come from a remote rule or calculation.

**Call relations**: OpenFeature calls this when application code asks for a boolean flag. The function packages its answer using OpenFeature’s `FlagResolutionDetails` so the rest of the system receives a normal OpenFeature result.

*Call graph*: 1 external calls (FlagResolutionDetails).


##### `OpenProvider.resolve_string_details`  (lines 51–62)

```
def resolve_string_details(self, flag_key: str, default_value: str, evaluation_context: EvaluationContext | None=None) -> FlagResolutionDetails[str]
```

**Purpose**: Answers a string-valued feature flag request using the project’s standard text forms for served true and served false. This supports call sites that represent flag state as strings instead of plain booleans.

**Data flow**: It receives a flag key, a default string, and optionally an evaluation context. It checks whether the key is closed. If not closed, it returns the standard `SERVED_TRUE` value; if closed, it returns `SERVED_FALSE`. It also attaches the matching `on` or `off` variant and marks the reason as static.

**Call relations**: OpenFeature calls this when application code asks for a string flag. Like the boolean resolver, it wraps the answer in `FlagResolutionDetails` so callers see a normal flag evaluation result.

*Call graph*: 1 external calls (FlagResolutionDetails).


##### `OpenProvider.resolve_integer_details`  (lines 64–70)

```
def resolve_integer_details(self, flag_key: str, default_value: int, evaluation_context: EvaluationContext | None=None) -> FlagResolutionDetails[int]
```

**Purpose**: Declines to make up an integer flag value. It gives back the caller’s default integer unchanged.

**Data flow**: It receives a flag key, a default integer, and optionally an evaluation context. It ignores the key and context, returns the default value, and marks the reason as default, meaning no special value was supplied by this provider.

**Call relations**: OpenFeature calls this if code asks this backend for an integer flag. The function uses OpenFeature’s `FlagResolutionDetails` to say, in the expected format, that the default value should be used.

*Call graph*: 1 external calls (FlagResolutionDetails).


##### `OpenProvider.resolve_float_details`  (lines 72–78)

```
def resolve_float_details(self, flag_key: str, default_value: float, evaluation_context: EvaluationContext | None=None) -> FlagResolutionDetails[float]
```

**Purpose**: Declines to make up a decimal-number flag value. It returns the caller’s default float unchanged.

**Data flow**: It receives a flag key, a default float, and optionally an evaluation context. It does not inspect the key or context. It returns the same default value with a reason saying the result came from the default.

**Call relations**: OpenFeature calls this when application code requests a floating-point flag. The function hands back a standard `FlagResolutionDetails` response so the caller can continue normally with its fallback value.

*Call graph*: 1 external calls (FlagResolutionDetails).


##### `OpenProvider.resolve_object_details`  (lines 80–86)

```
def resolve_object_details(self, flag_key: str, default_value: Sequence[FlagValueType] | Mapping[str, FlagValueType], evaluation_context: EvaluationContext | None=None) -> FlagResolutionDetails[Sequen
```

**Purpose**: Declines to make up a complex flag value, such as a list or dictionary. It returns the caller’s default object unchanged.

**Data flow**: It receives a flag key, a default list-like or dictionary-like value, and optionally an evaluation context. It leaves that default untouched and returns it with a reason saying the default was used.

**Call relations**: OpenFeature calls this when code asks for an object-shaped flag. The function wraps the unchanged default in OpenFeature’s normal result object, keeping this backend predictable for flag types it does not actively support.

*Call graph*: 1 external calls (FlagResolutionDetails).


##### `build`  (lines 89–90)

```
def build(cache_ttl_seconds: float, declared: Mapping[str, FlagSpec]) -> OpenProvider
```

**Purpose**: Builds the open flag provider from the project’s declared flag list. It finds the flags that explicitly say they should not be opened in this backend.

**Data flow**: It receives a cache time-to-live value and a mapping of flag names to their `FlagSpec` declarations. The cache value is not used here. It scans the declarations, collects keys whose specification has `open` set to false, freezes that collection, and returns a new `OpenProvider` with that closed list.

**Call relations**: The extension manifest points to this function as the builder for the `open` backend. When the flag system selects this backend, it calls `build`, which then calls `OpenProvider.__init__` to create the provider used for later flag evaluations.

*Call graph*: calls 1 internal fn (__init__).


##### `manifest`  (lines 93–98)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the larger system. It says the extension’s name and version, and advertises that it provides the `open` flag backend.

**Data flow**: It takes no input. It creates a flag provider specification that connects the backend name to the `build` function, then wraps that in a manifest object with the extension name and version. The manifest is returned to whoever is discovering available extensions.

**Call relations**: The extension loading system calls this to learn what this file contributes. The function hands off to `FlagProviderSpec` and `Manifest` so the information is shaped exactly as the rest of the project expects.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/flagship/ufo_ext_flagship.py`

`io_transport` · `startup for flag provider setup; operator command time for flag writes`

Feature flags are switches that let the product turn behavior on or off without changing code. This file is the bridge between UFO’s own flag system and Cloudflare Flagship, the outside service that stores the live answers. At startup, the file checks whether the deployment has the needed Cloudflare app, account, and read token. If any are missing, it does not connect to Flagship, and the rest of the app simply uses each flag’s built-in default. That keeps a badly configured deployment from crashing because of flags.

For normal reads, the file builds a Flagship provider, which is the adapter used by OpenFeature, a common interface for feature-flag systems. Reads are intentionally bounded: the HTTP request has a short timeout and no retry, so a slow or unreachable Flagship service causes a closed/default answer instead of hanging the product.

The file also defines FlagshipAdmin, used by an operator command such as `ufoctl flags set`. This write path uses a separate token from the read path. It first reads the whole flag from Cloudflare, changes only the default variation to "on" or "off", and writes the full flag back. That matters because Cloudflare’s update API expects the whole flag record; sending only the changed field could accidentally erase rollout rules or Terraform-owned fields.

#### Function details

##### `build`  (lines 53–74)

```
def build(cache_ttl_seconds: float, _declared: Mapping[str, FlagSpec]) -> FeatureProvider | None
```

**Purpose**: Creates the runtime Flagship provider that the app uses to read feature flags. If the deployment does not have all required Cloudflare credentials, it returns nothing so the app falls back to code defaults instead of failing.

**Data flow**: It reads three deployment environment values: the Flagship app ID, Cloudflare account ID, and read token. If any value is missing, it records a warning saying which pieces were present and returns `None`. If all are present, it uses them, plus the requested cache time, to create a Flagship provider with a short request timeout and no retries.

**Call relations**: This function is registered in the extension manifest as the flag-provider builder. During startup, core flag setup calls it through that registration. It calls `deploy_env` to fetch deployment secrets, `warn` when setup is incomplete, and `FlagshipServerProvider` when it can actually connect the app to Cloudflare Flagship.

*Call graph*: 3 external calls (FlagshipServerProvider, deploy_env, warn).


##### `FlagshipAdmin.serve`  (lines 96–104)

```
def serve(self, key: str, *, on: bool) -> None
```

**Purpose**: Changes one Flagship flag so it serves either the "on" variation or the "off" variation by default. It is meant for administrative commands, not normal per-request flag reads.

**Data flow**: It receives a flag key and the desired boolean state. First it asks Cloudflare for the current full flag record. It checks that the record is readable and that the requested variation exists. Then it copies the current flag data, removes fields Cloudflare only returns for reading, replaces `default_variation`, and sends the complete updated record back to Cloudflare.

**Call relations**: This method is used when an operator wants to set a flag’s served value. It relies on `FlagshipAdmin._call` for both the read request and the write request, so all HTTP details and error handling stay in one place.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin.list`  (lines 106–118)

```
def list(self) -> tuple[str, ...]
```

**Purpose**: Returns the flag keys that currently exist in the connected Cloudflare Flagship app. This lets tools compare what Cloudflare actually has with what the code or infrastructure expects.

**Data flow**: It sends a request for the app’s flag collection. If Cloudflare does not return a readable list, it raises an error. Otherwise it pulls the `key` from each flag, turns each key into text, sorts them, and returns them as an immutable tuple.

**Call relations**: Administrative tooling can call this when it needs the service’s own view of available flags. Like `serve`, it delegates the actual HTTP request and response checking to `FlagshipAdmin._call`.

*Call graph*: calls 1 internal fn (_call).


##### `FlagshipAdmin._call`  (lines 120–135)

```
def _call(self, method: str, path: str, body: dict[str, object] | None=None) -> dict[str, object]
```

**Purpose**: Performs one authenticated HTTP request to Cloudflare’s Flagship API and turns Cloudflare error responses into clear Python errors. It is the shared low-level doorway used by the admin read and write operations.

**Data flow**: It receives an HTTP method, a path under the Flagship flags endpoint, and optionally a JSON body. It builds the full Cloudflare API URL from the stored account and app IDs, adds the bearer token, sends the request with the stored HTTP client, and parses the JSON response when present. If the HTTP status or Cloudflare `success` field shows failure, it raises a `RuntimeError`; otherwise it returns the parsed response body.

**Call relations**: This method is called by `FlagshipAdmin.serve` when reading and updating a single flag, and by `FlagshipAdmin.list` when reading the whole flag list. It keeps Cloudflare transport details out of those higher-level operations.

*Call graph*: called by 2 (list, serve).


##### `build_admin`  (lines 138–155)

```
def build_admin() -> FlagshipAdmin
```

**Purpose**: Creates the administrative Flagship client used by write commands. Unlike the read provider setup, it fails loudly if required write credentials are missing, because an operator asked to change something and needs a clear reason if that cannot happen.

**Data flow**: It reads the Cloudflare account ID, Flagship app ID, and special write token from deployment environment values. It collects the names of any missing values. If anything is missing, it raises an error listing those names; otherwise it returns a `FlagshipAdmin` configured with the account, app, and write token.

**Call relations**: This function is the setup step for commands such as `ufoctl flags set`. It calls `deploy_env` to read credentials and constructs `FlagshipAdmin`, which then performs the actual list or serve operations.

*Call graph*: 2 external calls (__init__, deploy_env).


##### `manifest`  (lines 158–164)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the wider UFO system. It tells core code the extension’s name, version, required deployment keys, and how to build its Flagship flag provider.

**Data flow**: It creates a manifest object containing fixed metadata and the three deployment keys needed for runtime flag reads. It also creates a flag-provider specification that names the backend as `flagship` and points to the `build` function as the provider factory.

**Call relations**: The extension loader calls this to discover what the file contributes. Through the returned `Manifest`, core setup learns which environment keys to report as required and which function to call when it wants to connect feature-flag reads to Cloudflare Flagship.

*Call graph*: 2 external calls (__init__, __init__).


### Assistant packs
Assistant pack manifests select the extension bundles for local billing development, standard local use, evaluation runs, and hosted deployment.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

This file exists to fill a gap between two existing ways of running the system. The normal local assistant pack is safe for day-to-day development, but it does not include Metronome, the external billing and usage-tracking service. The hosted assistant pack does include Metronome, but also includes hosted infrastructure that does not make sense on a laptop.

So this file creates a middle option: an `assistant_billing` pack. A pack is like a recipe card for which system extensions should be turned on together. This recipe starts with everything from the regular assistant pack, then adds `metronome` so the billing chain can be tested end to end.

That matters because hosted onboarding can offer an owner a “Set up billing” action. Without this pack, a local deployment could show or exercise parts of that path but could not actually service it through the billing integration. Because this can send usage and seat information to a billing provider, it is deliberately opt-in. Developers are expected to point it at safe test credentials, such as a Metronome sandbox token and a Stripe test-mode key.

#### Function details

##### `pack`  (lines 24–25)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for the local assistant-with-billing setup. Someone uses this when they want the system to load the regular assistant features plus the Metronome billing extension.

**Data flow**: It reads the file’s fixed name, version, and extension list. The extension list is made by taking the regular assistant pack’s extensions and adding `metronome`. It then creates a `Pack` object with those values and returns it to the pack-loading system.

**Call relations**: When the system loads this pack, it calls `pack` to get the recipe. `pack` hands the name, version, and extensions to `Pack.__init__`, which turns those plain values into the manifest object the rest of the system can use.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load / startup`

This file is like a packing list for the assistant product. Instead of containing the code for memory, search, apps, browser tools, document generation, connectors, model providers, and other features, it names the extensions that should be switched on together when someone selects the `assistant` pack.

The important idea is that a “pack” is a deployable preset. When configuration says to use the assistant pack, the system narrows itself to exactly the extensions listed here. Each extension brings its own tools, setup instructions, permissions, and behavior. This file does not add extra skills or onboarding text of its own; it only decides which existing pieces travel together.

That matters because the assistant experience depends on many separate capabilities working as one product. Without this file, someone would have to enable those capabilities one by one, and different deployments could accidentally miss key pieces such as memory, web research, the browser sandbox, member apps, scheduled tasks, or model providers.

The constants `NAME`, `VERSION`, and `EXTENSIONS` are the human-readable identity and contents of the pack. The `pack()` function turns those constants into a `Pack` object that the wider system can read during startup or configuration loading.

#### Function details

##### `pack`  (lines 70–71)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description for the local assistant setup. The system uses it to learn the pack’s name, version, and the exact extensions that should be activated together.

**Data flow**: It starts with the file’s fixed constants: the pack name, version, and tuple of extension names. It passes those values into the `Pack` constructor, which creates a structured manifest object. The result is returned to the caller; nothing else is changed.

**Call relations**: When the pack system asks this module for its manifest, this function is the handoff point. It creates a `Pack` object from the listed settings, so the rest of the startup flow can load the named extensions rather than reading raw constants from the file.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup`

This file exists so evaluation runs can behave like a real assistant session without accidentally depending on real third-party services. A “pack” is a named bundle of extensions that the system can load. Here, the bundle is called assistant_eval.

The file imports the regular assistant pack, then builds a safer evaluation version of it. It keeps most of the normal assistant extensions, but deliberately removes the real broker providers named composio and pipedream. Those services normally connect to outside tools, and an evaluation environment may not have their keys or should not use them. If they stayed in the pack, the agent might waste attempts trying to use unavailable real services.

After removing those real brokers, the file adds two evaluation-specific extensions: eval_env and docker. The eval_env extension represents the controlled test workplace. The docker extension lets an evaluation run use Docker so each conversation can get a real mounted workspace directory, rather than pretending by rewriting command paths.

An important detail is that merely registering these extensions does not run Docker or start external services. It only makes them available. They are used later only if the deployment configuration selects them.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for the assistant evaluation environment. The system uses this to know the pack’s name, version, and which extensions are available during an eval run.

**Data flow**: It reads the file-level constants for the pack name, version, and prepared extension list. It puts those values into a Pack object, which is the system’s standard container for describing a loadable pack, and returns that object to the caller.

**Call relations**: When the system asks this module what pack it provides, this function creates the answer. It hands the selected evaluation-safe extension list to Pack so the wider loading process can register those extensions without including the removed real broker integrations.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `startup / pack activation`

Think of this file as a packing list for the hosted version of the assistant. Instead of implementing memory, Slack, browser use, billing, or code execution itself, it names the pieces that should be included when the pack is activated. Without this file, the system would not know that the hosted assistant should use managed services such as Turbopuffer for search indexing, Browserbase for hosted browser sessions, Redis for live frames, E2B for sandboxes, and Slack or iMessage as user-facing surfaces.

The file sets a pack name and version, then defines a special prompt section for customer questions. That prompt section tells the assistant: when a paying customer asks about UFO itself, first consult the bundled `customer-onboarding-help` skill. This helps the assistant answer product-support questions from approved shipped content, rather than guessing or relying on workspace memory that may not exist.

It also lists many extensions that together make up the hosted assistant experience: chat surfaces, memory, connectors, scheduling, documents, model providers, feature flags, billing metering, browser tools, code tools, and more. Finally, it points to a local skills folder and includes one curated skill. The `pack()` function turns all of this declarative information into a `Pack` object the rest of the system can load.

#### Function details

##### `pack`  (lines 99–106)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the complete manifest for the hosted assistant pack. The system uses this to know which extensions to enable, which bundled skill to load, and what extra prompt instructions to give the assistant.

**Data flow**: It starts with the constants in this file: the pack name, version, extension list, skills directory, skill names, and customer-support prompt section. It creates a `SkillSpec` for each named skill, pointing to that skill's folder on disk. It then wraps everything into a `Pack` object and returns it, so the pack loader has one clear object describing the hosted assistant setup.

**Call relations**: When the hosted pack is loaded, this function is the place that assembles the pack description. Inside that assembly step, it calls `SkillSpec.__init__` to describe the bundled `customer-onboarding-help` skill, then calls `Pack.__init__` to package the name, version, extensions, skills, and prompt section into the final manifest.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation and sample packs
Specialized evaluation and sample pack manifests provide ready-made extension bundles for DSQA, GDPVal, and end-to-end pack-system testing.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `config discovery`

This file is like a small menu of preset toolkits. Instead of making users remember a long list of extensions every time they want to run DSQA evaluation work, it gives them three named choices: a core pack, a search pack, and a browser pack.

The core pack is the smallest bundle. It includes the default index, OpenAI embedding support, and OpenRouter model access. The search pack builds on that by adding extensions for web-style research and Perplexity-backed searching. The browser pack builds on the search pack again by adding browser automation and a Chrome sandbox, so the system can do work that needs an actual browser-like environment.

The important idea is layering. Each larger pack includes everything from the smaller one, then adds more abilities. This avoids repeating the same extension lists by hand and reduces the chance that similar packs drift apart by accident. If this file were missing, someone trying to use these DSQA evaluation setups would have to assemble the extension bundles themselves, which would be more error-prone and less discoverable.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest DSQA evaluation pack. Someone would use it when they need the basic indexing, embedding, and model-routing pieces but not web search or browser control.

**Data flow**: It starts with the fixed core pack name, version number, and core extension list stored near the top of the file. It passes those values into the Pack constructor, which creates a Pack object. The result is a named bundle the rest of the system can recognize and load.

**Call relations**: When the system asks this module for the core DSQA pack, this function builds it by calling Pack.__init__. It does not call any other local helpers; it simply turns the constants in this file into a Pack object.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA evaluation pack that includes search and research abilities. Someone would use it when evaluation tasks need to look things up beyond the local index or model tools.

**Data flow**: It reads the fixed search pack name, version number, and search extension list. That list includes the base extensions plus extra search-related extensions. It gives those values to the Pack constructor and returns the finished Pack object.

**Call relations**: When the system needs the search-capable DSQA pack, this function is the small factory that produces it. Its main handoff is to Pack.__init__, which receives the name, version, and extensions and turns them into the shared Pack representation.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA evaluation pack in this file, adding browser and sandboxed Chrome support on top of search. Someone would use it for tasks that need to inspect or interact with web pages through a browser-like tool.

**Data flow**: It takes the browser pack name, shared version number, and browser extension list from this module. That list includes the search extensions plus browser-specific additions. It passes everything into the Pack constructor and returns the resulting Pack object.

**Call relations**: When the system wants the browser-enabled DSQA pack, this function packages up the preset information and hands it to Pack.__init__. It is the final layer in the file’s pack ladder: core abilities first, then search, then browser support.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `startup/config load`

This file is like a menu of toolkits for running GDPVal evaluation tasks. A pack is a small manifest object that says, “Use this name, this version, and these extensions.” Extensions are add-on capabilities, such as indexing, embeddings, document tools, coding support, web research, browser use, or a sandboxed Chrome environment.

The file first names the shared version and groups the extensions into meaningful sets. Every pack includes the base extensions, which appear to provide the common foundation: a default index, OpenAI-style embedding support, and OpenRouter access. From there, the file builds three larger choices. The documents pack adds document, REPL, and coding tools. The research pack adds search and browser-style research tools. The full pack combines all of them.

Without this file, another part of the system would have to remember the exact extension lists and pack names by hand. That would make setup easier to get wrong. Here, the choices are clear and reusable: ask for the core pack when you want the minimum, or ask for the full pack when you want the complete GDPVal evaluation environment.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. It is used when the system only needs the shared base capabilities, not the document-specific or research-specific tools.

**Data flow**: It takes no inputs. It reads the fixed core pack name, version, and base extension list from this file, then creates and returns a Pack object containing those values. Nothing else is changed.

**Call relations**: When some setup code wants the basic GDPVal environment, it calls this function. The function immediately hands the chosen name, version, and extensions to Pack.__init__, which builds the actual manifest object used by the rest of the system.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack meant for document-heavy work. It includes the shared base tools plus document, interactive REPL, and coding extensions.

**Data flow**: It takes no inputs. It combines the base extension list with the document-focused extension list, then uses those combined values with the document pack name and version to return a Pack object. It does not modify any global data.

**Call relations**: Setup code calls this when it needs GDPVal with document and coding support. This function gathers the right extension names and passes them to Pack.__init__, which produces the manifest the system can load.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack meant for research-style tasks. It includes the shared base tools plus extensions for research, web browsing, Perplexity access, and a sandboxed Chrome browser.

**Data flow**: It takes no inputs. It joins the base extensions with the research extension group, then returns a Pack object with the research pack name, version, and full extension list. The file’s constants stay unchanged.

**Call relations**: When the system needs research capabilities rather than document tools, setup code can call this function. The function passes the prepared manifest details to Pack.__init__, which turns them into the Pack object used later.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the most complete GDPVal pack. It includes the base tools, document and coding tools, and research and browser tools all in one bundle.

**Data flow**: It takes no inputs. It combines all three extension groups in order: base, document, and research. It then returns a Pack object named as the full GDPVal pack, using the shared version and the combined extension list.

**Call relations**: Setup code calls this when it wants the complete GDPVal evaluation environment. The function assembles every extension group and gives the result to Pack.__init__, which creates the manifest object that downstream loading code can use.

*Call graph*: 1 external calls (__init__).


### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample pack, meaning it is a deliberately small but real example used to check that the public pack interface still works. It imports only from `ufo.sdk`, which is the stable surface that outside pack authors are expected to use. That matters because tests can use this pack like an outside user would; if this file stops working, the project may have accidentally broken the pack boundary.

The file names the pack, gives it a version, points to the bundled sample extension, and points to a skill folder on disk. Its main entry point is `pack()`, which returns a `Pack` object. You can think of that object as a shipping label: it tells the system what extension, skills, and setup work belong in this package.

The setup work is `_setup()`. When the onboarding step runs, it writes `{ "pack_onboarded": true }` into the pack’s scoped store. A scoped store is durable storage tied to this pack, not just a temporary log message. This is important because tests can later read the value back through the same public system path that normal code uses. Without this file, the project would lose a simple, real-world probe that checks whether pack loading, bundled extensions, skills, onboarding, and storage all still connect correctly.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the pack’s onboarding action. It records that the pack has completed its setup by writing a small marker into the pack’s durable store.

**Data flow**: It receives an `ExtensionContext`, which is the runtime object that gives the pack access to its allowed tools, including storage. It writes the key `pack:onboarded` with the value `{ "pack_onboarded": true }` into that store. It returns nothing, but after it finishes, the store contains proof that the onboarding step actually ran.

**Call relations**: The `pack()` function wraps this function inside an `OnboardingStep`. Later, when the system activates or onboards the pack, that onboarding step calls `_setup` with the pack’s context so the durable marker can be written.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the public entry point the pack loader uses to discover what this pack contains. It builds and returns a `Pack` description that names the bundled extension, the contributed skill, and the onboarding step.

**Data flow**: It starts from constants in this file: the pack name, version, bundled extension name, skill folder path, onboarding step name, and setup function. It turns the skill path into a `SkillSpec`, turns the setup function into an `OnboardingStep`, and combines those pieces into a `Pack`. The result is a complete description the host system can load.

**Call relations**: When pack discovery asks this module what it provides, `pack()` creates the objects the rest of the system understands. It calls `SkillSpec` to describe the skill, `OnboardingStep` to attach `_setup` as setup work, and `Pack` to bundle everything into one pack definition.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-feature-flags` — The shared on/off switches that let the service enable or disable behavior at runtime.
- `reg-model-catalog` — The live menu of AI models, their capabilities, providers, and calling rules.
- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-skill-library` — The stored and packaged reusable skill instructions and files available to agents.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-egress-policy-cache-state` — Per-workspace egress-rule generation and cache-freshness state used by proxies to detect stale sandbox network-access rules.
