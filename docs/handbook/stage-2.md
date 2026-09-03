# Configuration, pack selection, and feature gates  `stage-2`

This stage is early startup and shared support. Before the system discovers and loads extensions, it first reads the rules that say how this deployment should behave. `core/src/ufo/config.py` defines the expected shape of the main `ufo.toml` settings file and rejects missing or invalid settings early, so problems are caught before real work begins. `core/src/ufo/proxy_serve.py` then helps services turn those settings and environment variables into clear connection rules for model providers and the database.

Next, pack composition chooses bundled sets of abilities, like picking a prepared toolkit. Different packs load different mixes of extensions, skills, prompts, onboarding steps, or safe evaluation tools for local development, hosted production, testing, or examples.

Finally, feature flags provide small on/off or choice switches. The core flag reader gives the rest of the system safe defaults if the flag service fails. The Flagship extension connects those reads to Cloudflare’s flag service and lets operators adjust behavior without redeploying. Together, these parts decide what the system is allowed and prepared to load.

## Sub-stages

- [Pack composition](stage-2.1.md) `stage-2.1` — 7 files
- [Feature flag reads and operator flag writes](stage-2.2.md) `stage-2.2` — 2 files

## Files in this stage

### Deploy configuration
Configuration helpers define and validate deployment settings, then translate them into safe startup connection rules.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project's configuration gatekeeper. It says what settings exist, what type each setting must have, what defaults are safe, and which combinations are not allowed. Without it, the service could start with missing database details, an unusable blob store, an unsafe public URL, or vague model choices that only fail later in harder-to-debug places.

The file uses Pydantic models, which are Python classes that validate incoming data and turn it into structured objects. Think of it like a checklist at an airport: the raw `ufo.toml` file is the suitcase, and these models inspect it before it is allowed onto the plane.

The top-level `Config` object gathers many smaller sections: database, blob storage, model choices, sandbox settings, observability, extensions, feature flags, and more. Most sections forbid unknown keys, so a typo in the config is rejected instead of silently ignored. Some sections also fill in sensible derived values, such as a separate DBOS system database URL based on the main database URL. Others enforce safety rules, such as requiring a filesystem root for filesystem blob storage, a bucket for S3 blob storage, concrete model names instead of `auto`, and a secure address for public sandbox ingress.

At the bottom, `load_config` finds the config file, reads TOML text, parses it, and validates it into a `Config` object used by the rest of the application.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 41–54)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the DBOS system database URL when the operator did not write one explicitly. DBOS is the system store used alongside the main application database, so this makes the common paired-database setup work with less repeated configuration.

**Data flow**: It starts with a `DatabaseConfig` that always has the main database `url` and may or may not have `system_url`. If `system_url` is already present, it leaves everything alone. If it is missing, it takes the main database name, creates a sibling name ending in `_dbos`, and also adjusts async driver names to the sync driver names expected by DBOS. The same config object comes out, now with `system_url` filled in.

**Call relations**: This is called automatically by Pydantic while building or validating a `DatabaseConfig`, usually as part of creating the top-level `Config` in `load_config`. It does not call other project functions; it prepares database settings so later startup code can use one complete database configuration.


##### `BlobConfig._backend_complete`  (lines 76–81)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step checks that the chosen blob storage backend has the one piece of information it cannot work without. Filesystem storage needs a local root folder, while S3 storage needs a bucket name.

**Data flow**: It receives a `BlobConfig` with a selected `backend` and related fields. If the backend is `filesystem`, it checks that `root` is set. If the backend is `s3`, it checks that `bucket` is set. If the required field is missing, it raises a clear error; otherwise it returns the unchanged config object.

**Call relations**: Pydantic runs this while validating blob settings from the config file. Its job is to stop `load_config` from returning a configuration that blob-storage code could not actually use.


##### `ModelsConfig._models_concrete`  (lines 104–115)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step makes sure the deploy pins real model names instead of leaving important model choices vague. The system allows agents to say `auto`, but the deployment configuration must decide what `auto` really means.

**Data flow**: It receives a `ModelsConfig` containing model IDs for normal agent turns, ambient reply decisions, and background jobs. It checks each one for two bad cases: empty text, or the special placeholder value `auto`. If any model is not concrete, it raises an error; otherwise it returns the same validated config.

**Call relations**: Pydantic runs this when model settings are loaded. It protects later model-calling code from having to guess which provider model to use, and it makes bad deploy configuration fail at startup instead of during a user request or background job.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 236–272)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validation step checks that the public base URL for sandbox-served sites is safe and simple enough for the rest of the system to use. A sandbox site can carry a member's session, so public HTTP URLs and malformed bases are rejected.

**Data flow**: It starts with a `SandboxConfig`. If `ingress_public_url` is not set, it accepts the config because no public ingress address is being configured. If it is set, it parses the URL with `urlsplit`, checks that it has a host, requires HTTPS unless the address is a local-only address accepted by `plain_local`, and rejects paths, queries, fragments, usernames, or passwords. The output is either the same config object or a clear validation error.

