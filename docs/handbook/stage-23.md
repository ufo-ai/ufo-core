# Model Catalogs, Pricing, Accounting, and Usage Metering  `stage-23` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for knowing which AI models exist, how to call them, and how much their use costs. It is used during startup to build the available model list, and during normal requests to choose models, price usage, and record spending.

The model spec file defines the standard “fact sheet” for a model: provider, API route, context size, pricing, and features such as reasoning support. The catalog file fills that format with the built-in models the system already knows. The registry then acts like the front desk: given a model name, it returns the correct facts, required API key information, and client object used to make calls.

Pricing turns token counts into money. Tokens are small chunks of text counted by AI APIs. It also fingerprints the price table so old bills can show exactly which prices were used. The catalog skill exposes the live registry to users, so they can ask what models are available. Accounting is the cash register: it records usage, checks spend limits, exports billable records, and builds spending reports.

## Files in this stage

### Model catalog definitions
Built-in model metadata and shared model records define what the system knows about each supported AI model.

### `core/src/ufo/models/catalog.py`

`config` · `startup/config load`

This file acts like a price list and address book for the core AI models. Without it, the system would not know which Anthropic or OpenAI models are available by default, how much to charge for their token use, which client code to use, or which environment variable should contain the needed API key.

The file defines shared facts first: the names of API key slots, the environment variable names, default context window sizes, and a reusable setting that says these models support reasoning and can use tools while reasoning. A context window is the amount of text a model can consider at once.

It then provides small helper functions that build model descriptions. A `ModelSpec` is the system's record card for one model: its ID, provider, pricing, knowledge cutoff, supported API surface, and a function that can create the right client when given an API key. The Anthropic and OpenAI helpers fill in the provider-specific defaults so each model entry does not have to repeat them.

The main function, `core_model_specs`, returns the full built-in list. At import time, the file also builds `CORE_MODEL_SPECS`, a price table, a pricing object, and a price digest. The digest is a compact fingerprint of the pricing data, useful for detecting exactly which pricing set is in use.

#### Function details

##### `_anthropic_client`  (lines 24–25)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Creates a ready-to-use Anthropic model client for one model specification and one API key. This lets the catalog store a recipe for making the client instead of opening a connection immediately.

**Data flow**: It receives a model description and an API key. It first creates the underlying Anthropic software development kit client using that key, then wraps it in the project's `AnthropicClient` along with the model description. The result is a project-level client object ready to send requests for that model.

**Call relations**: This function is stored inside Anthropic `ModelSpec` records as the client factory. When another part of the system later chooses an Anthropic model and has an API key, it can call this recipe to get the actual client. It relies on the lower-level Anthropic SDK client builder and then hands that client into the project's Anthropic wrapper.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 28–29)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Creates a ready-to-use OpenAI model client for one model specification and one API key. It is the OpenAI counterpart to the Anthropic client factory.

**Data flow**: It receives a model description and an API key. It uses the key to create the underlying OpenAI SDK client, then wraps that lower-level client and the model description in the project's `OpenAIClient`. The returned object is what the rest of the project can use to talk to that OpenAI model.

**Call relations**: This function is stored inside OpenAI `ModelSpec` records as the client-making recipe. Later, when model routing selects an OpenAI model, the system can call this function with the configured key. It delegates the raw SDK setup to `openai_sdk_client` and then packages it in the project's OpenAI client wrapper.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 32–51)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Builds a complete catalog entry for one Anthropic model. It fills in all the Anthropic-specific defaults so the main catalog can list models clearly and consistently.

**Data flow**: It receives the model ID, price, knowledge cutoff, API key environment variable name, and optionally a custom context window. It combines those with fixed Anthropic settings: provider name, key slot, chat API surface, reasoning support, and the Anthropic client factory. It returns a `ModelSpec`, which is the system's structured record for that model.

**Call relations**: The main catalog function calls this repeatedly while assembling the built-in Anthropic model list. Instead of each catalog entry spelling out the same provider and key details, `core_model_specs` hands the unique facts to this helper and gets back a finished model record.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 54–68)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: Builds a complete catalog entry for one OpenAI model. It centralizes the OpenAI defaults, including the standard context size and the recipe for creating an OpenAI client.

