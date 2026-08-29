# Cross-cutting configuration, catalogs, packaging, and import-time modules  `stage-22` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It is not one single startup step or request path. Instead, many parts of the system consult it to know what is available, what is allowed, and where to find things.

Several sub-stages provide Python package markers. These small __init__.py files act like labels on folders, so the main code, app extensions, automation features, provider integrations, and generated protocol code can be imported by name. The nested scaffolding also includes document review constants, which keep shared filenames consistent.

The direct source files provide the system’s rulebooks. model spec defines what facts every AI model record must contain, while the catalog lists the built-in models, their providers, prices, limits, API routes, and key sources. config defines and checks the main ufo.toml deployment settings, stopping early when something important is missing or unsafe. proxy_serve, sandbox cache, and sandbox preview define shared service addresses, allowed providers, database settings, and safe sandbox routing. file_changes sets one common maximum file path length. Together, these pieces make the rest of the system predictable before real work begins.

## Sub-stages

- [Core package import markers](stage-22.1.md) `stage-22.1` — 20 files
- [App extension package markers](stage-22.2.md) `stage-22.2` — 8 files
- [Feature and automation extension package markers](stage-22.3.md) `stage-22.3` — 16 files
- [Integration and provider extension package markers](stage-22.4.md) `stage-22.4` — 11 files
- [Nested extension import scaffolding and script constants](stage-22.5.md) `stage-22.5` — 11 files

## Files in this stage

### Model definitions and catalog
Core model records and the built-in catalog define provider, pricing, routing, limits, and reasoning capabilities for AI model selection and billing.

### `core/src/ufo/models/catalog.py`

`config` · `startup / config load`

This file is like a menu and price sheet for the AI models shipped with the core system. Without it, the system would not have one trusted place to answer basic questions such as: “What provider owns this model?”, “Which API key should be used?”, “How much should this call cost?”, and “How much text can this model safely handle?”

The file defines shared constants for Anthropic and OpenAI models, such as environment variable names for API keys and default context windows. A context window is the amount of text a model can consider at once. It also defines reusable reasoning settings, meaning whether the model can use extra internal thinking and whether that can be combined with tools.

Small helper functions build model descriptions, called `ModelSpec` objects. A `ModelSpec` is the system’s compact record for one model: provider, price, knowledge cutoff, supported API surface, and the function that can create the real client when an API key is available.

The main function, `core_model_specs`, returns the full built-in list. At import time, the file also builds price lookup tables and a pricing digest, which gives the rest of the system a stable way to charge and verify pricing. One important detail is that some OpenAI models can technically accept more text than listed here, but the catalog uses the smaller window where the stated price is still accurate, avoiding underbilling.

#### Function details

##### `_anthropic_client`  (lines 27–28)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates a ready-to-use Anthropic model client for one specific model. It keeps the rest of the system from needing to know the details of how the Anthropic software development kit client is made.

**Data flow**: It receives a model description and an Anthropic API key. It uses the key to create the lower-level Anthropic SDK client, then wraps that client together with the model description in an `AnthropicClient`. The result is a provider-specific client that knows both how to talk to Anthropic and which model settings apply.

**Call relations**: This function is stored inside Anthropic `ModelSpec` records created by `_anthropic`. Later, when the system needs to actually call an Anthropic model, that stored factory function can be used to turn the configured API key into a working client.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 31–32)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates a ready-to-use OpenAI model client for one specific model. It hides the low-level OpenAI client setup behind a simple factory function.

**Data flow**: It receives a model description and an OpenAI API key. It uses the key to create the lower-level OpenAI SDK client, then combines that with the model description in an `OpenAIClient`. The result is a client object prepared to call the chosen OpenAI model with the right settings.

