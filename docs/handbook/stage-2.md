# Configuration, pack selection, and extension inventory  `stage-2`

This stage is startup preparation. Before UFO can answer requests, it decides what kind of deployment it is, what add-ons are allowed, and what services are available. The pack manifest selection is the first recipe choice: it picks a pack, such as a local assistant, hosted assistant, evaluation setup, or specialized workspace, and uses it to choose the right extensions, skills, and infrastructure.

Extension manifest loading then reads each extension’s “identity card.” These manifests declare what the extension contributes, such as tools, web pages, login routes, scheduled jobs, skills, storage backends, or helper agents. Model and backend registration builds shared catalogs for AI models and data services, so later code can look up costs, credentials, limits, and connection details in one place.

The direct files tie this together. config.py reads and checks ufo.toml, failing early if the deployment is unsafe or incomplete. store.py manages which extensions are actually installed by pinning or removing them. bundle.py freezes the chosen configuration and extensions into a deployable folder, so the same setup can run elsewhere.

## Sub-stages

- [Pack manifest selection](stage-2.1.md) `stage-2.1` — 10 files
- [Extension manifest loading](stage-2.2.md) `stage-2.2` — 40 files
- [Model and backend registration](stage-2.3.md) `stage-2.3` — 7 files

## Files in this stage

### Deployment Configuration and Extension Selection
Defines the deployment configuration, manages the extension catalog and lockfile, and bundles the chosen configuration and extensions for reproducible runtime startup.

### `core/src/ufo/bundle.py`

`orchestration` · `bundle creation`

A UFO deployment depends on configuration and extensions. If those are not pinned down, the same deploy might behave differently on another machine, or even fail because an extension is missing. This file solves that by creating a small Docker build context: a directory containing a Dockerfile, a copied config file, and a lockfile that records the exact extension pins.

Think of it like packing a travel kit before a trip. The config says what the app should do, the lockfile lists the exact tools it needs, and the Dockerfile explains how to assemble the suitcase into a runnable container.

The main class, Bundle, is given the current config path, an optional extension catalog, and an output directory. When build is called, it first decides which extensions must be included. It starts from the current lockfile if one exists, or from the extensions discovered in the environment if not. If an extension catalog is available, it also adds entries marked as “bundle-only” — extensions that are installed into the bundle but not installed during normal runtime. Each selected extension is turned into a verified pin, so the bundle cannot silently refer to something that is not actually installed.

Finally, the file writes three outputs: ufo.toml, ufo.lock, and Dockerfile. The Dockerfile installs the local UFO wheel and starts ufoctl serve with the bundled config and lockfile.

#### Function details

##### `wheel_name`  (lines 25–28)

```
def wheel_name() -> str
```

**Purpose**: Builds the expected filename of the UFO Python wheel that will be copied into the Docker image. A wheel is a packaged Python distribution file, and this project expects the CLI to build it locally because it is not fetched from a public package index.

**Data flow**: It reads the current UFO version from the extension store helper, places that version into the standard wheel filename pattern, and returns the resulting string, such as a versioned ufo-...-py3-none-any.whl name. It does not write files or change state.

**Call relations**: Bundle._dockerfile calls this when writing the Dockerfile text. That lets the Dockerfile refer to the exact wheel filename that should exist beside the build context.

*Call graph*: called by 1 (_dockerfile); 1 external calls (ufo_version).


##### `Bundle.build`  (lines 49–62)

```
def build(self) -> BundleResult
```

**Purpose**: Creates the complete bundle directory. It gathers the extension pins, copies the config, writes a new lockfile, writes a Dockerfile, and returns a summary of what it produced.

**Data flow**: It starts with the Bundle object's inputs: the source config path, optional extension catalog, and output directory. It asks _pins for the exact extension list, creates the output directory if needed, copies the config text into ufo.toml, writes ufo.lock with the current UFO version and extension pins, and writes the Dockerfile text from _dockerfile. It returns a BundleResult containing the output paths and the pins used.

**Call relations**: This is the main action for this file. Higher-level bundle command code would call it when the user asks to make a bundle. Inside, it delegates extension selection to Bundle._pins and Dockerfile generation to Bundle._dockerfile, then packages those results into BundleResult.

