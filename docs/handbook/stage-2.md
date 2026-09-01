# Configuration, Pack Selection, and Service Wiring  `stage-2`

This stage is part of startup and behind-the-scenes setup. It decides what kind of UFO system is being launched, checks that the needed settings are safe and present, then turns those settings into real services the rest of the program can use. The main configuration file defines the expected shape of the deploy config, usually from ufo.toml, and stops early if something important is missing. The proxy startup helpers combine that config with environment variables, which are operating-system settings, to create model-provider and database connections.

The pack files are like preset toolboxes. The local assistant pack enables the normal assistant features. The billing variant adds billing for local testing. The hosted assistant pack chooses cloud-ready services and prompts for managed workspaces. The evaluation packs adjust the toolbox for tests: assistant_eval removes live broker dependencies and adds deterministic test tools; DSQA and GDPVal packs offer different mixes of core, search, browser, document, and research tools. The sample pack is a small proof that pack loading and onboarding work end to end.

## Files in this stage

### Deployment Configuration
Defines the deployment configuration contract and turns it into concrete startup settings for model and database services.

### `core/src/ufo/config.py`

`config` · `config load and startup validation`

This file is the rulebook for configuration. A UFO deployment needs many outside pieces: a database, a place to store blobs such as transcripts and artifacts, model names, sandbox settings, browser providers, feature flags, and more. Rather than let each part of the system guess what settings mean, this file gathers them into one typed Config object.

It uses Pydantic models, which are Python classes that validate incoming data. In plain terms, they work like a checklist at startup: every known setting must have the right kind of value, unknown extra fields are rejected, and some fields are filled in with safe defaults. For example, if the database's separate DBOS system-store URL is not given, it is derived from the main database URL. If the blob store says it uses the filesystem, it must also name a root folder. If model settings say "auto" instead of a real model, startup stops because the deploy must pin the actual model.

The file also contains the two small functions that find and read the config file. By default it looks for ufo.toml, but an environment variable can point somewhere else. Without this file, configuration mistakes would show up later as confusing runtime failures, or worse, as unsafe network and sandbox behavior.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 40–53)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the database URL used by DBOS, the system store, when the operator did not set it directly. It keeps the common case simple while still allowing a separate system database when needed.

**Data flow**: It starts with the main database URL and the optional system_url field. If system_url is already present, nothing changes. If it is missing, the function builds a sibling database name by adding _dbos, and also swaps async database driver names for sync-driver names where needed. The result is the same DatabaseConfig object with system_url filled in.

**Call relations**: This runs automatically while Pydantic is building a DatabaseConfig from the loaded configuration. It does not call other project code; it prepares a complete database configuration before the rest of the system starts using it.


##### `BlobConfig._backend_complete`  (lines 75–80)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step checks that the chosen blob storage backend has the information it needs. It prevents the system from starting with a blob store that cannot actually read or write files.

**Data flow**: It receives a BlobConfig after basic fields have been parsed. If the backend is filesystem, it checks that a root folder was provided. If the backend is s3, it checks that a bucket name was provided. If something required is missing, it raises an error; otherwise it returns the unchanged config object.

**Call relations**: This runs automatically during configuration validation. It is part of the startup safety gate before transcript storage, artifact storage, or other blob users are allowed to run.


##### `ModelsConfig._models_concrete`  (lines 103–114)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step makes sure every deploy-level model setting names a real model, not the placeholder value "auto". That matters because agents may ask for "auto", but the deployment must decide what concrete model that means.

**Data flow**: It reads the configured model names for normal agent turns, ambient reply checks, and background jobs. For each one, it rejects empty strings and the special automatic placeholder. If all three are real model IDs, it returns the ModelsConfig object unchanged.

**Call relations**: This runs automatically when model configuration is loaded. It protects later model-calling code from having to guess which provider model to use.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 235–272)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validation step checks that the public base URL for sandbox-served sites is safe and usable. It prevents a deployment from minting broken links or exposing session cookies over unsafe plain HTTP, except for localhost development.