**Call relations**: Pydantic runs this during sandbox config validation. Inside the check, it hands the URL to Python's URL parser so it can inspect the parts, and to `plain_local` so local development URLs can be allowed safely. Later ingress and link-building code can then rely on this value being just a scheme and host, not a surprising full URL with hidden extras.

*Call graph*: 2 external calls (plain_local, urlsplit).


##### `config_path`  (lines 419–420)

```
def config_path() -> Path
```

**Purpose**: This function decides where the application should look for its main config file. It lets an operator override the default `ufo.toml` path with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that variable is present, its value becomes the path; otherwise it uses the default `ufo.toml`. It wraps the chosen text in a `Path` object and returns it.

**Call relations**: `load_config` calls this when no explicit path was passed in. This keeps the path-selection rule in one small place, so config loading and command-line tools can share the same default behavior.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 423–429)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function loads the deploy configuration from disk and turns it into a validated `Config` object. It is the main doorway from a plain TOML file into the structured settings the program uses.

**Data flow**: It receives an optional path. If no path is given, it asks `config_path` where to look. It checks that the file exists, and if it does not, raises a clear `FileNotFoundError` telling the operator to create `ufo.toml` or set `UFO_CONFIG`. If the file exists, it reads the text, parses it as TOML with `tomllib.loads`, and asks Pydantic to validate the result as a `Config`. The output is a complete typed configuration object, or an exception explaining what is wrong.

**Call relations**: Startup or setup code calls this when it needs the application's settings. It calls `config_path` for the default location and `tomllib.loads` to turn TOML text into normal Python data, then hands that data into the config models in this file so all defaults and validation rules run before the rest of the system starts.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/proxy_serve.py`

`config` · `startup / config load`

This file is a small but important bridge between configuration and the services that run the system. Its first job is to decide which outside model providers a sandbox is allowed to contact. A sandbox is an isolated place where code runs, so it should not be able to call any random internet host. The file looks for configured API keys, uses those keys to derive the allowed model-provider routes, and refuses to continue if no provider key is available. That “fail loud” behavior matters because otherwise a sandbox would start with no way to reach a model and later fail in a more confusing place.

Its second job is to provide the database connection string for shared ingress services. These services serve many workspaces from one process, so they use an owner database role that bypasses row-level security, meaning database-level rules that normally hide other tenants’ rows. Because that is powerful, callers are expected to filter every query by workspace explicitly. The helper reads the owner database URL from an environment variable first, then from config, and fails clearly if neither exists.

One subtle detail is that the database URL is rewritten to use the async PostgreSQL driver shipped with this project, so the rest of the service opens the connection in the expected non-blocking way.

#### Function details

##### `model_rule_base`  (lines 18–37)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the base set of outbound network rules that let sandboxes reach configured model providers, such as Anthropic or OpenAI. It only enables a provider when that provider’s API key is present, and it stops startup if no usable provider is configured.

**Data flow**: It takes the main Config object as input and reads the configured environment-variable names for model API keys. For each key that is actually present in the deployment environment, it asks the egress-rule code to turn a model probe and key into concrete allow-rules. It combines the allowed host names into one scope rule, keeps any extra rules, and returns them as a tuple. If no hosts are found, it raises an error instead of returning an empty rule set.

**Call relations**: During service setup, this helper is used to create the common model-provider egress base that sandbox traffic will rely on. It calls deploy_env to read secrets from the deployment environment, then hands each available key to derive_model_rules so provider-specific routing details stay in the egress-rule subsystem. It creates a ScopeRule at the end to package the final allowed hosts for the caller.

*Call graph*: 3 external calls (__init__, deploy_env, derive_model_rules).


##### `owner_dsn`  (lines 40–52)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string for the shared service’s owner database role. This is the connection used when one service process must serve many workspaces and therefore cannot rely on per-workspace database credentials alone.

**Data flow**: It takes the Config object as input, then first checks the UFO_OWNER_DSN environment variable. If that is not set, it falls back to the owner_url value in the database config. If neither exists, it raises a clear startup error. If it finds a URL, it rewrites the beginning from postgresql:// to postgresql+psycopg:// so the async PostgreSQL driver is selected, and returns the rewritten string.

**Call relations**: This helper is called when the shared ingress side needs to open its database connection. It does not open the connection itself; it simply supplies the correctly chosen and correctly shaped connection string. The surrounding service code is then responsible for using that owner connection carefully, including filtering work by workspace.

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-feature-flags` — The shared on/off switches and rollout choices that let operators change behavior without redeploying.
- `reg-pack-composition` — The selected bundle of built-in extensions, prompts, skills, jobs, and setup steps for this deployment.
- `reg-model-catalog` — The shared list of available AI models, their abilities, providers, prices, and credential needs.
- `reg-egress-policy` — The network access rules that decide which outside hosts sandboxed or connector code may contact.
- `reg-egress-policy-cache-generation` — Workspace egress-rule generation counters and proxy cache-invalidation state for refreshed network access decisions.
- `reg-model-client-pools` — Shared outbound model/provider client sessions, connection pools, retry state, and provider-side rate-limit/backoff buckets used across turns.