*Call graph*: calls 2 internal fn (_dockerfile, _pins); 3 external calls (__init__, __init__, ufo_version).


##### `Bundle._pins`  (lines 64–78)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Decides exactly which extensions belong in the bundle and verifies each one by turning its name into an extension pin. This is what makes the bundle reproducible instead of depending on whatever happens to be installed later.

**Data flow**: It first reads the extensions discovered in the current environment. It then checks for an existing lockfile: if one exists, it uses the extension names already pinned there; if not, it uses all discovered extension names. If a catalog is present, it adds catalog entries marked disabled, because those are bundle-only additions. It removes duplicate names while keeping order, converts each name into a verified pin, and returns the pins as a tuple.

**Call relations**: Bundle.build calls this before writing the lockfile. This function relies on extension-loader helpers to discover installed extensions and read any current lockfile, then relies on pin_for from the store to produce the final checked pins.

*Call graph*: called by 1 (build); 4 external calls (discovered, lockfile_path, read_lockfile, pin_for).


##### `Bundle._dockerfile`  (lines 80–94)

```
def _dockerfile(self) -> str
```

**Purpose**: Creates the text of the Dockerfile used to build the runnable UFO container image. The Dockerfile installs the local UFO wheel, copies in the bundled config and lockfile, and sets the container to run ufoctl serve by default.

**Data flow**: It uses fixed bundle filenames and the wheel name produced by wheel_name to assemble a multi-line Dockerfile string. The result names the base Python image, sets the working directory, sets environment variables pointing UFO at the bundled config and lockfile, installs the wheel, copies the bundle files, and sets the startup command. It returns this text without writing it directly.

**Call relations**: Bundle.build calls this after the config and lockfile paths are known. This function calls wheel_name so the Dockerfile's COPY and install commands match the wheel that the surrounding bundle process is expected to provide.

*Call graph*: calls 1 internal fn (wheel_name); called by 1 (build).


### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project's configuration gatekeeper. It says what settings a UFO deployment must provide, what defaults are safe to assume, and which combinations are invalid. Without it, the server might start with missing database details, an unusable blob store, or a vague model name like `auto` that cannot actually be called.

The file uses Pydantic models, which are Python classes that validate data when they are built. Each section of `ufo.toml` has a matching class: database settings, blob storage, model choices, sandbox behavior, browser transport, connectors, research, packs, and more. These classes forbid unknown fields, so a typo in the config file is treated as an error instead of being silently ignored.

Some sections also fill in or check important details. For example, the database config can derive a separate DBOS system-store URL from the main database URL. The blob config checks that filesystem storage has a root folder and S3 storage has a bucket. The models config rejects `auto` as the final deployed model, because deployment must pin a real model ID.

At the bottom, `config_path` chooses which config file to read, and `load_config` reads TOML text, parses it, and validates the whole thing into one `Config` object. In everyday terms, this file is like the checklist at a building entrance: no one gets in until the required badges are present and make sense.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 36–49)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the DBOS system database URL when the user did not write one explicitly. DBOS is the system store used by the framework, and this keeps it paired with the main application database by default.

**Data flow**: It starts with a `DatabaseConfig` object that always has a main `url` and may or may not have `system_url`. If `system_url` is already set, it leaves the object unchanged. If not, it splits the main database URL, builds a sibling database name ending in `_dbos`, adjusts the driver name for either SQLite or PostgreSQL, stores that derived URL back on the object, and returns the updated config.

**Call relations**: This is run automatically by Pydantic while a `DatabaseConfig` is being created as part of loading the full config. It does not call other project functions; it prepares the database section so later startup code can rely on both the application database URL and the DBOS system-store URL being present.


##### `BlobConfig._backend_complete`  (lines 67–72)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step makes sure the chosen blob storage backend has the minimum information needed to work. Blob storage is where larger shared data such as transcripts, compaction records, and artifacts live.

**Data flow**: It receives a `BlobConfig` object with a selected backend. If the backend is `filesystem`, it checks that a local root path was provided. If the backend is `s3`, it checks that a bucket name was provided. If the required value is missing, it raises an error; otherwise it returns the same valid config object.

**Call relations**: Pydantic calls this automatically when the blob section of the config is built. Its job is to stop the application before startup continues with storage settings that could never read or write blobs successfully.


