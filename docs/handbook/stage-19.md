# Configuration, adapters, utilities, and conformance scaffolding  `stage-19` (cross-cutting infrastructure)

This stage is shared support used across the whole system, especially during startup and testing. It is the wiring and labeling layer that tells UFO what settings to use, what outside services it can talk to, and where different extension code lives.

The configuration file loader reads ufo.toml and checks that important choices are clear and safe before the program runs. The bundle builder creates a repeatable Docker deployment package, freezing which extensions and settings should be used on another machine. Adapter manifests and registries act like a front-desk directory, mapping provider names to the code for Redis hubs, source connectors, models, search, sandbox, browser, and related services.

The evaluation pack definitions provide safe, repeatable tool bundles for tests, replacing real services with controlled fake ones when needed. The sample extension and pack scaffolding proves that the public SDK can load tools, skills, routes, hooks, connectors, and setup actions correctly. The many package marker files are small but important: they make folders importable in Python, like labels on drawers, so later stages can find core code, platform integrations, and user-facing extensions.

## Sub-stages

- [Adapter manifests and provider registries](stage-19.1.md) `stage-19.1` — 2 files
- [Evaluation pack definitions](stage-19.2.md) `stage-19.2` — 3 files
- [Sample extension and pack conformance scaffolding](stage-19.3.md) `stage-19.3` — 3 files
- [Core, control, and pack import package markers](stage-19.4.md) `stage-19.4` — 15 files
- [User-facing capability extension package markers](stage-19.5.md) `stage-19.5` — 16 files
- [Platform and integration extension package markers](stage-19.6.md) `stage-19.6` — 12 files

## Files in this stage

### Deployment configuration and bundling
Builds reproducible UFO deployment bundles around a validated ufo.toml configuration.

### `core/src/ufo/bundle.py`

`domain_logic` · `bundle creation / packaging time`

This file supports the `ufoctl bundle` command. A bundle is like packing a lunchbox before a trip: it copies the needed config, writes down the exact extension versions and checksums to use, and prepares instructions for building a container image. Without this step, a deployment could accidentally run with different extensions, missing extensions, or changed extension contents when moved to another environment.

The main class, `Bundle`, takes the current config file, an optional extension catalog, and an output folder. When `build` runs, it first decides which extensions must be pinned. A “pin” means a recorded name and digest, where the digest is a fingerprint proving the installed extension is exactly the expected one. If a lockfile already exists, the bundle starts from those already-pinned extensions. If not, it pins every discovered installed extension. If a catalog is available, it also includes extensions marked as bundle-only: these are disabled during normal store install, but intentionally included when creating a bundle.

After that, the file creates the output directory, copies the config to `ufo.toml`, writes a fresh `ufo.lock`, and writes a Dockerfile. The Dockerfile installs a local `ufo` wheel into a small Python image, copies in the config and lockfile, and starts `ufoctl serve` by default.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: Builds the filename of the local `ufo` Python wheel that the Dockerfile will install. A wheel is a packaged Python distribution, and here it is expected to exist beside the Docker build context rather than being downloaded from a public package index.

**Data flow**: It reads the current `ufo` version from the extension store version helper, places that version into the standard wheel filename pattern, and returns the resulting string. Nothing is written or changed.

**Call relations**: The Dockerfile text generator calls this when it needs to name the wheel in the `COPY`, `pip install`, and cleanup commands. This keeps the Dockerfile tied to the same `ufo` version that the bundle records elsewhere.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: Creates the complete bundle folder on disk. It gathers the extension pins, copies the deploy config, writes a new lockfile, writes a Dockerfile, and returns a summary of what it produced.

**Data flow**: It starts with the `Bundle` object's config path, optional catalog, and output directory. It asks `_pins` for the exact extensions to freeze, creates the output folder if needed, copies the config text into `ufo.toml`, writes `ufo.lock` with the current `ufo` version and extension pins, writes the Dockerfile text from `_dockerfile`, and returns a `BundleResult` containing the created paths and pins.

**Call relations**: This is the main action method for the file. It calls `_pins` first so the lockfile reflects the intended extension set, then calls `_dockerfile` so the Docker build context knows how to run that locked deployment. Other code can call `build` as the single high-level operation for producing a bundle.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Chooses and verifies the extensions that must be frozen into the bundle. This protects the bundle from naming an extension that is not actually installed in the current environment.

**Data flow**: It reads the installed extensions, checks where the current lockfile should be, and then chooses a base list: existing lockfile entries if a lockfile exists, or all discovered installed extensions if not. If a catalog is available, it adds catalog entries marked disabled, because those are treated as bundle-only additions. It removes duplicate names while keeping order, asks the store to create a verified pin for each name, and returns the pins as a tuple.

**Call relations**: The `build` method calls this before writing the lockfile. `_pins` relies on extension loader helpers to see what is installed and what is already locked, and on `pin_for` to turn each chosen extension name into a digest-checked pin.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: Creates the text of the Dockerfile used to build the runnable container image. The Dockerfile installs the local `ufo` wheel, copies in the frozen config and lockfile, and sets the container to run `ufoctl serve` by default.

**Data flow**: It uses fixed bundle filenames and the wheel filename from `wheel_name` to assemble Dockerfile lines. The result is one string ending with a newline; it does not write the file itself.

**Call relations**: The `build` method calls this after preparing the config and lockfile paths, then writes the returned text to `Dockerfile`. It calls `wheel_name` so the Dockerfile refers to the exact wheel filename expected for the current `ufo` version.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project’s configuration contract. It says, in one place, what settings a UFO deployment must provide: database connection strings, blob storage, model choices, sandbox behavior, browser transport, connector options, extension settings, and more. Think of it like the checklist a building inspector uses before letting the system open for business.