**Data flow**: It starts with sandbox.ingress_public_url. If no URL is set, it allows that. If a URL is set, it breaks it into parts using urlsplit, then checks that it has a host, uses HTTPS unless it is a localhost development address, and contains no path, query string, fragment, username, or password. A valid value is returned as part of the SandboxConfig; an invalid one raises a clear error.

**Call relations**: This runs during configuration validation before sandbox ingress starts. It calls Python's URL-splitting helper so later code can safely build per-sandbox subdomain links from the configured base.

*Call graph*: 1 external calls (urlsplit).


##### `config_path`  (lines 419–420)

```
def config_path() -> Path
```

**Purpose**: This small helper decides where the configuration file should be read from. It lets operators override the default ufo.toml path with the UFO_CONFIG environment variable.

**Data flow**: It reads the process environment for UFO_CONFIG. If that variable is set, it turns its value into a Path object. If it is not set, it uses the default path ufo.toml. The output is a filesystem path object.

**Call relations**: load_config calls this when no explicit path was passed in. It is the single place that applies the environment-variable override for config file location.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 423–429)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the TOML configuration file and turns it into the validated Config object used by the rest of the program. It is the main doorway from a text config file into structured runtime settings.

**Data flow**: It receives an optional path. If no path is given, it asks config_path where to look. It checks that the file exists, reads its text, parses the TOML into ordinary data, and then asks the Config model to validate and assemble all nested settings. The result is a fully checked Config object, or an error if the file is missing or invalid.

**Call relations**: Startup code uses this function when it needs the deploy configuration. Inside, it may call config_path to choose the file and tomllib.loads to parse the TOML text before handing the parsed data to the Config model for validation.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/proxy_serve.py`

`config` · `startup`

This file is a small but important bridge between configuration and the running shared services. Its job is to answer two questions at startup: which outside model-provider hosts may a sandbox contact, and what database connection string should the shared ingress service use?

For model calls, sandboxes are not allowed to freely access the internet. They need explicit egress rules, meaning rules for traffic going out of the sandbox. `model_rule_base` looks at the configured API key environment variable names for Anthropic and OpenAI. If a real key is present in the deployment environment, it asks the egress-rule code to build the rules for that provider. It then combines the allowed host names into one scope rule and keeps any extra rules needed to swap placeholder secrets for real keys on the wire. If no provider key is set, it raises an error early, because otherwise a sandbox would be launched with no usable route to a model.

For database access, `owner_dsn` chooses a privileged database URL. This shared service serves many workspaces, so it cannot rely on ordinary per-workspace database filtering. Instead, it uses an owner connection and expects each query to explicitly filter by workspace. The function also adjusts plain PostgreSQL URLs so they use the async Python driver this service expects.

#### Function details

##### `model_rule_base`  (lines 18–37)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the common outbound network rules that let sandboxes call configured model providers. It fails immediately if no provider API key is available, because then model calls would have nowhere valid to go.

**Data flow**: It takes the application `Config`, reads the configured environment variable names for Anthropic and OpenAI keys, and checks the deployment environment for actual key values. For each key that exists, it asks `derive_model_rules` to produce provider-specific access rules, gathers the allowed host names into one combined `ScopeRule`, and returns that rule plus any extra rules. If no allowed hosts are found, it raises an error instead of returning an unusable rule set.

**Call relations**: This function is used when shared services are being prepared to run sandboxes that need model access. It calls `deploy_env` to read secrets from the deployment environment, hands each available provider key to `derive_model_rules` so provider-specific rules can be made, and creates a `ScopeRule` to describe the final set of hosts the sandbox may reach.

*Call graph*: 3 external calls (__init__, deploy_env, derive_model_rules).


##### `owner_dsn`  (lines 40–52)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Chooses the privileged database connection string used by the shared ingress service. It makes sure the service has an owner-level database URL and rewrites it to use the async PostgreSQL driver expected by this codebase.

**Data flow**: It reads `UFO_OWNER_DSN` from the process environment first, and if that is missing, falls back to `config.database.owner_url`. If neither value exists, it raises a clear startup error. If it finds a URL, it changes a leading `postgresql://` into `postgresql+psycopg://` and returns the adjusted string.

