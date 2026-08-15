# Cross-cutting configuration, catalogs, packs, credentials, and model/provider metadata  `stage-23` (cross-cutting infrastructure)

This stage is the system’s shared settings and catalog layer. It is read mainly at startup, so mistakes are caught before real requests run, but its information is used everywhere afterward. It acts like the label board and key cabinet for the rest of the machine.

The main configuration file defines what a deployment is allowed to look like and loads it from one TOML settings file, which is a simple structured text format. A small file-change setting gives all code one shared path-length limit. The model files work together to describe, collect, and expose AI model choices: the model “spec” defines the standard record for a model, the catalog lists built-in models and how to call them, the registry turns all model definitions into one clear lookup table, and the catalog skill lets users ask what models are available. The Bedrock extension adds Amazon Bedrock models in the same format. The extension store manages adding or removing extension pins from the lockfile. The keyed connectors extension defines API-key based services, including which secrets to request and where they may safely be sent.

## Files in this stage

### Deployment settings and limits
Core deployment configuration and shared constants establish validated startup settings and common limits used elsewhere.

### `core/src/ufo/config.py`

`config` · `config load and startup validation`

This file is the project's configuration rulebook. A deployment is expected to provide one `ufo.toml` file, or point to one with the `UFO_CONFIG` environment variable. The file is parsed into typed Pydantic models, which means each section has known fields, defaults, and validation rules. Unknown fields are rejected, so a typo in configuration does not silently do nothing.

The main `Config` class gathers all the smaller sections: database, blob storage, model choices, sandbox settings, extension settings, artifact signing, live-frame hub, terminal transport, browser provider, connectors, research, and packs. Think of it like a checklist at the front door: before the app is allowed inside, each required item is checked.

Some sections also fill in safe defaults. For example, the database section can derive a DBOS system database URL from the main application database URL. Other sections refuse unsafe ambiguity. Blob storage must specify the filesystem root or S3 bucket depending on the chosen backend. Model names must be real concrete model IDs, not the placeholder `auto`. Public sandbox ingress URLs must be plain HTTPS host bases, because later code builds per-site subdomains from them and depends on secure cookies.

Without this file, many failures would happen later and more mysteriously: missing storage paths, unusable model settings, malformed public URLs, or configuration typos would only show up during requests or background jobs.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 38–51)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the DBOS system database URL when the operator did not set one explicitly. It keeps the main application database and the DBOS system store paired by convention, while still allowing a custom system store when needed.

**Data flow**: It starts with the configured main database URL and an optional system database URL. If `system_url` is already present, it leaves the configuration unchanged. If it is missing, it splits the main URL, builds a sibling database name ending in `_dbos`, adjusts the driver name for SQLite or PostgreSQL, writes that back into `system_url`, and returns the completed database configuration.

**Call relations**: Pydantic calls this method while creating a `DatabaseConfig`, which happens as part of validating the full `Config`. Nothing else needs to remember the naming rule; later code can simply read `database.system_url` and trust that it is set.


##### `BlobConfig._backend_complete`  (lines 73–78)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step makes sure the chosen blob storage backend has the information it needs. Filesystem storage needs a local root directory, while S3 storage needs a bucket name.

**Data flow**: It receives a blob configuration with a selected backend and optional fields. If the backend is `filesystem` but no `root` is set, it raises an error. If the backend is `s3` but no `bucket` is set, it raises an error. Otherwise it returns the same configuration as valid.

**Call relations**: Pydantic runs this during `BlobConfig` creation inside the larger config load. It prevents storage code later in the program from discovering halfway through an operation that it has nowhere to read or write blobs.


##### `ModelsConfig._models_concrete`  (lines 95–102)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step prevents deployment configuration from using the placeholder model name `auto` where a real model must be pinned. It makes the deployment, not the agent definition, decide which concrete model is used.

**Data flow**: It reads `auto_model` and `ambient_reply_model` from the model configuration. If either value is empty or equals the special placeholder `AUTO_MODEL`, it raises a clear error. If both are concrete model IDs, it returns the configuration unchanged.

**Call relations**: Pydantic runs this when the models section is built. Later turn-taking and ambient-reply logic can rely on these fields naming actual model backends instead of having to resolve or reject `auto` at the point of use.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 182–206)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validation step checks that the public sandbox ingress URL is safe and usable as a base for per-sandbox website addresses. It rejects values that would produce broken links or insecure browser sessions.

**Data flow**: It begins with the optional `ingress_public_url`. If it is not set, it accepts the configuration. If it is set, it splits the URL into parts, then requires HTTPS and a real host. It also rejects paths, query strings, fragments, usernames, and passwords, because later code only expects a clean scheme-and-host base such as `https://example.com`. It returns the configuration if the URL passes these checks, or raises a descriptive error if not.