The file uses Pydantic models, which are Python classes that validate incoming data. Each section of `ufo.toml` maps to one small class, such as `DatabaseConfig`, `BlobConfig`, or `ModelsConfig`. These classes reject unknown fields, so a typo in the config does not silently do nothing. Some classes also fill in safe derived values. For example, if the DBOS system database URL is not written explicitly, `DatabaseConfig` derives a sibling database name from the main application database URL.

The top-level `Config` class gathers all sections into one complete object. Many optional subsystems have defaults, while core pieces like `database` and `blob` must be present. At the bottom, `config_path` decides where the config file lives, using the `UFO_CONFIG` environment variable if set, otherwise `ufo.toml`. `load_config` reads that TOML file, parses it, and validates it into a `Config`. Without this file, startup code would not have a reliable, checked source of truth for how this deployment is supposed to run.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 36–49)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validator fills in the DBOS system-store database URL when the config does not provide one. It keeps the operator from having to repeat a predictable related database address, while still allowing an explicit override.

**Data flow**: It starts with a `DatabaseConfig` that already has the main database `url` and may or may not have `system_url`. If `system_url` is already set, it leaves everything alone. If not, it splits the main URL at the final slash, builds a sibling database or file name ending in `_dbos`, adjusts the driver name for synchronous DBOS use, stores that back on the config object, and returns the updated object.

**Call relations**: This runs automatically while Pydantic is building a `DatabaseConfig`, usually as part of `load_config` validating the whole `Config`. Other startup code can then read `database.system_url` without needing to know whether it came from the TOML file or was derived here.


##### `BlobConfig._backend_complete`  (lines 73–78)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validator checks that the chosen blob storage backend has its minimum required setting. Filesystem storage needs a local root folder, while S3 storage needs a bucket name.

**Data flow**: It receives a `BlobConfig` after its fields have been parsed. If the backend is `filesystem`, it checks that `root` is present. If the backend is `s3`, it checks that `bucket` is present. Missing required information becomes a clear validation error; otherwise the same config object is returned unchanged.

**Call relations**: This is called automatically during config validation, before the rest of the system tries to store transcripts, artifacts, workspaces, or other blob data. It prevents later, harder-to-understand failures caused by an incomplete storage setup.


##### `ModelsConfig._auto_model_concrete`  (lines 93–96)

```
def _auto_model_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validator makes sure the deployment pins `auto` model selection to a real model ID. It prevents an agent setting of `model = "auto"` from resolving to another vague `auto` value at runtime.

**Data flow**: It receives a `ModelsConfig` with an `auto_model` value. If that value is empty or equal to the special `AUTO_MODEL` placeholder, it raises a validation error. Otherwise it returns the config unchanged, meaning future agent turns have a concrete model name to use.

**Call relations**: This runs during configuration validation, typically inside `load_config`. Later model-selection code can rely on `models.auto_model` being a real backend model identifier instead of having to repeat this safety check.


##### `config_path`  (lines 263–264)

```
def config_path() -> Path
```

**Purpose**: This function decides which config file path should be used. It lets operators override the default `ufo.toml` location with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that variable exists, its value becomes the path. If not, it uses the default `ufo.toml`. It wraps the chosen string in a `Path` object and returns it.

**Call relations**: `load_config` calls this when no path is passed in directly. This keeps path selection separate from file reading, so tests or callers can provide a specific path while normal startup uses the environment-aware default.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 267–273)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the deployment config file and turns it into a validated `Config` object. It is the main entry point other code uses when it needs the system’s settings.

**Data flow**: It receives an optional path. If no path is given, it asks `config_path` for the right location. It checks that the file exists; if not, it raises a clear `FileNotFoundError` explaining how to fix it. If the file exists, it reads the text, parses the TOML into ordinary data, validates that data through the `Config` model, and returns the resulting typed config object.

**Call relations**: This function ties together path selection, disk reading, TOML parsing, and Pydantic validation. Startup code calls it before constructing the rest of the application, and the validators on classes like `DatabaseConfig`, `BlobConfig`, and `ModelsConfig` run as part of the object-building process it triggers.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).

## 📊 State Registers Touched

- `reg-effective-config` — The combined settings that tell the service which features, adapters, limits, and deployment options to use.
- `reg-extension-pack-lock` — The saved choice of active packs and installed extensions for a workspace.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-tool-catalog` — The shared list of tools the agent may call, including their names, inputs, safety labels, and handlers.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-browser-session` — The browser connection state used when a turn needs a Chrome endpoint or computer-use actions.
- `reg-live-event-stream` — The live progress channel that lets clients attach, resume, and receive streamed turn updates.
- `reg-background-job-registry` — The registered set of built-in and extension background workflows that the scheduler can run.
- `reg-extension-catalog-cache` — The discovered extension/pack catalog and update metadata used to show available extensions and resolve pinned installs before loading capabilities.
- `reg-evaluation-replay-state` — Durable evaluation corpora, replay runs, scores, and judgments used by self-improvement and conformance workflows beyond prompt approval records.
- `reg-redis-connection-pool` — Process-wide Redis client/connection pool and stream backend handles used to distribute live turn events across server processes.
- `reg-adapter-implementation-registry` — Process-wide mapping from configured backend/provider names to implementation adapters for Redis hubs, sandboxes, browsers, models, search, sources, and related services.