**Call relations**: This function is called during shared service setup, before database connections are opened. It does not call other project helpers; it simply chooses the correct source for the secret, validates that it exists, and hands back the database URL format needed by the async database layer.


### Assistant Packs
Defines the local, billing-enabled, evaluation, and hosted assistant pack variants used to assemble assistant capabilities.

### `packs/assistant_billing/ufo_pack_assistant_billing.py`

`config` · `config load`

Most local assistant setups should not talk to real billing services. Billing uses Metronome, an outside service for tracking usage and charging, so the normal local assistant pack leaves it out. But that creates a gap: the hosted onboarding flow can offer an owner a “Set up billing” action, and without Metronome included there is no local way to prove that action works end to end.

This file fills that gap. It builds a pack named “assistant_billing” by taking the existing assistant pack and adding one extra extension: “metronome”. Think of it like taking a standard toolbox and adding the one special billing tool needed for a particular test job.

The pack keeps the same assistant skills as the regular assistant pack, so it behaves like the normal local assistant in most ways. The important difference is that billing-related pieces are turned on, including billing management and shipping usage or seat information. Because those pieces need real credentials, this pack is deliberately opt-in. A developer must choose it and point it at safe test credentials, such as a Metronome sandbox token and a Stripe test-mode key.

#### Function details

##### `pack`  (lines 24–33)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack definition for the assistant-with-billing setup. This is what tells the system the pack’s name, version, enabled extensions, and assistant skills.

**Data flow**: It starts with constants from this file and skill information from the regular assistant pack. It adds the “metronome” extension to the assistant’s normal extensions, turns each assistant skill name into a SkillSpec pointing at that skill’s path, and returns a Pack object containing the complete local billing-enabled setup.

**Call relations**: When the pack system asks this file for its pack definition, this function assembles it. It creates SkillSpec entries for the assistant skills and then passes the full name, version, extensions, and skills into Pack so the wider system can load this optional development pack.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `startup/config load`

A “pack” is like a pre-packed toolbox. Instead of asking users to enable dozens of separate features one by one, this file gives the system one name, “assistant”, and a fixed list of tools and services that should come with it. That includes user-facing apps such as chat, wiki, tasks, meetings, issues, metrics, and artifacts; assistant abilities such as memory, research, scheduled tasks, documents, code execution, and browser automation; and integration layers such as connectors, MCP, Composio, Pipedream, OpenAI embeddings, Bedrock, and OpenRouter.

The important point is that this pack is meant to run on the project’s own local carrier and index, rather than depending on a managed hosted setup. In plain terms, it describes a self-contained local assistant configuration.

The file is mostly declarative: it names the pack, gives it a version, lists the extensions to activate, and points to a local skills folder. The `pack` function then packages those details into a `Pack` object that the wider system can load. Without this file, selecting the “assistant” pack would not tell the system which features to activate together, so the assistant would come up missing large parts of its expected toolbox.

#### Function details

##### `pack`  (lines 72–78)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the official `Pack` description for the local assistant setup. The system uses this when someone selects the `assistant` pack so it knows which extensions and skills belong to that setup.

**Data flow**: It starts with the constants in this file: the pack name, version, extension list, skills directory, and skill names. It turns each skill name into a `SkillSpec`, which is a small description pointing at that skill’s folder, then puts the name, version, extensions, and skill descriptions into a new `Pack` object. The result is a complete pack definition ready for the rest of the system to load.