**Call relations**: During sandbox configuration validation, this method calls `urllib.parse.urlsplit` to inspect the URL. It protects two later users of the setting: the ingress server that maps hostnames back to sandbox sites, and link-building code that places a site label in front of the configured host.

*Call graph*: 1 external calls (urlsplit).


##### `config_path`  (lines 332–333)

```
def config_path() -> Path
```

**Purpose**: This function decides where the configuration file should be read from. It uses the `UFO_CONFIG` environment variable when present, otherwise it falls back to `ufo.toml` in the current working directory.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If the variable exists, its value becomes the path string; if not, the default `ufo.toml` is used. It wraps that string in a `Path` object and returns it.

**Call relations**: The main config loader calls this when no explicit path is passed in. This keeps the path-selection rule in one small place instead of spreading environment-variable checks throughout the program.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 336–342)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the deployment configuration file and turns it into a validated `Config` object. It is the normal entry point for code that needs the application's settings.

**Data flow**: It accepts an optional path. If no path is provided, it asks `config_path` where to look. It checks that the file exists and raises a clear `FileNotFoundError` if it does not. If the file exists, it reads the text, parses the TOML content with `tomllib.loads`, and asks Pydantic to validate the result as a `Config`. The output is a fully checked configuration object, or an exception explaining what is wrong.