**Call relations**: This function is attached to OpenAI `ModelSpec` records created by `_openai`. When the wider model system later needs to contact OpenAI, it uses this factory to build the actual client from the configured key.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 35–55)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS_WITH_TOOLS) -> ModelSpec
```

**Purpose**: This helper builds the catalog entry for one Anthropic model. It avoids repeating the same provider, API key, context, reasoning, and client-wiring details for every Anthropic row in the catalog.

**Data flow**: It receives the model id, price, knowledge cutoff, API key environment variable name, and optional settings such as context window and reasoning support. It packages those facts into a `ModelSpec` with Anthropic-specific defaults, including the Anthropic provider name, Anthropic key slot, chat API surface, and `_anthropic_client` factory. The output is one complete model record ready to be included in the catalog.

**Call relations**: `core_model_specs` calls this repeatedly while building the built-in Anthropic model list. Each returned `ModelSpec` becomes one entry in the final tuple of core-supported models.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 58–72)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds the catalog entry for one OpenAI model. It gives all OpenAI models consistent defaults while still allowing special cases, such as choosing a different API surface.

**Data flow**: It receives the model id, price, knowledge cutoff, API key environment variable name, and optionally the API surface to use. It creates a `ModelSpec` with OpenAI-specific values: provider name, OpenAI key slot, OpenAI context window, reasoning support, and `_openai_client` as the client factory. The output is one complete OpenAI model record.

**Call relations**: `core_model_specs` calls this for each built-in OpenAI model. The helper hands back `ModelSpec` objects that join the Anthropic entries in the system’s core model catalog.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 75–180)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function returns the complete list of model records that core UFO ships with. It is the single place where built-in model prices, context limits, knowledge cutoffs, and provider routing are declared.

**Data flow**: It receives the names of the environment variables that should supply Anthropic and OpenAI API keys. It creates `ModelPrice` records for each model, passes those along with model facts into `_anthropic` or `_openai`, and returns a tuple of `ModelSpec` objects. Nothing is fetched from the network here; it is building structured catalog data from hard-coded facts and the configured key names.

**Call relations**: This is the central builder for the file. At module load time it is called to create `CORE_MODEL_SPECS`; that result is then used to build `CORE_PRICES`, `CORE_PRICING`, and `PRICE_DIGEST`, which other parts of the system can use for model lookup and billing.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `core/src/ufo/models/spec.py`

`data_model` · `model lookup and request setup`

This file is like the label on a piece of equipment: it tells the rest of the system exactly what a model is capable of and how it should be used. Without this single record, one part of the code might think a model supports reasoning, another might bill it differently, and another might call it through the wrong API.

The main type is ModelSpec, a frozen data record, meaning once it is created its values cannot be changed. Each ModelSpec names the model, its provider, how to build a client for it, its price, its knowledge cutoff date, its context window size, whether it accepts images, which API surface it uses, and where to find its API key. The file also defines ReasoningSupport, a smaller record that explains whether a model can do extended reasoning and whether that reasoning can be used together with tools.

The file checks important promises early. For example, the knowledge cutoff must look like YYYY-MM, and a model cannot claim tool-compatible reasoning if it does not support reasoning at all. It also turns provider authentication failures into a clear credential error that tells the user which key needs replacing. Finally, it decides what reasoning setting should actually be sent on a request, including cases where reasoning must be omitted because the model or tool combination does not allow it.

#### Function details

##### `ReasoningSupport.internal_effort`  (lines 36–39)

```
def internal_effort(self) -> ReasoningEffort
```

**Purpose**: This decides the safest internal reasoning setting for a model before any user request is considered. It returns "off" when reasoning is unsupported or can be disabled, otherwise it returns the model's minimum required reasoning level.

**Data flow**: It reads the ReasoningSupport record's own fields: whether reasoning is supported, whether it can be disabled, and what the minimum effort is. From those facts it produces one reasoning value: either "off" or the declared minimum. It does not change anything.

**Call relations**: This is a small decision helper on the reasoning capability record. It does not hand work off to other functions; it simply gives callers a normalized reasoning choice based on the model's declared abilities.


##### `ModelSpec.__post_init__`  (lines 66–76)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a newly created ModelSpec is internally consistent. It catches bad model registry data early, before the system tries to call or bill a model using impossible settings.

**Data flow**: After a ModelSpec is built, it reads fields such as the knowledge cutoff and reasoning options. If the cutoff is not in YYYY-MM form, or if reasoning-related flags contradict each other, it raises a ValueError. If everything is valid, creation continues and nothing is changed.

**Call relations**: This runs automatically as part of creating a ModelSpec. It does not call other project functions; its role is to stop invalid model descriptions at the door so later request-building code can trust the record.


##### `ModelSpec.key_rejected`  (lines 78–89)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This creates a clear, user-facing credential error for the case where a model provider rejects an API key. Instead of leaving the failure as a vague provider authentication error, it explains which environment variable or workspace key slot may need to be replaced.

**Data flow**: It reads the model's id, provider, key environment name, and workspace key slot. It combines those facts into a detailed message, then creates and returns a CredentialValueInvalid error object. It does not itself contact the provider or change the stored key.

**Call relations**: When some other part of the system knows the provider rejected the key for this ModelSpec, this function packages that fact into the project's standard credential-error type. Its direct handoff is to CredentialValueInvalid, which receives the explanatory message and becomes the returned error.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 91–105)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This decides what reasoning setting, if any, should be sent to the model provider for one request. It protects the system from sending reasoning options that the model cannot accept, especially when tools are also being used.

**Data flow**: It takes the requested reasoning effort and the tuple of tools for the request. It reads the model's reasoning rules, then returns either a concrete reasoning value to send, or None to mean no reasoning setting should be sent at all. If the caller asks for "off" but the model has always-on reasoning that cannot be disabled, it returns the model's minimum effort instead.

**Call relations**: This sits at the point where a high-level request is being translated into provider-specific request data. It does not call other functions; it uses the ModelSpec's stored facts to tell the request-building code whether to include, omit, or adjust the reasoning parameter.


### Deployment service settings
Main deployment and shared service configuration validate required settings such as project safety rules, allowed model providers, and database connectivity.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project’s configuration contract. It says what settings a UFO deployment can have, what defaults are safe to assume, and which combinations are invalid. Without it, different parts of the system would each guess their own settings, and mistakes like a missing blob bucket, an unusable model name, or an unsafe public sandbox URL might only fail later in confusing or dangerous ways.

The file uses Pydantic models, which are Python classes that validate incoming data and turn it into well-shaped objects. Each section of `ufo.toml` has a matching class: database settings, blob storage, model choices, sandbox settings, feature flags, extensions, connectors, and so on. Most classes forbid unknown fields, so a typo in the config is treated as an error instead of being silently ignored.

A few sections also contain extra checks. For example, the database section can derive a companion DBOS system database URL if one is not supplied. The blob section requires either a filesystem root or an S3 bucket depending on the selected backend. The sandbox section carefully checks that public ingress URLs are real address bases, not paths or credentials hidden inside a URL.

At the bottom, `load_config` reads `ufo.toml` or the path named by the `UFO_CONFIG` environment variable, parses TOML, and returns one validated `Config` object for the rest of the system to use.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 40–53)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validator fills in the DBOS system database URL when the operator did not write one explicitly. DBOS is the system store used beside the main application database, so this keeps the common setup simple while still allowing an override.

**Data flow**: It starts with a database config containing `url` and maybe `system_url`. If `system_url` is already set, it leaves everything alone. If it is missing, it looks at the main database URL, creates a sibling database name with `_dbos` added, adjusts the driver name for sync access, stores that derived value back on the config object, and returns the updated object.

**Call relations**: This runs automatically while Pydantic is building a `DatabaseConfig` as part of loading the main `Config`. Other code receives a complete database configuration afterward, so it does not need to repeat the fallback logic.


##### `BlobConfig._backend_complete`  (lines 75–80)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validator checks that the chosen blob storage backend has the information it needs. A filesystem blob store needs a local root folder, while an S3 blob store needs a bucket name.

**Data flow**: It receives a blob config after basic parsing. If the backend is `filesystem`, it checks for `root`; if the backend is `s3`, it checks for `bucket`. When the required field is missing, it raises a clear error. When the config is complete, it returns it unchanged.

**Call relations**: This is called automatically during configuration validation. It protects later storage code from starting with half a setup, such as an S3 backend with nowhere to store objects.


##### `ModelsConfig._models_concrete`  (lines 103–114)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validator makes sure the deploy chooses real model IDs for the places where the system must know exactly what model to call. It rejects empty values and the placeholder value `auto`.

**Data flow**: It receives the model configuration with three model choices: the general automatic model, the ambient reply decision model, and the background jobs model. It checks each one. If any is empty or still set to the special `auto` marker, it raises an error explaining which setting is wrong. Otherwise, it returns the config unchanged.

**Call relations**: This runs when the top-level config is loaded. It means later agent turns and background jobs can rely on these settings being concrete provider model names instead of having to resolve or reject `auto` themselves.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 235–272)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validator checks that the public base URL for sandbox-served sites is safe and usable. It prevents confusing URLs with paths, query strings, fragments, usernames, or passwords, and it requires HTTPS except for local development on `localhost`.

**Data flow**: It receives a sandbox config. If `ingress_public_url` is not set, it returns the config unchanged. If it is set, it splits the URL into parts, checks that it has an allowed scheme and host, allows plain HTTP only for `localhost` or subdomains of `localhost`, and rejects extra URL parts that would make generated site links ambiguous or unsafe. A valid config is returned; an invalid one raises a clear error.

**Call relations**: This runs automatically during config loading. It relies on `urllib.parse.urlsplit` to inspect the URL. Later ingress and surface-link code can then safely build per-site addresses by putting labels in front of the configured host.

*Call graph*: 1 external calls (urlsplit).


##### `config_path`  (lines 419–420)

```
def config_path() -> Path
```

**Purpose**: This small helper decides which configuration file path to use. It lets an operator override the default `ufo.toml` location with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If the variable exists, its value becomes the path. If not, it falls back to `ufo.toml` in the current working directory. It returns that choice as a `Path` object.

**Call relations**: This is called by `load_config` when no path was passed in directly. It is the single place that applies the environment-variable override, so the rest of the config loading path does not need to know that rule.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 423–429)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the deployment configuration file and turns it into a validated `Config` object. It is the main entry point other code uses when it needs the system settings.

**Data flow**: It receives an optional file path. If no path is given, it asks `config_path` for the default or environment-selected path. It then checks that the file exists. If it is missing, it raises a clear `FileNotFoundError`. If it exists, it reads the text, parses it as TOML using `tomllib.loads`, validates the parsed data against the `Config` model, and returns the finished configuration object.

**Call relations**: Startup code calls this to get the deploy settings before the service continues. Inside, it hands off path selection to `config_path` and TOML parsing to Python’s `tomllib`, then lets the Pydantic config classes and validators enforce the detailed rules.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/proxy_serve.py`