##### `ModelsConfig._auto_model_concrete`  (lines 87–90)

```
def _auto_model_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step makes sure the deployment chooses a real model ID for requests that were authored as `auto`. It prevents the running system from reaching model-call time with an unresolved placeholder.

**Data flow**: It receives a `ModelsConfig` object and reads its `auto_model` value. If the value is empty or still equal to the special `auto` marker, it raises an error. Otherwise it returns the valid model config unchanged.

**Call relations**: Pydantic runs this when building the models section during config loading. Later agent turns can then resolve `model = "auto"` to this concrete deployed model without having to decide it themselves.


##### `config_path`  (lines 266–267)

```
def config_path() -> Path
```

**Purpose**: This function decides which configuration file path the program should use. It lets an operator override the default `ufo.toml` location with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that variable is set, it turns its value into a `Path`; if not, it uses the default path `ufo.toml`. The result is a filesystem path object pointing to the config file to read.

**Call relations**: `load_config` calls this when its caller did not pass an explicit path. It is the small decision point that connects shell-level deployment settings to the config loading process.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 270–276)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the config file and turns it into a validated `Config` object. It is the main entry point other code uses when it wants the deployment settings.

**Data flow**: It starts with an optional path. If no path is provided, it asks `config_path` where to look. It checks that the file exists, and if it does not, it raises a clear error telling the operator to create `ufo.toml` or set `UFO_CONFIG`. If the file exists, it reads the text, parses the TOML into ordinary data, validates that data against the `Config` model, and returns the resulting config object.

**Call relations**: Startup or setup code calls this to obtain the full deployment configuration. Inside, it may call `config_path` to find the file and uses Python's TOML parser to decode it before handing the data to the Pydantic config models, whose validators fill in defaults and reject bad settings.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/ext/store.py`

`domain_logic` · `extension management commands`

This file supports commands such as `ufoctl ext` that work with UFO extensions. Think of it like a small app store with a shopping list: the catalog says what extensions are available, and the lockfile says which exact ones are chosen for this deployment. The lockfile is important because UFO’s loader boots from it, so it needs exact, repeatable information rather than a vague name.

The catalog is read from a TOML file, a simple configuration format. Each catalog entry has a name, a version, and an optional `disabled` flag. A disabled extension is “bundle-only”: it can be pinned by bundle-building tools, but normal install refuses it.

Installing does not just copy the catalog version. The code checks the current Python environment to make sure the extension package is really installed, reads its extension manifest version, computes a digest of its source entry, and writes that exact pin into the lockfile. This protects against pinning something that is listed in the catalog but missing locally.

Removing is the reverse: it checks the extension is currently pinned, then rewrites the lockfile without it. Searching combines catalog entries with the current lockfile so users can see both what is available and what is already installed.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file and turns it into a checked `Catalog` object. This gives the rest of the store a trusted list of extension names, versions, and disabled flags.

**Data flow**: It takes a filesystem path. It reads the text at that path, parses the TOML text into basic data, then validates that data against the expected catalog shape. The result is a `Catalog` object containing zero or more catalog entries.

**Call relations**: This is the front door for loading the store’s list of available extensions. It relies on the path object to read the file and on TOML parsing to understand the file contents before the store uses the catalog for search and install decisions.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Finds the installed version of the UFO package itself. The lockfile records this so the pinned extensions are tied to the UFO version they were written for.

**Data flow**: It takes no direct input. It asks Python’s package metadata system for the version of the installed package named `ufo`, then returns that version as text.

**Call relations**: This is used when `ExtensionStore._write` creates a brand-new lockfile. If an existing lockfile is present, `_write` preserves its recorded UFO version instead of calling this for a new anchor.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an installed extension. It makes sure the extension really exists in the current Python environment, then records its manifest version and a digest, which is a fingerprint of the extension source.

**Data flow**: It receives an extension name. It looks through the discovered installed extensions for that name. If none is found, it raises an error. If it is found, it takes the extension’s manifest version, computes a digest for its source entry, and returns an `ExtensionPin` with the name, version, and digest.