**Call relations**: Startup code calls this to load configuration before the rest of the system is assembled. It delegates path choice to `config_path` and TOML parsing to the standard library, then all section-level validators run as part of building the final `Config`.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/tools/file_changes.py`

`config` · `cross-cutting`

This file is tiny, but it still has a clear job: it sets a safety limit for file path strings. File paths can come from the operating system, user actions, tools, or generated data. If a path is unexpectedly huge, it can waste memory, make logs hard to read, or cause problems in code that assumes paths are a reasonable size.

The constant `FILE_CHANGE_PATH_MAX_CHARS` is set to 4,096 characters. Think of it like a sign on a doorway saying “items wider than this cannot pass.” Other parts of the project can refer to this value when deciding whether a file path is acceptable for file-change tracking.

Keeping the limit in one file matters because it avoids disagreement. If different parts of the system each invented their own maximum path length, one part might accept a path that another part later rejects. With this shared constant, the project has a single source of truth for this rule.


### Model provider catalogs
Built-in and extension-provided model catalogs describe available providers, pricing, context windows, credentials, and client construction rules.

### `core/src/ufo/models/catalog.py`

`config` · `startup and model/pricing lookup`

This file is like the project’s official menu of built-in AI models. When the rest of the system needs to know “Can I use this model?”, “How much should we charge for its tokens?”, or “Which API key and provider client does it need?”, this is the source of truth for the core models.

It defines shared facts first: the environment variable names for Anthropic and OpenAI API keys, standard context window sizes, and whether these models support reasoning with tools. A context window is the maximum amount of text a model can consider at once.

Small helper functions then build model descriptions. For Anthropic models, `_anthropic` fills in Anthropic-specific details; for OpenAI models, `_openai` fills in OpenAI-specific details. Both produce `ModelSpec` objects, which are plain records describing one model and how to call it. Separate client helper functions know how to turn a model spec plus an API key into a usable provider client.

The main function, `core_model_specs`, returns the full tuple of built-in model specs. It also contains important billing choices. For example, some OpenAI models can technically accept more text than the catalog advertises, but above a threshold the provider charges at a different rate. This file uses the smaller safe window so the system does not undercount cost. At import time, it also builds price lookup tables and a pricing digest so other parts of the system can quickly stamp ledger entries with consistent prices.

#### Function details

##### `_anthropic_client`  (lines 24–25)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates a ready-to-use Anthropic model client for one model. It connects the generic model description to the real Anthropic software client that sends requests to Anthropic’s service.

**Data flow**: It receives a `ModelSpec`, which describes the model, and an API key string. It uses the key to create the underlying Anthropic SDK client, then wraps that SDK client together with the model spec in an `AnthropicClient`. The result is an object the rest of the system can use to make Anthropic calls for that specific model.

**Call relations**: This function is stored inside Anthropic `ModelSpec` entries as the recipe for building a live client later. When some later part of the system has chosen an Anthropic model and has an API key, this recipe calls `anthropic_sdk_client` to create the provider connection and then hands it to `AnthropicClient`.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 28–29)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates a ready-to-use OpenAI model client for one model. It is the OpenAI counterpart to `_anthropic_client`.

**Data flow**: It receives a `ModelSpec` and an API key string. It uses the key to create the underlying OpenAI SDK client, then combines that SDK client with the model spec inside an `OpenAIClient`. The output is the client object the system can use to send requests to OpenAI for that model.

**Call relations**: This function is placed into OpenAI `ModelSpec` entries as their client-building recipe. Later, when the system needs to talk to an OpenAI model, this recipe calls `openai_sdk_client` and wraps the result in `OpenAIClient` so higher-level code does not need to know the provider-specific setup steps.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 32–51)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This helper builds the catalog entry for one Anthropic model. It saves repeated setup so each Anthropic model can be listed with only the details that differ, such as name, price, cutoff date, and context size.

**Data flow**: It takes a model id, pricing information, a knowledge cutoff date, the name of the API-key environment variable, and optionally a context window. It combines those with fixed Anthropic facts: the provider name, the Anthropic client-building function, the key slot, chat API surface, and reasoning support. It returns a complete `ModelSpec` for that Anthropic model.

**Call relations**: `core_model_specs` calls this repeatedly while building the built-in Anthropic section of the catalog. `_anthropic` does not create a network client immediately; instead, it stores `_anthropic_client` in the spec so a client can be created later only when needed.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 54–68)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds the catalog entry for one OpenAI model. It keeps OpenAI-specific defaults in one place so the model list stays readable and consistent.

**Data flow**: It takes a model id, pricing information, a knowledge cutoff date, the API-key environment variable name, and optionally which OpenAI API surface to use. It combines those inputs with fixed OpenAI facts: the provider name, standard context window, OpenAI client-building function, key slot, and reasoning support. It returns a complete `ModelSpec` for that OpenAI model.

**Call relations**: `core_model_specs` calls this for each built-in OpenAI model. For GPT-5.6 models, the caller passes the `responses` API surface because those models need a different OpenAI endpoint for legal requests involving reasoning and tools.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 71–173)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds and returns the full list of built-in model descriptions for core UFO. It is the central place where model names, provider routing, prices, knowledge cutoff dates, and context limits are declared.

**Data flow**: It receives the environment variable names that should be used for Anthropic and OpenAI API keys. It creates `ModelPrice` objects for each model, passes them into `_anthropic` or `_openai`, and returns a tuple of finished `ModelSpec` entries. Nothing is sent to a provider here; it only creates the catalog records that later code can consult.

**Call relations**: At the bottom of the file, this function is called once with the default API-key environment names to create `CORE_MODEL_SPECS`. Those specs then feed the core price map, the pricing table, and the price digest. In normal use, this catalog is available as soon as the module is imported, so model selection and billing can rely on one consistent set of facts.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `extension load and model selection`

This extension is like a directory card for Amazon Bedrock Mantle models. The rest of the system needs to know which models exist, how much they cost, how much text they can accept, what API style they use, and how to connect to them. Without this file, UFO would not be able to discover or call these Bedrock-hosted models through its normal provider system.

The file does not translate prompts or responses itself. Instead, it builds `ModelSpec` objects, which are records describing each model. Some model IDs are Anthropic models, so they use UFO’s shared `AnthropicClient`. Others are OpenAI-compatible models, so they use UFO’s shared `OpenAIClient`. This keeps the Bedrock extension small: it only supplies Bedrock-specific details such as the endpoint URL, the AWS region, and the credential slot.

A key detail is region selection. Bedrock needs an AWS region, so the file reads `AWS_REGION` first, then `AWS_DEFAULT_REGION`. If neither is set, it stops with a clear error. The API key is expected from `AWS_BEARER_TOKEN_BEDROCK`, and the manifest advertises that credential need to the host system.

At the end, `manifest()` returns the public package description: provider name, version, credential requirement, and the tuple of supported model specs.

#### Function details

##### `bedrock_region`  (lines 42–48)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock should use. Bedrock endpoints are regional, so calls cannot be built safely unless this value is known.

**Data flow**: It reads the process environment, first looking for `AWS_REGION` and then for `AWS_DEFAULT_REGION`. If it finds a value, it returns that region string. If both are missing, it raises an error telling the user which environment variables to set.

**Call relations**: The Anthropic and OpenAI client builders call this before creating their Bedrock endpoint connection. It supplies the region used in the Anthropic Bedrock client setup and in the OpenAI-compatible base URL.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 51–63)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds a UFO Anthropic client for Bedrock-hosted Anthropic models. It connects UFO’s standard Anthropic request path to Amazon’s Bedrock Mantle Anthropic endpoint.

**Data flow**: It receives a model description and an API key. It asks `bedrock_region` for the AWS region, creates an Anthropic Bedrock Mantle async client with that key, region, no automatic retries, and a fixed timeout, then wraps it in UFO’s `AnthropicClient` together with the model spec. The result is a ready-to-use client object for that model.

**Call relations**: This function is stored inside Anthropic model specs through `_anthropic`. Later, when the system needs to call one of those models, the spec can use this builder to create the actual client connection.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 66–73)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds a UFO OpenAI-style client for Bedrock-hosted models that speak an OpenAI-compatible API. It chooses the correct Bedrock Mantle URL shape for either Chat Completions-style or Responses-style requests.

**Data flow**: It receives a model description and an API key. It reads the Bedrock region, chooses a base URL based on the model’s `api_surface`, creates an OpenAI SDK client pointed at that URL, and wraps it in UFO’s `OpenAIClient`. The result is a ready-to-use client object for that model.

**Call relations**: This function is attached to OpenAI-compatible model specs through `_openai`. It relies on `bedrock_region` for the regional endpoint and hands off actual SDK setup to `openai_sdk_client`.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 76–94)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Creates a complete model description for an Anthropic model available through Bedrock. This avoids repeating the same provider, credential, reasoning, and API settings for every Anthropic model entry.

**Data flow**: It receives a model ID, pricing information, knowledge cutoff date, and optionally a context window size. It combines those with Bedrock’s provider name, credential slot, environment variable, Anthropic client builder, reasoning support, and chat API setting. It returns a `ModelSpec` describing that model.

**Call relations**: The file uses this helper while building `BEDROCK_MODEL_SPECS`. The resulting specs later appear in the extension manifest, where the host system can discover them.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 97–111)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates a complete model description for an OpenAI-compatible model available through Bedrock. It packages the details UFO needs to price, display, and call that model.

**Data flow**: It receives a model ID, pricing information, knowledge cutoff date, context window size, and API surface name. It adds the shared Bedrock provider name, credential settings, reasoning support, and OpenAI client builder. It returns a `ModelSpec` for that model.

**Call relations**: The file uses this helper while building `BEDROCK_MODEL_SPECS` for GPT-style Bedrock models. Those specs are then exposed through `manifest()` so the wider system can offer and call them.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 172–183)

```
def manifest() -> Manifest
```

**Purpose**: Returns the extension’s public description to UFO. This is how the host system learns the provider name, version, required Bedrock credential, and available models.

**Data flow**: It creates a credential slot describing the Bedrock API key requirement, combines it with the extension name, version, and all model specs, and returns a `Manifest` object. It does not read the key itself; it only declares what credential is needed.

**Call relations**: The extension loader calls this when discovering the provider. The returned manifest hands the host system the model catalog built earlier in the file and the credential information needed before clients can be created.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/models/spec.py`