`config` · `startup`

This file is a small “front desk” for shared service configuration. Sandboxes are intentionally restricted: they cannot freely call any internet host. To let them call approved AI model providers, the system needs egress rules, meaning rules for allowed outgoing network traffic. `model_rule_base` builds those rules from the deployment’s configured API-key environment variables. If an Anthropic or OpenAI key is present, it derives the matching network permissions and secret-substitution rules. If no supported model key is present, it stops immediately, because a sandbox with no model route could not do useful model work.

The file also defines how the shared ingress service connects to the database. Normally database access may be limited by row-level security, which means the database itself filters rows by tenant or workspace. This shared service instead uses an owner connection string that can bypass those database-level filters, so every query must explicitly filter by workspace. That is powerful and risky, so `owner_dsn` insists that the owner database URL be provided either in an environment variable or in config. It also rewrites the PostgreSQL URL prefix so the async database driver is used. In short, this file keeps the shared services using the same approved model exits and the correct privileged database entrance.

#### Function details

##### `model_rule_base`  (lines 18–37)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic outgoing-network rules that let every sandbox reach configured AI model providers. It uses only providers whose API key is actually present, and it refuses to continue if no model provider is available.

**Data flow**: It receives the project `Config`, reads the configured environment-variable names for Anthropic and OpenAI keys, then asks `deploy_env` for each real secret value. For each key that exists, it asks `derive_model_rules` to turn a model probe name and key into network rules. It combines allowed host names into one `ScopeRule` and returns that plus any additional rules; if no hosts were found, it raises an error instead of returning an unusable rule set.