**Call relations**: During pack loading, the broader configuration system calls `pack` to get this bundle definition. Inside the function, it hands the extension list to `Pack.__init__` and creates each skill entry through `SkillSpec.__init__`, so the pack object can carry both the named extensions and the local skill path information forward into startup.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/assistant_eval/ufo_pack_assistant_eval.py`

`config` · `startup / pack selection`

A “pack” is a named bundle of extensions that the system can load. This file creates a safer, more predictable pack for automated evaluations, where conversations run in controlled workspaces instead of a real user environment. It reuses most of the regular assistant pack, but deliberately leaves out real broker providers like Composio and Pipedream. Those services normally need real API keys, and in an eval run they would either fail in distracting ways or expose tools that should not be available. In their place, this pack adds evaluation-specific extensions: `eval_env`, which provides fake or deterministic workplace connectors, and `docker`, which lets the sandbox run a conversation’s `/workspace` as a real bind-mounted directory. In everyday terms, this file swaps the assistant’s normal toolbox for an exam-room toolbox: most of the familiar tools are still there, but anything that depends on the outside world is removed, and controlled test tools are added. The main thing this file exports is a `pack()` function, which returns the pack description the larger system can register and load.

#### Function details

##### `pack`  (lines 26–27)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the manifest for the `assistant_eval` pack. The manifest tells the system this pack’s name, version, and which extensions should be available during evaluation runs.

**Data flow**: It reads the file’s constants: the pack name, version, and prepared extension list. It passes those into `Pack`, which turns them into a pack object, and returns that object to whoever is loading packs.

**Call relations**: When the system asks this module what pack it provides, `pack` creates the `Pack` object by calling `Pack.__init__`. That returned object is then used by the pack-loading machinery to make the evaluation-specific extensions visible.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `startup / pack loading`

A “pack” is like a product preset: it says, “turn on these capabilities together.” This file builds the hosted version of the assistant. It includes many assistant features, such as chat, memory, web browsing, code execution, Slack and iMessage surfaces, scheduled tasks, document creation, model providers, billing and metering support, and hosted browser/sandbox infrastructure.

The important difference is that this pack points the assistant toward managed backends. For example, it uses Turbopuffer for the memory search index, Redis for live frame updates, E2B for sandboxed execution, and Browserbase for hosted Chrome browser runs. Without this file, a hosted deployment would not have one clear manifest that says which cloud-backed pieces should be active together.

It also adds a special prompt section for customer questions. That section tells the assistant that when a paying customer asks about the ufo product itself, it should first consult the shipped `customer-onboarding-help` skill instead of guessing or relying on the customer workspace’s own memory. This matters because new workspaces may not yet contain product documentation in memory. The file then combines that hosted-only skill with the regular assistant skills from the base assistant pack.

#### Function details

##### `pack`  (lines 99–112)

```
def pack() -> Pack
```

**Purpose**: Creates and returns the complete hosted assistant pack definition. The system uses this to know the pack’s name, version, enabled extensions, available skills, and extra prompt instructions.

**Data flow**: It starts with constants defined in this file: the pack name, version, extension list, hosted skill directory, hosted skill names, and the customer-question prompt section. It turns each hosted skill name into a `SkillSpec`, also adds the skills from the base assistant pack, then wraps everything into a `Pack` object. The result is a single manifest object that the rest of the system can load.

**Call relations**: When the pack loader asks this module for its pack, this function assembles the answer. It calls `SkillSpec.__init__` to describe each skill folder and `Pack.__init__` to build the final pack manifest that will be activated by the hosted assistant configuration.

*Call graph*: 2 external calls (__init__, __init__).


### Evaluation and Sample Packs
Provides reusable pack definitions for DSQA, GDPVal, and sample end-to-end pack validation scenarios.

### `packs/dsqa_eval/ufo_pack_dsqa_eval.py`

`config` · `startup/config selection`

A pack is like a labeled toolkit: it has a name, a version, and a list of extensions that should be available together. This file creates three DSQA evaluation toolkits of increasing capability. The core pack includes the basic pieces for indexing, OpenAI embeddings, and OpenRouter model access. The search pack adds tools for web-style research, including Perplexity and research support. The browser pack builds on the search pack and adds browser and Chrome sandbox support, so evaluations can interact with web pages more directly.

The important idea is layering. Each larger pack reuses the smaller pack’s extension list and adds more tools. That keeps the definitions consistent: if the shared base extensions change, all three packs inherit that change automatically. Without this file, other parts of the system would need to know the exact extension names and combine them by hand, which would make setup more error-prone.

Each function returns a `Pack` object, which is the manifest object the UFO system uses to describe one bundle. The functions do not run the extensions themselves; they simply describe what should be included when that pack is selected.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest DSQA evaluation pack. Someone would use this when they need the basic indexing, embedding, and model-routing tools but do not need search or browser features.

**Data flow**: It reads the fixed core pack name, version, and core extension list from this file. It puts those values into a new `Pack` object and returns that object to the caller.

**Call relations**: When another part of the system asks for the core DSQA pack, this function builds it by calling `Pack.__init__`. It hands over the pack name, version, and extension list so the manifest layer can represent that bundle.

*Call graph*: 1 external calls (__init__).


##### `search_pack`  (lines 17–18)

```
def search_pack() -> Pack
```

**Purpose**: Creates a DSQA evaluation pack with search and research tools included. Someone would use this when the evaluation needs to look up information beyond the local index or base model tools.

**Data flow**: It reads the fixed search pack name, version, and search extension list. That list includes the base extensions plus search-related additions, then the function returns a new `Pack` object containing those choices.

**Call relations**: When the system needs the search-enabled DSQA pack, this function calls `Pack.__init__` to turn the stored constants into a manifest object. The returned pack can then be used by the broader UFO setup process.

*Call graph*: 1 external calls (__init__).


##### `browser_pack`  (lines 21–22)

```
def browser_pack() -> Pack
```

**Purpose**: Creates the most capable DSQA evaluation pack in this file, adding browser support on top of the search tools. Someone would use this when evaluations need to open or inspect web pages through a browser environment.

**Data flow**: It reads the browser pack name, version, and browser extension list. That list starts with the search-capable extensions and adds browser and sandboxed Chrome support, then the function returns a `Pack` object describing the full bundle.

**Call relations**: When the system needs a browser-enabled DSQA pack, this function calls `Pack.__init__` with the prepared constants. It hands the completed pack description back to the caller so the chosen extensions can be loaded together.

*Call graph*: 1 external calls (__init__).


### `packs/gdpval_eval/ufo_pack_gdpval_eval.py`

`config` · `config load`

This file is like a menu of toolkits for running GDPVal evaluation work. Instead of making every user remember a long list of extension names, it groups them into clear choices. A Pack is a small manifest object from the UFO software development kit that says: “this bundle has this name, this version, and these extensions.”

The file starts by naming shared pieces. All packs use the same version number and the same base extensions: a default index, OpenAI embeddings, and OpenRouter access. Then it defines two optional groups. One group adds document and coding-style tools, such as document support, a REPL (an interactive command prompt), and coding support. The other group adds research and browser-style tools, such as Perplexity, research helpers, a browser, and a Chrome sandbox.

The four functions each build one pack. The core pack is the smallest useful base. The documents pack adds document-oriented tools. The research pack adds web and research-oriented tools. The full pack combines all of them. Without this file, someone setting up GDPVal evaluation would need to manually assemble these extension combinations, which is easier to get wrong and harder to standardize.

#### Function details

##### `core_pack`  (lines 13–14)

```
def core_pack() -> Pack
```

**Purpose**: Creates the smallest GDPVal pack. It is for cases where the system only needs the shared base extensions and not the extra document or research tools.

**Data flow**: It starts with the fixed core name, version number, and base extension list stored in this file. It passes those values into Pack, which returns a Pack object describing that bundle. Nothing else is changed.

**Call relations**: When another part of the system wants the basic GDPVal setup, it can call this function. The function hands the name, version, and extension list to Pack.__init__, which builds the actual manifest object.

*Call graph*: 1 external calls (__init__).


##### `documents_pack`  (lines 17–22)

```
def documents_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for document-heavy work. It includes the shared base tools plus extensions for documents, an interactive REPL, and coding support.