`data_model` · `model registry setup and per-turn request preparation`

This file is like an ID card format for every AI model supported by the project. Without it, different parts of the system could disagree about the same model: one part might call the wrong API, another might bill the wrong price, and another might assume a feature is available when it is not. The main type, `ModelSpec`, is a frozen data record, meaning once a model description is created it cannot be changed by accident. It says who provides the model, how to build a client for calling it, what it costs, how much text it can fit in one request, when its training knowledge ends, whether it supports extra “reasoning” work, and where to find its API key. `ReasoningSupport` is a smaller record that says whether reasoning exists at all, and whether it can be used together with tools. The file also validates important promises early. For example, the knowledge cutoff must look like `YYYY-MM`, and a model cannot claim it supports reasoning with tools if it does not support reasoning in the first place. It also turns a rejected provider key into a clear project-level credential error, and decides when requested reasoning must be turned off because the chosen model or request shape cannot support it.

#### Function details

##### `ModelSpec.__post_init__`  (lines 56–64)

```
def __post_init__(self) -> None
```

**Purpose**: This runs right after a `ModelSpec` is created to catch impossible or malformed model descriptions immediately. It protects the rest of the system from discovering bad model data later during a user request.

**Data flow**: It reads the newly created model record, especially its `knowledge_cutoff` and `reasoning` settings. It checks that the cutoff date is written as year and month, such as `2024-06`, and checks that tool-based reasoning is not enabled unless reasoning itself is enabled. If everything is valid, nothing changes; if not, it raises a `ValueError` with a specific explanation.

**Call relations**: This is called automatically by Python's frozen dataclass machinery when a `ModelSpec` is constructed. It does not hand work to other project functions; its job is to stop bad registry entries before they can reach routing, prompting, or billing code.


##### `ModelSpec.key_rejected`  (lines 66–76)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This turns a provider's rejected API key into a clear credential error that the rest of the system can understand. Someone would use it when a model provider returns an authentication failure, meaning the key being used is not accepted.

**Data flow**: It reads the model's ID, provider name, environment-variable key name, and workspace bring-your-own-key slot. It builds a human-readable message saying which possible key locations should be replaced. It returns a `CredentialValueInvalid` error object containing that message.