**Data flow**: It receives the model ID, price, knowledge cutoff, API key environment variable name, and optionally which OpenAI API surface to use. It adds the fixed OpenAI settings: provider name, key slot, context window, reasoning support, and client factory. It returns a `ModelSpec` ready to be placed in the model catalog.

**Call relations**: The main catalog function calls this for each built-in OpenAI model. Most OpenAI models use the default chat surface, but the helper also lets a model declare a different API surface when needed, so the routing code can later form a legal request.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 71–160)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: Returns the full tuple of model descriptions that core ships with. This is the single built-in source for model IDs, prices, knowledge cutoff dates, context windows, provider routing, and API key environment names.

**Data flow**: It receives the names of the environment variables that should hold the Anthropic and OpenAI API keys. It creates `ModelPrice` records for each model, passes those prices and model facts into the Anthropic or OpenAI helper, and collects the resulting `ModelSpec` records into one tuple. The output is a complete built-in catalog that other code can index by model ID.

**Call relations**: This is the file's main assembly point. At module load time, it is called to create `CORE_MODEL_SPECS`; that result is then used to build the core price map, pricing object, and pricing digest. It hands individual model construction to `_anthropic` and `_openai` so the catalog remains readable and provider-specific details stay in one place.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `core/src/ufo/models/spec.py`

`data_model` · `model selection and request preparation`

This file is like a catalog card for every model the project can use. Each `ModelSpec` is a frozen record, meaning once it is created its facts cannot be quietly changed. That matters because many parts of the system need the same answers: which provider owns the model, which client code should call it, what price to charge, what API style it uses, what date its training knowledge stops at, and whether it can do extra reasoning.

The goal is to make model behavior predictable. Without this shared record, one part of the system might think a model supports tools with reasoning, another might bill it differently, and another might crash while rendering a prompt. Instead, all of those places are expected to ask the model registry for a `ModelSpec` and read the same facts.

The file also defines `ReasoningSupport`, a small record that says whether a model supports extended reasoning at all, and whether that reasoning can be used at the same time as tools. A tool is an external function the model can ask the system to run. Some models can reason normally but cannot combine that mode with tool calls. `ModelSpec.default_reasoning` turns reasoning off automatically in those cases, so requests stay compatible with the model.

#### Function details

##### `ModelSpec.__post_init__`  (lines 54–62)

```
def __post_init__(self) -> None
```

**Purpose**: This method checks that a newly created model record is internally consistent. It catches bad model metadata early, such as an invalid knowledge cutoff date or a claim that tools can use reasoning when reasoning itself is not supported.

**Data flow**: A `ModelSpec` has already been filled with values. This method reads its `knowledge_cutoff` and `reasoning` fields. If the cutoff is not written as `YYYY-MM`, or if the reasoning settings contradict each other, it raises an error. If everything is valid, it returns nothing and leaves the frozen record ready to use.

**Call relations**: This runs automatically when a `ModelSpec` is created by the registry or setup code that defines available models. Its job is to stop bad model definitions at the boundary, before later request-building, prompt-rendering, or billing code relies on incorrect facts.


##### `ModelSpec.default_reasoning`  (lines 64–74)

```
def default_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort
```

**Purpose**: This method decides what reasoning setting should actually be sent to a model for one request. It preserves the caller’s requested reasoning level only when the model supports it and when that request’s tool use is allowed with reasoning.

**Data flow**: It receives the requested reasoning effort and the tools included in the request. It checks the model’s reasoning capability. If the model does not support reasoning, it outputs `off`. If tools are present but this model cannot combine tools with reasoning, it also outputs `off`. Otherwise, it outputs the original requested effort.

**Call relations**: Request-building code calls this when preparing a model call. It acts as a safety filter between the caller’s preference and the model’s real abilities, so the final request handed to the model client does not ask for an unsupported combination.


### Pricing and registry
Pricing logic and the model registry turn catalog entries into usable model facts, clients, and billable rates.