**Data flow**: It reads the fixed document pack name, version, base extensions, and document extensions from this file. It joins the base and document extension lists together, then gives that combined list to Pack. The result is a Pack object for the document-focused bundle.

**Call relations**: When setup code needs GDPVal with document and coding capabilities, it can call this function. This function prepares the combined extension list and hands it to Pack.__init__ to create the manifest.

*Call graph*: 1 external calls (__init__).


##### `research_pack`  (lines 25–30)

```
def research_pack() -> Pack
```

**Purpose**: Creates a GDPVal pack for research and browsing work. It includes the shared base tools plus extensions for research, web access, and a sandboxed browser environment.

**Data flow**: It takes the fixed research pack name, version, base extensions, and research extensions defined at the top of the file. It combines the base and research extension lists, then passes the result into Pack. The output is a Pack object describing the research-focused bundle.

**Call relations**: When another part of the system needs GDPVal with research tools enabled, it can call this function. The function builds the right extension combination and delegates the manifest construction to Pack.__init__.

*Call graph*: 1 external calls (__init__).


##### `full_pack`  (lines 33–38)

```
def full_pack() -> Pack
```

**Purpose**: Creates the largest GDPVal pack, with every extension group included. It is the convenient choice when the user wants both document tools and research tools available.

**Data flow**: It reads the fixed full pack name, version, base extensions, document extensions, and research extensions. It combines all three extension groups into one list, then gives that to Pack. The output is a Pack object for the complete GDPVal bundle.