**Call relations**: When a provider signals a 401-style key rejection, this method is the model-specific way to describe the problem. It hands off to `CredentialValueInvalid.__init__` to create the typed credential fault, so higher-level code can report the failure as a replaceable bad key instead of a vague stream or provider error.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.default_reasoning`  (lines 78–88)

```
def default_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort
```

**Purpose**: This decides what reasoning effort should actually be sent to the model. It prevents the system from asking for reasoning in cases where the selected model, or the combination of model plus tools, cannot support it.

**Data flow**: It receives the requested reasoning level and the tools included in the request. It reads the model's reasoning capabilities. If the model does not support reasoning, it returns `off`; if tools are present but this model cannot combine tools with reasoning, it also returns `off`; otherwise it returns the requested reasoning level unchanged.

**Call relations**: This method is used during request preparation, after a model has been chosen and before the API call is made. It does not call other functions; it acts as a small gatekeeper that turns model capability facts into the safe reasoning setting for the outgoing request.


### Model registry and discovery
The model registry consolidates model specifications and exposes them through a user-facing catalog skill.

### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a documentation problem: the system needs to tell users what models are available, but that list must not become stale or disagree with what the runtime actually uses. Instead of keeping a hand-written model list, this file turns the live model registry into a readable skill at startup. The registry is the source of truth for routing, pricing, and model behavior, so the catalog is like a restaurant menu printed directly from the kitchen’s current inventory.

The main output is a RuntimeSkill, which is a piece of runtime-readable skill content. Its instructions contain a Markdown table. Each row describes one registered model: its id, provider, knowledge cutoff, context window, input and output price per million tokens, whether it supports reasoning, and which API surface it uses.

A small helper formats prices from the system’s internal unit, micro-dollars, into normal dollar text. The catalog builder sorts models by id so the table is stable and easy to scan. It also creates a raw Markdown version with front matter, so the skill has both structured fields and the original skill text. Without this file, users could still run models, but they would lack a trustworthy, automatically updated guide for choosing between them.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns an internal price number into a human-readable dollar price per million tokens. It exists so the model catalog shows prices in a form people can compare easily.

**Data flow**: It receives a price stored as micro-dollars per million tokens, where a micro-dollar is a very small accounting unit. It divides that number by the constant that represents one dollar, formats the result with two decimal places, and returns text like "$1.25".

**Call relations**: The catalog builder calls this helper while creating each model’s table row. It keeps the price-formatting detail out of the larger table-building flow.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the complete model-catalog RuntimeSkill from the current ModelRegistry. Someone would use it at boot time to publish an accurate, readable list of the models this deployment can actually run.

**Data flow**: It takes a ModelRegistry, reads all of its model specifications, sorts them by model id, and turns them into a Markdown table. For each model it includes provider, knowledge cutoff, context window, prices, reasoning support, and API surface. It then wraps that table in a RuntimeSkill object and returns it, without changing the registry.

**Call relations**: This is the main builder in the file. As it formats rows, it calls _per_mtok to convert stored prices into dollar text. At the end, it hands the finished name, description, instructions, and raw Markdown to RuntimeSkill.__init__ so the rest of the system can load the catalog as a normal runtime skill.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `core/src/ufo/models/registry.py`

`domain_logic` · `startup and per-model request handling`

This file is the “front desk” for model selection. Other parts of the system should not have to guess which provider a model belongs to, how it is priced, or where its API key comes from. They ask the registry instead.

At startup, `model_registry` gathers the built-in model definitions and any model definitions contributed by extension manifests. It puts them into one table keyed by exact model id. If two entries claim the same id, startup fails immediately. This is important because silent overlap could send a request to the wrong provider or charge the wrong price.

The registry also checks that the configured default model and ambient reply model are real registered models. That turns a typo in configuration into an early, clear error instead of a confusing failure halfway through a user interaction.

During normal use, `ModelRegistry` answers questions like: “what model does `auto` mean here?”, “what specification belongs to this id?”, “which bring-your-own-key slot is used?”, and “how do I build a client for this model?” When building a client, it looks up the right API key from the current workspace first, falling back to the configured environment variable. It also rejects keys with non-ASCII characters, because provider network protocols cannot safely carry them.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name `auto` into the actual default model configured for this deployment. If the caller already gave a specific model id, it leaves it unchanged.

**Data flow**: It receives a model name. If that name is the shared `auto` marker, it reads the registry’s configured `auto_model` value and returns that real model id. Otherwise, it returns the original model name with no change.

**Call relations**: This is used by `ModelRegistry.model_key_env` before checking which environment variable is needed. That way, onboarding checks the key for the model that will really run, not the placeholder word `auto`.

*Call graph*: called by 1 (model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This looks up the full specification for a model id. If the id is unknown, it raises a clear error right here instead of letting different parts of the system fail later in different ways.

**Data flow**: It receives a model id and reads the registry’s `specs` table. If the id exists, it returns the matching `ModelSpec`, which contains facts such as provider, pricing, key requirements, and client builder. If the id is missing, it raises a `ValueError` that names the unknown id.

**Call relations**: `ModelRegistry.client_for` calls this before building a provider client, and `ModelRegistry.model_key_env` calls it before deciding which key setting applies. It is the common checkpoint that keeps model facts consistent across the rest of the system.

*Call graph*: called by 2 (client_for, model_key_env).


##### `ModelRegistry.client_for`  (lines 44–69)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This creates the actual client object used to talk to the provider for a chosen model. It also finds the correct API key at the moment the model is used, so workspace-specific keys and rotated platform keys are honored without restarting.

**Data flow**: It receives a model id, looks up that model’s specification, and checks whether the model needs a key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the credential, using either the model’s bring-your-own-key slot or its configured environment variable. If no key is set, it raises a clear runtime error. If the key contains characters that cannot be sent safely as plain ASCII text, it raises `CredentialValueInvalid`. Otherwise, it passes the specification and key into the model’s client builder and returns the resulting `ModelClient`.

**Call relations**: When some higher-level model-running code needs to make a provider call, it comes here to get the right client. This function first relies on `ModelRegistry.spec` for the model facts, then asks `ws_current` for the active workspace so it can fetch the right credential. It hands off the final construction to the client factory stored in the model specification.

*Call graph*: calls 1 internal fn (spec); 2 external calls (__init__, ws_current).


##### `ModelRegistry.key_slot_for`  (lines 71–78)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This answers which workspace bring-your-own-key slot would be used for a model, if any. It is deliberately gentle: unknown or keyless models return `None` instead of raising an error, which is useful for reporting old billing rows or platform-served usage.

**Data flow**: It receives a model id and checks the registry’s `specs` table without requiring the id to exist. If there is no matching specification, or the specification has no workspace key slot, it returns `None`. If the specification names a key slot, it returns that slot name.

**Call relations**: This function is a lookup helper for flows that need to label model usage rather than run the model. Unlike `spec`, it does not fail loudly, because historical records may mention models that are no longer registered.


##### `ModelRegistry.model_key_env`  (lines 80–90)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding code which environment variable must be set before a selected model can run. It only returns eager environment-variable checks for the core providers it knows about, and returns `None` for extension-provided models whose key rules are resolved later.

**Data flow**: It receives a model name and the application configuration. First it resolves `auto` to the real configured model. Then it looks up that model’s specification and reads its provider. If the provider is Anthropic, it returns the configured Anthropic API key environment variable name. If the provider is OpenAI, it returns the configured OpenAI API key environment variable name. For any other provider, it returns `None`.

**Call relations**: This function calls `ModelRegistry.resolve` so it checks the real model behind `auto`, then calls `ModelRegistry.spec` to inspect the model’s provider. It supports startup or onboarding checks that want to warn about missing core provider keys before the first turn runs.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 93–120)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the active `ModelRegistry` for the whole process. It combines built-in model definitions with extension-provided ones, checks for configuration mistakes, and prepares the shared pricing table.

**Data flow**: It receives the application configuration and a tuple of extension manifests. It asks `core_model_specs` for the built-in model specifications, then walks through those plus every model listed by each manifest. Each specification is inserted into a dictionary under its model id. If an id appears twice, it raises a clear error. It then verifies that `models.auto_model` and `models.ambient_reply_model` in the configuration both name registered models. Finally, it builds a pricing table from each model’s price data and returns a frozen `ModelRegistry` containing the specs, pricing, and configured automatic model.

**Call relations**: This is the construction step used during setup. It pulls built-in models from `core_model_specs`, folds in manifest-contributed models, asks `pricing_from` to turn the gathered price facts into a usable pricing object, and then creates the `ModelRegistry` that later request-time code uses for lookup and client creation.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### Extensions and keyed credentials
Extension catalog management and keyed connector metadata govern installable integrations and the API-key secrets they require.

### `core/src/ufo/ext/store.py`

`domain_logic` · `extension command handling`

UFO extensions are Python packages that can be switched on by recording them in a lockfile. Think of the catalog as a menu of available add-ons, and the lockfile as the project’s signed order slip: it says exactly which add-ons should be loaded, at which version, and with which content digest. This file connects those two ideas.

It defines the shape of a catalog entry, including whether an extension is disabled. A disabled extension is not installable through the normal store command; it is meant only for bundling. It also defines a search result that says both what the catalog offers and whether that extension is already pinned in the lockfile.

The main class, ExtensionStore, works over one catalog and one lockfile. Search reads the current pins so it can mark results as installed. Install first checks that the requested name is in the catalog and is not disabled, then verifies that the Python environment actually has that extension installed. Only then does it create a pin containing the extension’s version and digest, and writes it to the lockfile. Remove does the reverse: it refuses to remove something that is not pinned, then rewrites the lockfile without it.

Without this file, command-line extension install and remove actions would not have a safe place to enforce catalog rules or keep the loader’s lockfile up to date.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads a catalog file from disk and turns it into a checked Catalog object. This gives the rest of the extension store a trusted list of extension names, versions, and disabled flags.

**Data flow**: It takes a filesystem path. It reads the file text, parses it as TOML, which is a simple configuration-file format, and validates the parsed data against the Catalog shape. The result is a Catalog object ready for searching or installing from.

**Call relations**: This is the doorway from a stored catalog file into the in-memory store logic. It relies on the path object to read the file and on tomllib to understand the TOML text; later, an ExtensionStore can use the returned catalog to answer searches and decide whether installs are allowed.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Looks up the installed version of the UFO package itself. The lockfile needs this as an anchor so it records which UFO version the extension pins belong with.

**Data flow**: It takes no inputs from the caller. It asks Python’s package metadata system for the version of the package named “ufo” and returns that version string.

**Call relations**: ExtensionStore._write calls this when it needs to create a new lockfile and there is no existing UFO version to preserve. In that moment, this function supplies the version label that will be written alongside the extension pins.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an extension that is already installed in the current Python environment. It refuses to pin a catalog name if the actual package cannot be found, which prevents the lockfile from promising an extension the loader could not load.

**Data flow**: It receives an extension name. It asks the extension loader what extensions have been discovered in the current environment, finds the matching one, reads its manifest version, calculates a digest of its source entry, and returns an ExtensionPin containing the name, version, and digest. If the name was not discovered, it raises an error instead of returning a fake pin.

**Call relations**: ExtensionStore.install calls this after catalog checks pass. The function hands install a trustworthy pin, using loader discovery and digest calculation, so install can write that pin into the lockfile.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and marks each matching result as already installed or not. This is what lets a user-facing command show both “available” and “currently pinned” information in one list.

**Data flow**: It receives a search string. It first reads the current lockfile pins through _pins, turns them into a set of pinned names, then walks through the catalog entries whose names contain the query. For each match it creates a StoreListing with the catalog details and an installed flag based on the lockfile.

**Call relations**: This is the read-only path through ExtensionStore. It calls _pins to learn the current installed state, then creates StoreListing results for the caller to display or process.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins an extension into the lockfile after checking that it is allowed and actually present in the environment. This is the main “install from the store” action.

**Data flow**: It receives an extension name. It looks for that name in the catalog, rejects it if missing, and rejects it if the catalog marks it disabled. It then asks pin_for to create a verified pin. Finally, it reads the existing pins, replaces any older pin with the same name, writes the updated pin list, and returns the new pin.

**Call relations**: This function is the store’s main write path for adding extensions. It calls pin_for for the proof of what should be pinned, calls _pins to keep the other existing pins, and calls _write to save the new lockfile contents.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. It refuses to do anything if the named extension is not currently pinned, so users get a clear error instead of a silent no-op.

**Data flow**: It receives an extension name. It reads the current pins, checks whether any pin has that name, and raises an error if none do. If the name is present, it filters that pin out and writes the shorter pin list back to the lockfile. It does not return a value.

**Call relations**: This is the reverse of install. It calls _pins to inspect the current lockfile and _write to save the lockfile after the requested extension has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the extension pins currently stored in the lockfile. If the lockfile does not exist yet, it treats that as an empty installed list.

**Data flow**: It uses the ExtensionStore’s lockfile path. If the file exists, it reads and parses the lockfile, then returns its extensions tuple. If the file is missing, it returns an empty tuple.

**Call relations**: Search, install, and remove all call this when they need the current state before making a decision. It is the shared “what is pinned right now?” helper for the store.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete new set of extension pins to the lockfile while preserving the existing UFO version anchor when possible. This keeps lockfile updates consistent whether the file already exists or is being created for the first time.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. It checks whether the lockfile already exists; if so, it reads the current UFO version from it, and if not, it asks ufo_version for the installed UFO version. It then builds a Lockfile object with that version and the given pins, and writes it to disk.

**Call relations**: Install and remove call this after they have decided the final pin list. This function takes their in-memory decision and hands it off to the loader’s lockfile writer so the change becomes persistent.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / extension manifest load`