### `core/src/ufo/models/pricing.py`

`domain_logic` · `cross-cutting`

This file is the small billing calculator for model usage. AI model providers charge different rates for different kinds of tokens: input tokens, output tokens, and cached tokens that are read or written. This file stores those rates in `ModelPrice`, then uses them to convert a `Usage` record into a cost measured in micro-USD, which means millionths of a US dollar.

The file also solves an audit problem. Prices can change over time, so each price table gets a deterministic digest, which is like a receipt stamp made from the full table contents. If the same table is used again, it produces the same stamp; if any rate changes, the stamp changes. This lets billed usage records point back to the exact version of pricing that was applied.

The main flow is simple: callers build a `Pricing` object from a mapping of model names to rates. `pricing_from` copies the table and calculates its digest. Later, accounting code asks `Pricing.micro_usd` to price one model’s usage. If the model is known, the function multiplies each token count by its matching rate and scales the result down from “per million tokens.” If the model is unknown, it logs a warning and returns zero rather than crashing, which is useful for old historical records.

#### Function details

##### `price_digest`  (lines 24–39)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: This function creates a stable version stamp for a model price table. Someone would use it when they need to prove which exact prices were used for billing.

**Data flow**: It receives a mapping from model names to `ModelPrice` values. It rewrites that information into sorted JSON text so the same prices always produce the same text, then runs SHA-256, a standard one-way fingerprinting algorithm, over that text. It returns a string starting with `sha256:` followed by the fingerprint.

**Call relations**: When `pricing_from` builds a `Pricing` object, it calls `price_digest` first so the finished pricing table carries its own version stamp. Internally, this function hands the normalized table text to `json.dumps` for serialization and to `hashlib.sha256` for the final fingerprint.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 42–54)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: This function calculates the cost of one usage record for one model. It is the core arithmetic that turns token counts into a billable amount.

**Data flow**: It receives a model name, a `Usage` object containing token counts, and a table of model prices. It looks up the model’s rates, multiplies each token count by the matching rate, adds those pieces together, and divides by one million because the rates are expressed per million tokens. It returns the final cost in micro-USD. If the model is not in the table, it logs that fact and returns zero.

**Call relations**: This is called by `Pricing.micro_usd`, which is the object-oriented wrapper used elsewhere. If a model is missing, it calls `ufo.o11y.log` so the system records the unexpected price lookup without stopping the accounting flow.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 64–65)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: This method prices one model usage record using the price table stored inside a `Pricing` object. It gives the rest of the system a simple way to ask, “What did this usage cost?”

**Data flow**: It receives the model name and a `Usage` record. It reads the `prices` stored on the current `Pricing` object, passes everything to `usage_priced_micro_usd`, and returns the computed micro-USD amount.

**Call relations**: Accounting code calls this method when recording sandbox token usage, turn usage, or workspace usage. The method delegates the actual math to `usage_priced_micro_usd`, keeping callers from needing to know the details of the pricing formula.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 68–71)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: This function builds a complete immutable-style pricing object from a plain model-to-price table. It packages the rates together with the digest that identifies them.

**Data flow**: It receives a mapping of model names to `ModelPrice` entries. It copies that mapping into a regular dictionary, computes the table’s digest with `price_digest`, and returns a new `Pricing` object containing both the copied table and the digest.

**Call relations**: This is the setup function for pricing data. It calls `price_digest` to create the version stamp, then constructs `Pricing` so later accounting code can call `Pricing.micro_usd` with a consistent table and its matching fingerprint.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### `core/src/ufo/models/registry.py`

`domain_logic` · `startup and model request handling`

This file solves a coordination problem: many parts of the system need to know which AI model is being used, how much it costs, what provider runs it, and what secret key is needed to call it. Instead of letting each part guess, this file creates one shared registry, like a front desk that knows every approved model by name.

The registry combines built-in model definitions with model definitions contributed by extensions called manifests. Each model must have a unique id. If two models claim the same id, startup fails immediately. That is intentional: it is much safer to catch a bad model table at boot than to discover it halfway through an AI request.