**Call relations**: This is called by `ExtensionStore.install` after the catalog check passes. It hands `install` the precise pin that should be written into the lockfile, rather than trusting only the catalog entry.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and shows whether each matching extension is already pinned in the lockfile. This is what lets a user see both availability and current install status in one result.

**Data flow**: It receives a query string. It first reads the current pins from the lockfile, then walks through the catalog entries whose names contain the query text. For each match, it creates a `StoreListing` that includes the catalog name, version, disabled flag, and whether the name appears among the current pins. It returns all listings as a tuple.

**Call relations**: This method is the read-only path through the store. It calls `ExtensionStore._pins` to learn what is already installed, then combines that with the catalog entries to produce user-facing search results.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins an extension into the lockfile so UFO will load it later. It refuses unknown catalog names and refuses catalog entries marked as disabled, because those are reserved for bundle workflows.

**Data flow**: It receives an extension name. It looks for that exact name in the catalog. If the name is missing, it raises an error. If the entry is disabled, it raises an error explaining that it is bundle-only. Otherwise it asks `pin_for` to build the exact installed-extension pin, removes any older pin with the same name from the current pin list, writes the updated list to the lockfile, and returns the new pin.

**Call relations**: This is the main write path for normal extension installation. It uses `pin_for` to verify and describe the installed package, uses `ExtensionStore._pins` to keep other existing pins, and hands the final list to `ExtensionStore._write` so the lockfile is replaced with the updated state.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Unpins an extension from the lockfile so UFO will no longer load it. It protects the user from removing something that is not currently installed.

**Data flow**: It receives an extension name. It reads all current pins from the lockfile. If no pin has that name, it raises an error. Otherwise it filters that pin out of the list and writes the shortened list back to the lockfile. It returns nothing.

**Call relations**: This is the reverse of `ExtensionStore.install`. It calls `ExtensionStore._pins` to see what is currently pinned, then calls `ExtensionStore._write` with the remaining pins after the chosen extension has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the lockfile and returns the currently pinned extensions. If there is no lockfile yet, it treats that as an empty install list.

**Data flow**: It reads the store’s lockfile path. If the file exists, it asks the loader’s lockfile reader to parse it and returns its extension pins. If the file does not exist, it returns an empty tuple.

**Call relations**: This helper is shared by `ExtensionStore.search`, `ExtensionStore.install`, and `ExtensionStore.remove`. It gives each of those flows the same view of the current lockfile state before they display, add, or remove pins.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete new lockfile with the given extension pins. It also preserves the lockfile’s existing UFO version when possible, so editing extensions does not unexpectedly change that anchor.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. If the lockfile already exists, it reads the existing UFO version from it. If not, it asks `ufo_version` for the currently installed UFO version. It then builds a new `Lockfile` object with that version and the supplied pins, and writes it to disk.

**Call relations**: This is the shared final step for `ExtensionStore.install` and `ExtensionStore.remove`. Those methods decide what the new pin list should be; `_write` turns that decision into the actual lockfile that the loader will later boot from.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).

## 📊 State Registers Touched

- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-pack-selection` — The selected product pack that decides which bundle of extensions, skills, and infrastructure is enabled.
- `reg-extension-inventory` — The installed extension set and their declared capabilities, such as tools, routes, jobs, skills, and storage.
- `reg-extension-store` — Per-workspace saved extension data that add-ons use to remember their own small pieces of state.
- `reg-model-catalog` — The shared list of available AI models, their limits, features, provider names, and calling rules.
- `reg-model-provider-adapters` — The shared provider clients that translate internal model requests into Anthropic, OpenAI, OpenRouter, or similar APIs.
- `reg-pricing-table` — The shared price list used to turn model and service usage into cost records.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-tool-catalog` — The shared catalog of tools the model is allowed to see and call during a turn.
- `reg-skill-store` — The shared set of built-in, extension-provided, and user-created skills available to agents.
- `reg-search-index` — The shared searchable index and chunk store built from synced or fetched content.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-data-backend-catalog` — The resolved registry of non-model service backends such as search, indexing, memory, source, browser, sandbox, and blob providers made available by configuration and extensions.
- `reg-evaluation-environment-state` — Persisted synthetic evaluation-environment data, such as fake email and calendar records, used by evaluation connectors and replay/test workflows without touching real external services.