**Call relations**: During service setup, code that needs the common sandbox egress policy calls this function to get the model-provider part of the rule set. Inside, it relies on `deploy_env` to read deployment secrets and on `derive_model_rules` to know the provider-specific host and key-substitution details, then wraps the combined hosts in a `ScopeRule` for the caller to add to the sandbox policy.

*Call graph*: 3 external calls (__init__, deploy_env, derive_model_rules).


##### `owner_dsn`  (lines 40–52)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Finds the privileged database connection string for the shared ingress service. It makes sure the service uses the owner database role and the async PostgreSQL driver expected by this distribution.

**Data flow**: It receives the project `Config`, first looks for `UFO_OWNER_DSN` in the process environment, and falls back to `config.database.owner_url`. If neither is present, it raises a clear error explaining that this shared service needs an owner-role connection and must scope queries by workspace. If a URL is found, it rewrites a leading `postgresql://` to `postgresql+psycopg://` and returns the adjusted string.

**Call relations**: When the shared ingress service opens its database connection, it calls this function instead of using the ordinary scoped database URL. This function does not hand off to other project helpers; it simply chooses the correct source, validates that it exists, and returns the driver-specific connection string that the database layer can use.


### Sandbox endpoints and limits
Sandbox-facing configuration defines cache and preview service addresses plus shared limits used by file change tracking.