The registry also supports an `auto` model choice. `auto` is not a real model; it means “use the configured default model.” This lets agents defer the exact model choice to deployment settings.

When code needs to talk to a model, `client_for` looks up the model, finds the right API key source, checks that the key can safely be sent over the provider's wire protocol, and builds a provider client. Keys may come from the current workspace's bring-your-own-key slot or from environment variables. Pricing is also built from the same registered specs, so billing and routing share one source of truth.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: Turns the special `auto` model name into the concrete default model configured for this deployment. If the caller already named a real model, it leaves that name unchanged.

**Data flow**: It receives a model name. If that name is the `auto` sentinel, it replaces it with the registry's configured `auto_model`; otherwise it returns the original name. Nothing else is changed.

**Call relations**: This is used by `ModelRegistry.model_key_env` before checking which environment variable is needed. That way onboarding checks the key for the model that will actually run, not the placeholder name `auto`.

*Call graph*: called by 1 (model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: Looks up the full description of a model by its exact id. It fails clearly if the id is unknown, so mistakes are caught at the registry boundary instead of later in routing, rendering, or billing.

**Data flow**: It receives a model id and reads the registry's `specs` table. If the id exists, it returns the matching `ModelSpec`; if not, it raises a `ValueError` explaining that no model is registered for that id.

**Call relations**: `ModelRegistry.client_for` uses this when it needs the provider, key settings, and client builder for a model. `ModelRegistry.model_key_env` uses it after resolving `auto` so it can inspect the provider behind the selected model.

*Call graph*: called by 2 (client_for, model_key_env).


##### `ModelRegistry.client_for`  (lines 44–69)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Builds the actual client object used to call a selected AI model. It also finds the right API key at the moment of use, so workspace-specific keys and rotated platform keys are honored without restarting the service.

**Data flow**: It receives a model id, looks up that model's spec, and checks whether the model needs a key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the key, using either the model's bring-your-own-key slot or its environment variable. If no key is available, it raises a clear runtime error. If the key contains non-ASCII characters, it raises `CredentialValueInvalid` because the provider connection cannot carry that value safely. On success, it returns a ready model client built from the spec and key.

**Call relations**: This is the request-time bridge from a registered model id to a usable provider client. It calls `ModelRegistry.spec` first, then asks `ws_current` for the active workspace so credentials can be resolved in the right context. It hands the final spec and key to the model spec's client factory.

*Call graph*: calls 1 internal fn (spec); 2 external calls (__init__, ws_current).


##### `ModelRegistry.key_slot_for`  (lines 71–78)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Answers which workspace bring-your-own-key slot would be used for a model, if any. It is deliberately forgiving: unknown or keyless models return `None` instead of raising an error.

**Data flow**: It receives a model id and checks the registry's `specs` table without using the stricter `spec` lookup. If there is no spec, or the spec has no key slot, it returns `None`. Otherwise it returns the slot name where that model's workspace key would live.

**Call relations**: This is useful for reporting paths such as billing exports, where old ledger rows may mention models that are no longer registered. Instead of blocking the export, it lets the caller label the model as not tied to a workspace key slot.


##### `ModelRegistry.model_key_env`  (lines 80–90)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: Tells onboarding which environment variable must be set before a first turn can use a core provider model. For extension-contributed providers, it returns `None` because the core system may not know what environment variable that provider uses.

**Data flow**: It receives a model name and the runtime configuration. First it resolves `auto` to the actual default model. Then it looks up that model's spec and reads its provider. If the provider is Anthropic, it returns the configured Anthropic API key environment variable name. If the provider is OpenAI, it returns the configured OpenAI API key environment variable name. For any other provider, it returns `None`.

**Call relations**: This function combines `ModelRegistry.resolve` and `ModelRegistry.spec` to answer a startup or onboarding question: what key should the operator provide before this model is used? It does not build a client; it only identifies the expected environment variable for known core providers.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 93–114)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: Builds the complete active model registry from built-in model specs and extension-provided specs. It validates the table early so duplicate ids or a bad default model stop the service at startup instead of causing scattered failures later.