Some outside services cannot be connected through a broker like Composio or Pipedream because the user already owns the API key and no broker creates it. This file is the safe “catalog” for those services. Each provider is described as a row: its name, the secret values it needs, the HTTP header each secret belongs in, and the API host that is allowed to receive it.

The key safety idea is that the sandbox does not receive the real secret. Instead, it receives a harmless placeholder value, called a sentinel. When the sandbox makes an outgoing request to the approved host, the egress proxy replaces that sentinel with the real key on the wire. It is like giving someone a locked envelope to mail: they can send it to the right address, but cannot read what is inside.

The file also supports providers whose API host depends on the customer’s region, such as Datadog. In that case, the user chooses from a fixed list of known hosts. This matters because it prevents a secret from being sent to a hostname typed by a user or invented by an agent.

At the end, `manifest` packages these credential slots and the human-facing instructions into a `Manifest`, which is how the rest of the system learns that these keyed connectors exist.

#### Function details

##### `KeyedProvider.__post_init__`  (lines 69–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a provider row is well-formed as soon as it is created. A provider must either have one fixed API host or a fixed menu of possible hosts, but not both and not neither.

**Data flow**: It reads the fields already placed on the `KeyedProvider`: `host`, `sites`, `host_env`, and `site_description`. If the host setup is inconsistent, it stops immediately by raising an error. If everything is valid, it returns nothing and leaves the provider definition unchanged.

