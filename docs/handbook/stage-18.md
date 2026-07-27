# Declarative configuration, packs, and capability manifests  `stage-18` (cross-cutting infrastructure)

This stage is the system’s catalog and instruction sheet. It mostly runs during startup and shared setup, before the assistant begins real work. It tells the runtime what bundles, tools, skills, credentials, outside services, and helper agents exist. Later lifecycle stages decide which of these to actually turn on.

The pack recipes are like pre-packed toolboxes for different jobs, such as a local developer assistant, a hosted assistant, evaluations, or a chief-of-staff assistant. The agent and workflow manifests label the tools inside each extension, such as browser work, coding, documents, research, sites, or brief writing. Platform manifests register shared surfaces like web pages, command-line access, scheduled tasks, alerts, debugging, and optional Redis-based shared state. Provider and connector manifests describe outside doors and keys, including Slack, Bedrock, OpenRouter-style model providers, YC tools, API-key connectors, and login-based app integrations.

At the center, core/src/ufo/config.py loads the deployment configuration from one TOML file, a simple structured settings file. It checks required and safety-sensitive settings early, so the server fails fast instead of starting in a broken or unsafe state.

## Sub-stages

- [Pack recipes and pack package declarations](stage-18.1.md) `stage-18.1` — 10 files
- [Agent, skill, and workflow extension manifests](stage-18.2.md) `stage-18.2` — 6 files
- [Platform surface and runtime extension manifests](stage-18.3.md) `stage-18.3` — 8 files
- [External provider, credential, and connector manifests](stage-18.4.md) `stage-18.4` — 7 files

## Files in this stage

### Declarative configuration, packs, and capability manifests
### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project’s rulebook for `ufo.toml`, the main configuration file. It says which settings exist, what type of value each one must have, which values are optional, and which combinations are not allowed. Without it, each part of the system would have to guess where to find the database, blob storage, model settings, sandbox settings, extension choices, and more. That would make startup fragile and errors would show up later, often during a user request.

The file uses Pydantic models, which are Python classes that validate incoming data. Think of them like a checklist at the door: a setting is either present and well-formed, or the program refuses to continue with a clear error. Most sections forbid unknown extra fields, so a typo in the config does not silently do nothing.

Some sections also fill in safe derived values. For example, if the DBOS system database URL is not given, it is derived from the main application database URL. Other sections check required backend-specific settings, such as needing a filesystem root for filesystem blob storage or a bucket name for S3 blob storage.

At the bottom, `config_path` chooses where to read the config from, and `load_config` reads, parses, and validates it into one `Config` object used by the rest of the application.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 36–49)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the DBOS system database URL when the user did not set one explicitly. It lets common deployments configure only the main database while still giving DBOS its own paired storage location.

**Data flow**: It starts with a database configuration that contains `url` and may or may not contain `system_url`. If `system_url` is already set, it leaves the object unchanged. If it is missing, it splits the main database URL, builds a sibling name ending in `_dbos`, adjusts the driver name for the synchronous DBOS store, writes that into `system_url`, and returns the updated configuration object.

**Call relations**: This is run automatically by Pydantic when a `DatabaseConfig` is built as part of loading the main `Config`. It does not hand off to another project function; its job is to make sure later database setup code receives both the application database URL and the DBOS system-store URL.


##### `BlobConfig._backend_complete`  (lines 73–78)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step checks that the chosen blob storage backend has the minimum setting it needs. It prevents the system from starting with, for example, filesystem storage but no folder to write into.

**Data flow**: It receives a blob configuration after basic fields have been parsed. If the backend is `filesystem`, it checks that `root` is present. If the backend is `s3`, it checks that `bucket` is present. If a required value is missing, it raises a clear error; otherwise it returns the same configuration object.

**Call relations**: Pydantic calls this while constructing `BlobConfig` during overall config loading. Later code that stores transcripts, artifacts, sandbox workspaces, and related files can then rely on the selected backend having its basic required destination.


##### `ModelsConfig._auto_model_concrete`  (lines 93–96)

```
def _auto_model_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step makes sure the deployment pins `auto` model selection to a real model ID. It avoids a circular or empty setting where `auto` would resolve to `auto` instead of to an actual model.

**Data flow**: It receives the model configuration after values have been parsed. It looks at `auto_model`; if the value is empty or literally the special `auto` placeholder, it raises an error. Otherwise it returns the configuration unchanged.

**Call relations**: Pydantic runs this when building `ModelsConfig`, usually inside `load_config`. Agent code can later ask for the deployment’s automatic model choice and receive a concrete backend model name rather than another placeholder.


##### `config_path`  (lines 265–266)

```
def config_path() -> Path
```

**Purpose**: This small helper decides which config file path to use. It checks the `UFO_CONFIG` environment variable first, and otherwise falls back to `ufo.toml` in the current working directory.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If the variable is set, that string becomes the path; if not, it uses the default `ufo.toml`. It wraps the chosen string in a `Path` object and returns it.

**Call relations**: `load_config` calls this when no explicit path was passed in. It delegates path construction to `pathlib.Path`, then hands the resolved path back so the loader can check for the file and read it.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 269–275)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This is the main entry point for reading UFO’s deployment configuration. It finds the config file, refuses to continue if it is missing, parses the TOML text, and validates it into a structured `Config` object.

**Data flow**: It takes an optional path. If a path is provided, it uses that; otherwise it asks `config_path` where to look. It checks whether the file exists, reads its text, parses the TOML into ordinary Python data with `tomllib.loads`, and then asks the `Config` model to validate and organize that data. The result is a ready-to-use `Config` object, or an exception if the file is missing or invalid.

**Call relations**: This function is called during configuration loading, typically as the application starts. It calls `config_path` only when the caller did not name a file, then calls the standard TOML parser. Its output becomes the shared configuration object that the rest of the system uses to connect to databases, blob storage, model providers, sandboxes, extensions, and other services.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).

## 📊 State Registers Touched

- `reg-config` — The effective deployment settings that tell the system how to start, what services to use, and what safety rules are enabled.
- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-model-catalog` — The shared list of available AI models, providers, limits, prices, and client adapters.
- `reg-tool-catalog` — The shared catalog of tools the model may call, including their names, schemas, handlers, and safety properties.
- `reg-egress-policy` — The network access and proxy state that decides which sandbox traffic is allowed, audited, billed, or given injected secrets.
- `reg-skill-inventory` — The built-in and user-created skill folders, metadata, dependencies, and workspace-specific skill records.
- `reg-search-index` — The searchable text chunks, embeddings, and selected index backend used to find relevant stored content.
- `reg-eval-environment-fixtures` — Workspace-scoped fake email and calendar records used by the evaluation environment connectors and tools.
- `reg-agent-runtime-settings` — Persistent non-prompt agent configuration such as selected runtime profile, workflow/tool policy, internet-access setting, and conversation or surface agent bindings.