**Data flow**: It receives the configuration and a tuple of manifests. It asks `core_model_specs` for the built-in models using the configured Anthropic and OpenAI key environment names. Then it walks through both built-in specs and all manifest model specs, inserting each into a dictionary by id. If an id appears twice, it raises a `ValueError`. It also checks that the configured default `auto_model` points to a registered model. Finally it builds a merged pricing table with `pricing_from` and returns a frozen `ModelRegistry` containing the specs, pricing, and default model id.

**Call relations**: This is the construction point for the registry. During setup, it gathers model facts from core code and manifests, calls `pricing_from` so pricing uses the same model table, and returns the `ModelRegistry` that later request-time code uses for lookup, key resolution, client creation, and onboarding checks.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### Catalog presentation and accounting
User-facing catalog output and accounting logic expose available models while tracking cost, usage, spend caps, and billable exports.

### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a simple but important problem: people need to know which models are available, what they cost, how much text they can read at once, and whether they support features like reasoning. Instead of keeping that information in a separate hand-written document, this file generates it from the same model registry the runtime uses to route requests and calculate prices. That means the catalog is not a stale brochure; it is made from the system’s current source of truth.

The main function takes a ModelRegistry, which is the collection of known model records. It sorts those records by model id, turns each one into a row in a Markdown table, and wraps the table in a RuntimeSkill. A RuntimeSkill is a piece of instruction text the system can load and show or use at runtime. In everyday terms, this file prints the restaurant menu directly from the kitchen’s inventory system, rather than from a separate paper menu that someone might forget to update.

A small helper formats prices stored as tiny “micro-dollar” units into normal dollar text per million tokens. Tokens are chunks of text that models process, so this makes model pricing readable to humans.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns a price stored in micro-dollars into a normal dollar string, such as “$1.25”. It is used so the catalog can show readable prices instead of internal accounting units.

**Data flow**: It receives an integer price measured in micro-dollars per million tokens. It divides that number by the constant that says how many micro-dollars make one dollar, formats the result with two decimal places, and returns the finished price string.

**Call relations**: When the catalog table is being built, model_catalog_skill asks this helper to format each model’s input and output prices. The helper gives back display-ready text that can be placed directly into the Markdown table.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function builds the complete model catalog skill from the live model registry. Someone would use it during startup to create a trustworthy, up-to-date reference page for available models.

**Data flow**: It receives a ModelRegistry containing model specifications. It reads each model’s id, provider, knowledge cutoff, context window, prices, reasoning support, and API surface; sorts the models by id; turns them into rows in a Markdown table; then wraps that table and its metadata into a RuntimeSkill object. The output is a ready-to-load skill whose instructions contain the generated catalog.

**Call relations**: This is the main builder in the file. As it walks through the registered models, it hands each price to _per_mtok so prices become human-readable. Once the Markdown body and metadata are ready, it creates a RuntimeSkill so the rest of the runtime can treat the generated catalog like any other skill.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `core/src/ufo/accounting.py`

`domain_logic` · `cross-cutting: turn completion, sandbox proxy metering, admission checks, reporting, and billing export jobs`

This file exists so the project can answer three practical questions: “What did we use?”, “What should be billed?”, and “Are we allowed to spend more?” It writes usage into a ledger, which is like a checkbook: every model call, sandbox token burn, or sandbox egress request becomes a row with an amount, a price, and enough labels to audit it later.

The file is careful about retries. A turn may be replayed, paused, or resumed, so the code uses stable ledger IDs where the same work must not be billed twice, and fresh IDs where a real repeated provider call should be billed again. Sandbox usage can grow over time, so it is accumulated instead of replaced.

It also creates export records for outside billing systems. These exports freeze a usage “delta” so a retry sends the exact same billable item again, rather than recalculating a different number.

Finally, it enforces spend caps and produces spend summaries. Caps can apply to a whole workspace, one member, or one agent. Reports sum the same ledger by different views: total spend, spend by member, by agent, by usage type, and by price table version. Without this file, usage could be lost, double-counted, exported unreliably, or allowed to run past configured limits.