**Call relations**: When setup code wants the most complete GDPVal environment, it can call this function. This function gathers all extension groups and passes them to Pack.__init__, which creates the final manifest object.

*Call graph*: 1 external calls (__init__).


### `packs/sample_pack/ufo_pack_sample.py`

`config` · `config load and onboarding`

This is a conformance sample: a deliberately simple pack that exercises the same public path a real installed pack would use. A pack is a bundle of extensions, skills, and setup steps that the system can load together. Here, the pack is named `sample_pack`, has version `0.1.0`, bundles the `sample` extension, adds one skill from a folder on disk, and adds one onboarding step.

The important point is that this file only imports from `ufo.sdk`, which is the public surface outside pack authors are expected to use. That makes it a useful safety check: if this file stops working, the public pack interface may have been broken.

The onboarding step is intentionally small but meaningful. When run, it writes `{"pack_onboarded": true}` into the pack's scoped store under the key `pack:onboarded`. That store is durable project data, not just a pretend test log. So tests can later read the value back through the same public system path that normal code uses. In everyday terms, this pack is like a sample appliance plugged into every socket in the wall: if one socket is wired wrong, the sample reveals it.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the pack's onboarding action. It records that the pack has been onboarded by writing a small marker into the pack's persistent store.

**Data flow**: It receives an `ExtensionContext`, which is the pack's access point to system services such as storage. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into `ctx.store`. It returns nothing, but it changes durable stored data so later checks can confirm the onboarding step really ran.

**Call relations**: This function is not called directly in this file. Instead, `pack` wraps it inside an `OnboardingStep`, and the wider onboarding system calls it later when that step is executed.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: This is the public entry point the pack loader uses to discover what this pack provides. It builds and returns a `Pack` description containing the bundled extension, skill, and onboarding step.

**Data flow**: It reads the constants in this file, including the pack name, version, bundled extension name, skill folder path, and onboarding step name. It turns the skill folder into a `SkillSpec`, turns `_setup` into an `OnboardingStep`, and combines everything into a `Pack` object. The returned `Pack` is the complete description the loader needs.

**Call relations**: When the system loads this installed pack, it calls `pack` to get the pack description. Inside that description, `pack` creates a `SkillSpec` for the sample pack skill, creates an `OnboardingStep` that points to `_setup`, and then creates the final `Pack` object that the loader can activate.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-search-provider-catalog` — The common search and page-fetching service state used when the system needs outside web information.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-database-connection-pool` — The shared SQLAlchemy engine/session and connection-pool state used by requests, turns, workers, migrations, and persistence helpers to access the database safely.
- `reg-redis-service-pool` — The shared Redis client/connection and stream/cache coordination state used by live turn streaming, workers, and Redis-backed extension stores.
- `reg-sandbox-template-build-cache` — The local Docker image and E2B template build/version state that sandbox launch code relies on to create compatible runtimes.
- `reg-evaluation-fixture-backends` — Deterministic fake connector/backend data for evaluation packs, such as mailbox, calendar, code-search, and business records used across test routes and tools.