### `core/src/ufo/sandbox/cache.py`

`config` · `startup and sandbox configuration`

Sandboxes often need to download code or packages from the public internet, but doing that directly can be slow and risky. This file supports a safer middle path: a cache service inside the system can fetch and reuse public content, while the sandbox is only pointed at approved public sources. Think of it like a library front desk: users ask for known public books through the desk, rather than wandering into any room they like.

The file names the internal cache host, `cache.ufo.internal`, which the proxy recognizes. It lists Git hosts that may be mirrored through the cache, currently GitHub, and package or download hosts that the proxy may transparently route through the cache for agents allowed to access the internet. The comments are important because they explain the safety boundary: the cache daemon has its own allowlist, so requests cannot be redirected toward private network addresses.

For Git, the file builds configuration entries that rewrite normal fetch URLs so downloads come from the cache, while pushes still go straight to the original host. That matters because reading public source code can benefit from caching, but writing code back should not go through a mirror. Finally, it includes a small parser for the cache daemon address, treating malformed deployment configuration as an error instead of silently disabling the cache.

#### Function details

##### `cache_git_config`  (lines 46–54)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: This function builds the Git configuration entries that make Git fetches use the internal cache for approved hosts. It also adds matching push rules so that pushes still go directly to the real origin instead of the cache.

**Data flow**: It reads the fixed cache host name and the list of Git hosts allowed to be cached. For each host, it creates two text key-value pairs: one that rewrites fetch URLs toward the cache, and one that keeps push URLs pointed at the original host. It returns all of those pairs as an immutable tuple, ready for another part of the system to apply to Git.