#### Function details

##### `applicable_caps_absent`  (lines 44–50)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: This is a quick shortcut for the common case where no spend cap applies. It tells callers whether a recent database check already found no cap for the exact workspace, member, and agent combination.

**Data flow**: It receives a workspace ID, optional member ID, and agent ID. It looks up that exact triple in a small in-memory cache and compares the stored expiry time with the current monotonic clock, which is a clock used for measuring elapsed time. It returns true only if the “no caps here” note is still fresh.

**Call relations**: Spend cap enforcement can call this before doing a database lookup. When SpendEvaluator.decide later discovers that no caps apply, it refreshes this cache through _note_absent_caps, making future checks cheaper for a few seconds.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 53–62)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: This records that a particular workspace, member, and agent combination had no applicable spend caps. It keeps that note only briefly, so a newly added cap starts taking effect soon.

**Data flow**: It receives the exact cache key. It reads the current monotonic time, removes expired cache entries if the cache has grown large, and stores a new expiry time a few seconds in the future. It returns nothing, but changes the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after it has checked the database and found no caps. That lets later cap checks use applicable_caps_absent as a short-lived fast path instead of paying for another database round trip.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `record_turn_usage`  (lines 65–105)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This writes the billable model-token usage for one turn attempt. It is designed so retries do not create duplicate charges for the same attempt.

**Data flow**: It receives a database connection, workspace and turn IDs, the model name, usage counts, an attempt identifier, and a pricing table. It adds all token categories together; if the total is zero, it does nothing. Otherwise it builds a stable ledger ID, checks whether that ID is already present, and inserts one priced ledger row if not.

**Call relations**: Turn-running code calls this when a model call’s usage should be billed to a turn. It asks Pricing.micro_usd to convert token usage into millionths of a US dollar, uses ledger_id_for to make a repeat-safe ledger key, and writes through the async database connection.

*Call graph*: calls 1 internal fn (micro_usd); 4 external calls (execute, insert, select, ledger_id_for).


##### `read_turn_cost`  (lines 108–126)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID) -> tuple[int, int, str] | None
```

**Purpose**: This reads the total billed token cost for a turn. It matters because one turn may have multiple run attempts, especially if it was parked and later resumed.

**Data flow**: It receives a database connection and a turn ID. It sums token amounts and priced cost across ledger rows for that turn, and also reads the model value. If no token ledger row exists, it returns None; otherwise it returns total tokens, total micro-dollars, and the model string.

**Call relations**: Other parts of the system use this when they need the final recorded cost of a turn. It reads from the same ledger rows written by record_turn_usage, so it reports what was actually billed rather than recomputing from scratch.

*Call graph*: 2 external calls (execute, select).


##### `record_workspace_usage`  (lines 129–165)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This bills model-token usage that belongs to a workspace but not to a specific turn. It is used for background work where there is no member or agent turn to attach the cost to.

**Data flow**: It receives a database connection, workspace ID, model name, usage counts, and pricing table. It totals the token counts; if the total is zero, it stops. Otherwise it creates a fresh ledger ID, calculates the price, and inserts a ledger row with no turn ID.

**Call relations**: Background jobs call this when they make a real model call outside the normal turn flow. It uses Pricing.micro_usd for the cost and uuid4 for a new ledger row, so each completed provider call becomes its own workspace-level spend record.

*Call graph*: calls 1 internal fn (micro_usd); 3 external calls (execute, insert, uuid4).


##### `record_egress_request`  (lines 168–197)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox egress requests, meaning outbound requests made from a sandbox. These requests are metered as counts, not dollar charges.

**Data flow**: It receives a connection, workspace ID, turn ID, and an amount to add. It builds a stable ledger ID for the egress dimension, then inserts a row or atomically increments the existing row. The priced value is always zero, while the count increases.

**Call relations**: The sandbox egress proxy calls this as requests happen. It uses ledger_id_for so all egress for the same turn lands in the same egress row, and the database upsert prevents concurrent increments from overwriting each other.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_sandbox_tokens`  (lines 200–251)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records model-token usage made from inside a sandbox through the egress proxy. It keeps this separate from the host-side turn token bill so the two sources do not collide.