**Call relations**: This runs automatically after each `KeyedProvider` row is built. It protects later code, such as `target_host` and `slots`, from having to guess what kind of host rule the provider meant to declare.


##### `KeyedProvider.target_host`  (lines 81–90)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This turns the provider’s host rule into the form used by credential injection. For a simple provider it returns one fixed hostname; for a region-based provider it builds a `HostChoice`, which is a controlled list of allowed hostnames plus the slot used to store the member’s choice.

**Data flow**: It reads the provider’s `host`, `sites`, `provider`, `site_description`, and `host_env` fields. If there is no site list, it outputs the fixed host string. If there is a site list, it creates and outputs a `HostChoice` containing the choice slot name, description, allowed hosts, default host, and environment variable name.

**Call relations**: The `slots` method asks this property where each secret is allowed to go before creating injection rules. The `usage` method also asks it how to show the correct curl example, using either a fixed host or the host environment variable.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 92–110)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This converts one provider row into the credential slots the system can ask the user to fill. Each slot says what secret is needed, where it may be injected, what header it belongs in, and what sentinel environment variable the sandbox will see.

**Data flow**: It starts with the provider’s declared secrets and host rule. For each secret, it creates a `CredentialSlot` with an `InjectionTarget`, including the approved host, HTTP header name, sentinel name, sandbox environment variable, and request dimension. If the provider has selectable hosts, it also adds a separate credential slot for the host choice. The result is a tuple of credential slot definitions.