**Call relations**: When sandbox or Git setup code needs to prepare an environment that uses the cache, it can call this function to get the exact Git settings. The function does not apply the settings itself; it hands back the configuration so the surrounding setup flow can install it where Git will read it.


##### `parse_cache_daemon`  (lines 57–66)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns an optional deployment setting into a usable cache daemon address. If no cache daemon is configured, it clearly reports that by returning nothing; if the setting is present but malformed, it raises an error so the deployment bug is noticed.

**Data flow**: It receives either a string such as `host:port` or no value at all. If there is no value, it returns `None`. If there is a value, it splits it at the last colon, checks that a host and port are present, converts the port to a number, and returns the host and port together as a pair. A bad shape, such as a missing colon or missing host, becomes a `ValueError` instead of being ignored.

**Call relations**: During deployment or startup configuration, other code can call this function when reading the cache daemon setting. It acts as a gatekeeper before the rest of the system tries to contact the daemon, ensuring later code receives either a clean address or a clear failure.


### `core/src/ufo/sandbox/preview.py`

`config` · `startup / config load`

This file is a shared reference point for the preview feature. The preview service is an internal service that can render shared files or document reads. Sandboxed code is allowed to ask for `preview.ufo.internal`, and the egress proxy, which is the gatekeeper for outbound network traffic, recognizes that special host and forwards the request to the real preview service.

The important safety idea is that the sandbox does not receive the real secret token for preview access. Instead, it uses a harmless placeholder value called a sentinel. When the proxy sees a request to the preview host, it swaps that placeholder for the real deploy token before forwarding the request. This is like giving someone a coat-check ticket instead of the key to the storage room: they can request the item, but they never hold the real key.

The file also contains `parse_preview_service`, which reads the deploy configuration value for the preview service address. That value must look like `host:port`. If the value is missing, the deploy is treated as having no preview service. If the value is present but malformed, the function raises an error immediately, because a bad internal service address is a deployment mistake and should not be silently ignored.

#### Function details

##### `parse_preview_service`  (lines 16–25)

```
def parse_preview_service(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns the preview service setting into a usable host and port pair. It is used when the deploy configuration may or may not include a preview service address.

**Data flow**: It receives either a text value such as `preview-service:443` or `None`. If the value is `None`, it returns `None`, meaning no preview service is configured. If text is provided, it splits it at the last colon, checks that there is a host before the colon, converts the port text into a number, and returns `(host, port)`. If the text does not look like `host:port`, it raises a `ValueError` so the bad deploy setting is noticed right away.

**Call relations**: This function sits at the boundary between deploy configuration and the rest of the preview plumbing. When configuration code needs to understand the preview service address, it can call this function to get a clean `(host, port)` result or a clear failure. The constants in this file then give other parts of the system the matching internal host name and token placeholder used by the proxy and sandbox.


### `core/src/ufo/tools/file_changes.py`

`config` · `cross-cutting`

This file is very small, but it sets an important boundary: file paths used for file-change tracking should not be longer than 4,096 characters. A file path is the text address of a file, like `src/app/main.py` or a much longer nested path. Without a shared limit, different parts of the system might accept different path lengths, which could lead to confusing errors or unsafe assumptions.

The constant `FILE_CHANGE_PATH_MAX_CHARS` acts like a posted height limit on a bridge. Other code can check this value before storing, sending, or processing a changed file path. If a path is too long, the system can reject it or handle it deliberately rather than failing later in a less clear way.

There are no functions or classes here. The file exists to make this rule explicit, named, and reusable. If the allowed path length ever needs to change, it can be updated in one place.

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-database-schema-version` — The database migration state that records which durable tables and columns the running code can rely on.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-egress-network-policy` — The outbound network permission state that decides which external hosts, proxies, and secret injections are allowed for a workspace or agent.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-sandbox-runtime-cache` — Built sandbox client/runtime image and reusable sandbox cache artifacts used when launching isolated execution environments.