**Data flow**: It receives a connection, workspace ID, turn ID, model name, usage counts, and pricing table. It totals tokens; if there are none, it returns. Otherwise it calculates the price, builds a stable ledger ID for sandbox tokens, and inserts or increments the row’s token and cost totals.

**Call relations**: The sandbox proxy calls this when in-sandbox model calls burn tokens. It uses Pricing.micro_usd to price the burn and ledger_id_for to group all sandbox token usage for a turn into one accumulating ledger row.

*Call graph*: calls 1 internal fn (micro_usd); 2 external calls (execute, ledger_id_for).


##### `mint_usage_exports`  (lines 276–381)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: This prepares billable usage for an external billing consumer. It freezes new ledger growth into export rows so delivery can be retried without changing the billable item.

**Data flow**: It receives a connection, workspace ID, consumer name, earliest export time, and a function that maps model names to provider key slots. It reads stored workspace credentials, finds the latest already-exported amount per ledger row, then finds ledger rows that have grown and are settled enough to export. For each growth slice, it inserts an export intent with from-and-to amounts, cost, timing, and whether the usage used a workspace-owned key.

**Call relations**: A billing export job calls this before reading pending exports. It reads ledger rows written by the usage-recording functions, consults ledger_export to avoid repeating already-frozen growth, and inserts new ledger_export rows while using database conflict protection so concurrent or replayed mints collapse into one intent.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 384–429)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: This reads usage export records that have been minted but not yet acknowledged. It gives an external sender a stable batch to deliver.

**Data flow**: It receives a connection, workspace ID, consumer name, and maximum number of items. It selects unacknowledged export rows, joins them with the ledger for descriptive fields like dimension, model, and price digest, converts each row into a UsageExport object, and returns them in mint order.

**Call relations**: An export worker calls this after mint_usage_exports. It does not recalculate usage; it reads the frozen export intents, so if a previous delivery was not acknowledged, the same items can be sent again.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 432–456)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This marks exported usage items as delivered after an outside billing system accepts them. Once acknowledged, they stop appearing in pending export reads.

**Data flow**: It receives a connection, workspace ID, consumer name, and the UsageExport items that were successfully delivered. It builds matching conditions from each item’s ledger ID and starting amount, then updates those export rows with an acknowledgement time.

**Call relations**: An export worker calls this only after a successful external API response. It closes the loop started by mint_usage_exports and read_pending_usage_exports: mint, read, send, then acknowledge.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 459–462)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: This finds workspaces that have ever produced ledger usage and are therefore candidates for billing export work. It is intentionally broad rather than trying to predict which workspaces have new usage.

**Data flow**: It builds a database query for distinct workspace IDs in the ledger and passes that query builder into owner_candidates. The result is a WorkspaceCandidates object that a job scheduler can iterate over.

**Call relations**: Usage export scheduling uses this to decide which workspaces to check. The later export steps can cheaply find no pending work, so this function favors a simple candidate list over complicated pre-filtering.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 497–512)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: This decides whether a turn may continue spending under the workspace’s configured caps. It returns allow, park, or reject, where park means wait until a cap is raised and reject means decline the turn.

**Data flow**: It receives a database connection and the amount of pending cost that is about to be added. It reads caps that apply to this workspace/member/agent, caches the “no caps” case if none exist, sums current usage for each cap window, adds the pending cost, and compares the result with each cap’s limit. It returns a SpendDecision with an outcome and, on breach, a user-facing message.

**Call relations**: Admission or mid-turn spending checks call this as the main cap workflow. It delegates to _applicable_caps to find relevant caps, _used_micro_usd to measure current spend, _note_absent_caps for the no-cap shortcut, and _message to explain a breach.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 514–542)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: This fetches the spend caps that apply to the current turn context. A cap can apply to the whole workspace, the specific member, or the specific agent.