**Call relations**: The top-level `manifest` function gathers the slots from every provider by calling this method. Inside the method, `InjectionTarget` describes the safe wire-level swap, and `CredentialSlot` describes the user-fillable piece of configuration.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 112–122)

```
def usage(self) -> str
```

**Purpose**: This writes a short instruction line showing how an agent should call the provider’s API from the sandbox. It names the credential slots and gives a sample `curl` command using environment variables instead of real secrets.

**Data flow**: It reads the provider name, label, secrets, headers, environment variable names, and host rule. It builds a human-readable string that lists the slot names and shows a sample HTTPS request. If the host is selectable, it includes the host-choice slot and uses the host environment variable in the example.

**Call relations**: This is used while building the prompt section text for the extension. Its output becomes part of the instructions that explain to the agent how keyed providers differ from broker-connected tools.


##### `manifest`  (lines 188–194)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s public declaration to the rest of the system. It returns a `Manifest`, which tells the platform the extension name, version, credential slots, and prompt text to install.

**Data flow**: It reads the constants in this file, walks through all `KEYED_PROVIDERS`, asks each provider for its credential slots, and wraps those slots together with a `PromptSection` containing the explanatory text. It outputs a complete `Manifest` object.

**Call relations**: The extension loader calls this function when it needs to discover what the keyed connectors extension provides. The function hands off provider-specific slot creation to `KeyedProvider.slots`, then uses `Manifest` and `PromptSection` to package the result for the wider system.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The deployment’s active settings, such as enabled services, limits, paths, providers, and safety options.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-extension-store` — Per-workspace extension pins and extension-owned settings saved so enabled add-ons survive restarts.
- `reg-model-catalog` — The lookup table of available AI models, providers, routing details, capabilities, and pricing metadata.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-search-index` — The searchable text chunks, embeddings, and backend index state used to find relevant documents and pages.
- `reg-connector-auth-flow-state` — Short-lived OAuth, consent-link, CSRF/state, and callback progress for connecting external accounts before durable connections and grants exist.