**Data flow**: It receives a database connection and reads rows from the spend-cap table for this evaluator’s workspace. It keeps caps whose scope is workspace-wide, whose member subject matches the evaluator’s member, or whose agent subject matches the evaluator’s agent. It returns those rows as SpendCap objects.

**Call relations**: SpendEvaluator.decide calls this first. Its result determines whether spending can skip further cap work, or whether decide must measure usage against each returned cap.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 544–570)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: This calculates how much money has already been spent inside one cap’s rolling time window. It uses the same attribution rules as the caps themselves.

**Data flow**: It receives a connection and a SpendCap. It computes the cutoff time by subtracting the cap’s window length from now. For workspace caps it sums matching ledger rows in the workspace; for member caps it joins ledger rows through turns and conversations to the member; for agent caps it joins ledger rows through turns to the agent. It returns the summed cost in micro-USD.

**Call relations**: SpendEvaluator.decide calls this once for each applicable cap. The returned spend is combined with pending_micro_usd to decide whether that cap still has room.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 572–583)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: This builds the plain message shown when a spend cap is breached. It explains whether the turn was parked or declined and names the tightest cap limit.

**Data flow**: It receives the chosen outcome and the list of breached caps. It picks the breached cap with the smallest dollar limit, converts micro-USD into dollars, and formats a short message. It returns that message as text.

**Call relations**: SpendEvaluator.decide calls this only after it has found one or more breached caps. The message becomes part of the SpendDecision returned to the caller.

*Call graph*: called by 1 (decide).


##### `SpendRollup.read`  (lines 664–740)

```
async def read(self, connection: AsyncConnection, window_seconds: int) -> SpendReport
```

**Purpose**: This builds the workspace-wide spend report for a rolling time window. It gives both the total and useful breakdowns for humans and audits.

**Data flow**: It receives a database connection and a window length in seconds. It computes the cutoff time, then queries the ledger for total priced spend, totals by usage dimension, totals by member, totals by agent, and totals by price digest. It packages those grouped sums into a SpendReport.

**Call relations**: CLI or web reporting code calls this for a workspace admin view. It reads the same ledger populated by the recording functions, and its grouped results are returned as small report data objects ready for rendering.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_agent`  (lines 742–790)

```
async def read_agent(self, connection: AsyncConnection, agent_id: UUID, window_seconds: int) -> AgentSpendReport
```

**Purpose**: This builds a spend report for one agent within a rolling window. It also includes the spend caps that apply specifically to that agent.

**Data flow**: It receives a connection, agent ID, and window length. It joins ledger rows to turns so only rows belonging to that agent are counted, groups spend by dimension, reads agent-scoped cap lines, and returns an AgentSpendReport with the total, breakdown, and caps.

**Call relations**: Agent-facing or member-facing views call this when they need one agent’s slice instead of the whole workspace. It follows the same ledger-to-turn attribution used by cap evaluation, so the report matches what agent caps measure.

*Call graph*: 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup.read_member`  (lines 792–845)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int) -> MemberSpendReport
```

**Purpose**: This builds a spend report for one member within a rolling window. It also includes the spend caps that apply specifically to that member.

**Data flow**: It receives a connection, member ID, and window length. It joins ledger rows through turns and conversations to find that member’s usage, groups it by dimension, reads member-scoped cap lines, and returns a MemberSpendReport with the total, breakdown, and caps.

**Call relations**: Member-facing views call this when a member needs to see their own spending. It uses the same conversation-based attribution that SpendEvaluator._used_micro_usd uses for member caps, so the displayed number and enforced cap are based on the same data.

*Call graph*: 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).

## 📊 State Registers Touched

- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-model-provider-catalog` — The shared list of AI models and providers, including how to call them, what keys they need, and what features they support.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-external-client-pools` — The live reusable HTTP/provider client sessions and connection pools for model and connector calls, including lifecycle cleanup handles.
- `reg-turn-execution-budget-state` — The per-turn live execution limits and counters for context size, tokens, reasoning, tool iterations, cost checks, and stop conditions that gate the model loop before final ledger recording.
