# Configuration, Pack Selection, and Extension Discovery  `stage-3`

This stage is startup setup. Before the assistant can work, the system must know which configuration to trust, which feature packs are active, and which extensions are installed. It is like opening a toolbox, checking the instruction sheet, and laying out only the approved tools.

The configuration files define the expected ufo.toml settings and shared connection rules for model providers and the database. The extension store reads the public catalog, lets ufoctl choose extensions, and saves those choices in a lockfile so the same set can be loaded again. The extension loader then acts as the gatekeeper: it discovers installed extensions, checks that they fit together safely, and registers their tools, skills, routes, credentials, hooks, object types, and backends.

The sub-stages supply the pieces being registered. Packs define ready-made bundles for local, hosted, billing, and evaluation use. Manifests describe apps, skills, web surfaces, workflows, external services, and providers. Provider registration adds models, feature flags, sandboxes, connectors, and live transports. The many package-root files are simple Python nameplates that make extension folders importable. Together, these parts build the enabled capability set for the rest of the system.

## Sub-stages

- [Application and Skill Bundle Registration](stage-3.1.md) `stage-3.1` — 12 files
- [Provider Backend Registration](stage-3.2.md) `stage-3.2` — 9 files
- [Pack Definitions and Evaluation Bundles](stage-3.3.md) `stage-3.3` — 7 files
- [Assistant, Web, and Workflow Extension Manifests](stage-3.4.md) `stage-3.4` — 11 files
- [External Integration Extension Manifests](stage-3.5.md) `stage-3.5` — 6 files
- [Workspace App and User Surface Package Roots](stage-3.6.md) `stage-3.6` — 10 files
- [Tool, Connector, and Provider Package Roots](stage-3.7.md) `stage-3.7` — 10 files
- [Knowledge, Automation, and Skill Package Roots](stage-3.8.md) `stage-3.8` — 13 files

## Files in this stage

### Extension selection
Catalog and lockfile support records the extension choices that startup later loads.

### `core/src/ufo/ext/store.py`

`domain_logic` · `extension command handling`

This file is the small “shop counter” for UFO extensions. The catalog is like a menu of extensions that may be offered by a deployment. The lockfile is the receipt that says exactly which extensions are pinned for use, including a digest, which is a fingerprint of the installed package. That fingerprint helps make the choice repeatable instead of vague.

The file defines simple data shapes for catalog entries and search results, then defines `ExtensionStore`, which performs the useful actions: search, install, and remove. Searching compares the user’s text against catalog names and also checks the lockfile so it can say whether each result is already installed. Installing first verifies that the extension is listed in the catalog, then refuses entries marked disabled. In this system, “disabled” means “bundle-only”: another command may pin it as part of a bundle, but normal install is not allowed. If installation is allowed, the store asks the extension loader what extensions are actually present in the current Python environment and creates a pin from the discovered manifest plus a source digest. If the package is not really installed, it fails clearly.

Removing is the reverse: it checks the lockfile, refuses to remove something that is not pinned, and writes back the remaining pins. Without this file, users could not safely search the approved extension catalog or update the lockfile that controls what the system boots with.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked `Catalog` object. Someone would use this before creating an `ExtensionStore`, so the store knows what extensions are available.

**Data flow**: It receives a file path. It reads the file text, parses it as TOML, which is a human-friendly configuration format, and validates the parsed data against the expected catalog shape. It returns a `Catalog` containing the listed extensions.

**Call relations**: This is the doorway from a catalog file into the in-memory store. It relies on the path object to read text and on the TOML parser to decode that text before the rest of this file can search or install from the catalog.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Looks up the installed version of the `ufo` package. The lockfile needs this version so it can record which UFO version the pins belong with.

**Data flow**: It takes no input from the caller. It asks Python’s package metadata for the installed version named `ufo`, then returns that version string.

**Call relations**: This is used by `ExtensionStore._write` when there is no existing lockfile to preserve. In that case, `_write` needs a fresh UFO version to anchor the new lockfile.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Creates the lockfile pin for one installed extension. A pin records the extension name, the version declared by its manifest, and a digest, meaning a fingerprint of the extension’s source.

**Data flow**: It receives an extension name. It asks the loader what extensions have been discovered in the current Python environment, finds the named one, and fails loudly if it is missing. If found, it reads the manifest version, computes a digest from the discovered entry, and returns an `ExtensionPin` ready to be written to the lockfile.

**Call relations**: `ExtensionStore.install` calls this after it has confirmed the requested name is allowed by the catalog. `pin_for` then hands back the exact pin that `install` writes into the lockfile.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog for extension names containing the user’s query text. It also marks each result as already installed if the extension is currently pinned in the lockfile.

**Data flow**: It receives a search string. It reads the current pins from the lockfile through `_pins`, builds a set of pinned names, then walks through the catalog entries. For each catalog entry whose name contains the query, it creates a `StoreListing` with the catalog details and an installed/not-installed flag. It returns all matching listings as a tuple.

**Call relations**: This is the read-only path used when a caller wants to browse the store. It depends on `_pins` to compare catalog entries with the saved lockfile state, but it does not write anything.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins one catalog extension into the lockfile so the loader can use it later. It enforces the catalog rules, including refusing bundle-only entries.

**Data flow**: It receives an extension name. It looks for that exact name in the catalog, raises an error if the name is unknown, and raises a different error if the catalog entry is disabled. If the entry is allowed, it asks `pin_for` to build a verified pin from the installed package. It then reads the existing pins, replaces any old pin with the same name, writes the updated pin list, and returns the new pin.

**Call relations**: This is the main write path for adding extensions. It calls `pin_for` to prove the extension exists in the current environment and to build the fingerprinted pin, uses `_pins` to preserve unrelated existing pins, and then hands the final list to `_write` for saving.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes one pinned extension from the lockfile. It is used when the user wants the loader to stop using an extension.

**Data flow**: It receives an extension name. It reads the current pins, checks whether any pin has that name, and raises an error if none do. If the pin exists, it filters that pin out and writes the smaller pin list back to the lockfile. It returns nothing.

**Call relations**: This is the opposite of `ExtensionStore.install`. It uses `_pins` to see what is currently saved, then uses `_write` to store the lockfile after the named extension has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current extension pins from the lockfile, or returns an empty list if there is no lockfile yet. This gives the rest of the store a simple view of what is currently installed.

**Data flow**: It uses the store’s lockfile path. If the file exists, it reads and parses the lockfile and returns its extension pins. If the file does not exist, it returns an empty tuple, meaning nothing is pinned yet.

**Call relations**: `search`, `install`, and `remove` all call this whenever they need the current saved state. It hides the “file may not exist yet” detail so those higher-level actions can work with pins directly.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete set of extension pins to the lockfile. It preserves the existing lockfile’s UFO version when possible, or records the current UFO version when creating a new lockfile.

**Data flow**: It receives the full tuple of pins that should be saved. It checks whether the lockfile already exists. If it does, it reads the existing UFO version from that file; if not, it asks `ufo_version` for the installed package version. It then builds a new `Lockfile` object with that version and the supplied pins, and writes it to disk.

**Call relations**: `install` and `remove` both call this after deciding the new desired pin list. `_write` is the final step that turns those in-memory changes into the lockfile the loader will later boot against.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### Deployment configuration
Configuration and startup helpers validate deployment settings and derive safe provider and database connection rules.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project’s configuration contract. It says, in one place, what settings a UFO deployment must provide: database addresses, blob storage, model choices, sandbox behavior, feature flags, extensions, observability, and more. Without it, different parts of the system would each guess at settings, and mistakes like using the placeholder model name `auto` in production or serving user sessions over an unsafe public URL could slip through silently.

The file uses Pydantic models, which are Python classes that both store configuration values and check that they are valid. Think of each class as a labeled section of the `ufo.toml` file: `DatabaseConfig` checks database URLs, `BlobConfig` checks where large stored files live, `SandboxConfig` checks how isolated workspaces are reached, and so on. Most classes forbid unknown fields, so a typo in the config file is treated as an error instead of being ignored.

A few settings are derived automatically. For example, if the special DBOS system database URL is not given, it is built from the main database URL. Other settings are deliberately strict. Blob storage must include either a filesystem root or an S3 bucket, depending on the chosen backend. Public sandbox ingress URLs must be safe and simple because they may carry a member’s browser session.

At the bottom, `load_config` reads the TOML file and turns it into the validated `Config` object used by the rest of the service.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 40–53)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validator fills in the DBOS system database URL when the operator did not write one explicitly. It keeps deployments simpler by deriving the system store as a sibling of the main application database.

**Data flow**: It starts with the configured main database URL and the optional `system_url`. If `system_url` is already present, it leaves everything unchanged. If it is missing, it splits the main URL near the database name, creates a related name ending in `_dbos`, adjusts the driver name for SQLite or PostgreSQL where needed, stores that derived URL back on the config object, and returns the updated object.

**Call relations**: This runs automatically while Pydantic is building a `DatabaseConfig` inside the larger `Config`. Other parts of the service can then read `database.system_url` without having to repeat this fallback logic or wonder whether it is missing.


##### `BlobConfig._backend_complete`  (lines 75–80)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validator makes sure the chosen blob storage backend has the setting it needs to work. It prevents the service from starting with, for example, filesystem storage but no folder to write into.

**Data flow**: It reads the chosen `backend` plus the related storage fields. If the backend is `filesystem`, it requires `root`. If the backend is `s3`, it requires `bucket`. If the needed value is absent, it raises a clear error; otherwise it returns the config unchanged.

**Call relations**: This check happens when the blob section of the main config is parsed. Later code that stores transcripts, artifacts, or other large records can trust that the basic destination exists in the configuration instead of checking the same condition again.


##### `ModelsConfig._models_concrete`  (lines 103–114)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validator makes sure every deployment-level model choice is a real model ID, not empty and not the special placeholder `auto`. That matters because this config is where vague model choices are pinned to actual providers and model names.

**Data flow**: It reads `auto_model`, `ambient_reply_model`, and `background_jobs_model`. For each one, it checks that the value is present and is not the project’s `AUTO_MODEL` marker. If any value is invalid, it raises a specific error naming the bad setting; if all are concrete, it returns the config object.

**Call relations**: This runs during config parsing before model-using code starts. It protects later agent turns, ambient reply checks, and background jobs from discovering too late that they were given a placeholder instead of a usable model.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 234–271)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validator checks that the public base URL for sandbox-served sites is safe and usable. It requires a simple host-only base, normally over HTTPS, because sandbox pages may carry a member’s session cookie.

**Data flow**: It first reads `ingress_public_url`. If it is not set, it accepts the config. If it is set, it splits the URL into parts, checks that there is a hostname, allows HTTPS in normal cases, and allows plain HTTP only for `localhost` or subdomains of `localhost` for local development. It rejects URLs with paths, query strings, fragments, usernames, or passwords because the system builds individual site addresses by putting labels in front of the host. The result is either the same config object or a clear validation error.

**Call relations**: During config validation, this function calls `urllib.parse.urlsplit` to inspect the URL. Its result matters later to both the sandbox ingress service, which receives browser traffic, and code that creates public links to sandbox surfaces; both need the base URL to mean exactly the same thing.

*Call graph*: 1 external calls (urlsplit).


##### `config_path`  (lines 418–419)

```
def config_path() -> Path
```

**Purpose**: This helper decides which configuration file path to use. It lets operators override the default `ufo.toml` location with the `UFO_CONFIG` environment variable.

**Data flow**: It reads the process environment. If `UFO_CONFIG` is set, it uses that string; otherwise it uses the default path `ufo.toml`. It wraps the chosen text in a `Path` object, which is Python’s standard way to represent filesystem paths, and returns it.

**Call relations**: `load_config` calls this when no path was passed in directly. This keeps the environment-variable rule in one small place instead of spreading it across the codebase.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 422–428)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the configuration file from disk and turns it into a validated `Config` object. It is the main entry point other code uses when it needs deployment settings.

**Data flow**: It receives an optional path. If no path is provided, it asks `config_path` for the default or environment-selected location. It checks that the file exists; if not, it raises a clear `FileNotFoundError` explaining how to fix it. If the file exists, it reads the text, parses it as TOML using `tomllib.loads`, and asks Pydantic to validate the parsed data into a `Config` object. The output is a fully checked configuration object, or an exception describing what is wrong.

**Call relations**: This function is the bridge between the outside world’s `ufo.toml` file and the internal typed config classes in this file. Startup code can call it once, receive a trusted `Config`, and pass that object to the rest of the system.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### `core/src/ufo/proxy_serve.py`

`config` · `startup / config load`

This file sits near the boundary between configuration and service startup. Its job is to answer two important questions for shared services: “Which outside model-provider hosts may a sandbox contact?” and “What database connection should the shared ingress service use?”

For model calls, sandboxes are not allowed to freely reach the internet. They need a short approved list of destinations, like a building security desk that only opens the door for known visitors. `model_rule_base` builds that approved list from the configured Anthropic and OpenAI API key environment variable names. If a key is present, it derives the network rules needed for that provider and gathers the allowed hosts. If no model key is present at all, it stops immediately with a clear error, because a sandbox with no model route would fail later in a more confusing way.

For database access, `owner_dsn` finds the special database address used by shared services. This connection bypasses row-level security, meaning the database itself will not automatically hide other workspaces’ rows. Because of that, callers must filter every query by workspace explicitly. The function reads this address from `UFO_OWNER_DSN` first, then from the config file, and fails loudly if neither is set. It also rewrites the URL so the async PostgreSQL driver shipped with this project is used.

#### Function details

##### `model_rule_base`  (lines 18–37)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the shared base set of outbound network rules that let sandboxes call configured model providers. It exists so every sandbox gets the same carefully limited route to services like Anthropic or OpenAI, and so missing provider keys are caught early.

**Data flow**: It takes the project `Config`, reads the configured environment variable names for model API keys, and asks `deploy_env` whether those keys are actually set in the deployment environment. For each provider with a key, it asks `derive_model_rules` to produce the needed egress rules, gathers host allow-list entries into one combined `ScopeRule`, and keeps any other rules alongside it. It returns a tuple of rules; if no provider key is found, it raises an error instead of returning an unusable empty rule set.

**Call relations**: This helper is used when a shared service is preparing the base egress policy for sandboxed model calls. In that flow, it delegates secret lookup to `deploy_env`, delegates provider-specific rule creation to `derive_model_rules`, and finally creates a `ScopeRule` that represents the combined allowed model-provider hosts.

*Call graph*: 3 external calls (__init__, deploy_env, derive_model_rules).


##### `owner_dsn`  (lines 40–52)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string for the shared service’s owner-level database access. This matters because shared services serve many workspaces, so they need one powerful connection but must rely on explicit workspace filters in their queries.

**Data flow**: It takes the project `Config`, first checks the `UFO_OWNER_DSN` environment variable, and if that is missing checks `config.database.owner_url`. If neither value exists, it raises a clear error explaining that the shared service needs an owner database URL. If it finds a URL, it rewrites the beginning from `postgresql://` to `postgresql+psycopg://` so the async PostgreSQL driver is selected, then returns the rewritten string.

**Call relations**: This helper is called when the shared ingress side needs to open its database connection. It does not call other project helpers itself; it simply chooses the right source for the secret, validates that it exists, and hands back a driver-specific database URL for the connection layer to use.


### Extension loading namespaces
Package markers and the loader make extension modules importable and convert enabled manifests into runtime capabilities.

### `core/src/ufo/ext/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can act as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to modules under `ufo.ext` using normal import paths.

There is no logic here: no functions, no classes, and no setup work. Its value is structural. It is like putting a label on a drawer so the rest of the program knows the drawer exists and can look inside it for extension-related code.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.ext` might fail or behave differently. Keeping it present makes the package layout explicit and helps tools, tests, and runtime code recognize this directory as part of the project’s Python module tree.


### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and per-turn/request setup`

Extensions are add-ons. They can bring new tools, credentials, database migrations, skills, hooks, object types, and search or embedding backends. This file is where those add-ons become part of a running UFO system.

The first job is discovery. Extensions and packs are found through Python entry points, which are like published sign-up cards installed packages leave for the application. The loader calls each card, gets a manifest, and rejects confusing cases such as two extensions using the same name.

The second job is safety. If a lockfile exists, it acts like a sealed packing list for deployment: only pinned extensions may load, and their source-code digest must match. If the code changed, startup fails instead of quietly running unexpected code. Without a lockfile, the system behaves like a development setup and loads everything installed.

The third job is translation. Manifests are declarations; the running system needs registries and chains. This file builds the turn tool list, object registry, skill registry, credential injection slots, database migration paths, backend clients, and hook chains. A hook chain is like a row of inspectors: each extension gets a chance to approve, modify, or observe an event. The file also fails early when an extension needs credentials but no credential store is available, so problems appear at startup rather than during a user’s turn.

#### Function details

##### `lockfile_path`  (lines 159–160)

```
def lockfile_path() -> Path
```

**Purpose**: Finds the lockfile path the loader should use. This lets deployments override the default lockfile location with an environment variable.

**Data flow**: It reads the environment variable for the lockfile path. If it is not set, it uses the default path `ufo.lock`. It returns a `Path` object pointing to that file.

**Call relations**: When `load_manifests` needs to know whether the deployment is pinned, it calls this helper first to locate the lockfile.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 163–164)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads a lockfile from disk and turns it into a validated `Lockfile` object. This catches malformed lockfiles before extension loading continues.

**Data flow**: It receives a file path, reads the file text, parses the JSON, and validates it against the expected lockfile shape. It returns the validated lockfile data.

**Call relations**: `load_manifests` calls this when a lockfile exists, then uses the returned pins to decide which extensions may run.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 167–168)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a validated lockfile object to disk as readable JSON. Tools that pin or bundle extensions use this format so startup reads the same information later.

**Data flow**: It receives a destination path and a `Lockfile` object. It converts the object to indented JSON, adds a final newline, and writes that text to disk.

**Call relations**: This is the writing counterpart to `read_lockfile`. The loader itself mainly reads lockfiles, while command-line tools can use this function to create them.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 171–185)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension in the current Python environment. It also rejects duplicate extension names and blocks third-party extensions from declaring privileged access.

**Data flow**: It asks Python for all entry points in the `ufo.extension` group. For each one, it loads and calls the entry point to get a manifest, checks basic safety rules, and builds a dictionary from manifest name to manifest plus entry point.

**Call relations**: `load_manifests` uses this as the full installed extension list before applying lockfile or pack rules. `migration_locations` also uses it so it can connect active manifests back to installed package locations.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 188–199)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds every installed pack. A pack is a bundle that names a coherent set of extensions and may add its own skills or onboarding content.

**Data flow**: It asks Python for all entry points in the `ufo.pack` group. Each entry point is loaded and called to produce a pack, and the function returns a dictionary keyed by pack name after checking for duplicates.

**Call relations**: `_pack_manifests` calls this when configuration selects a pack, so it can narrow the active extension set to the pack’s declared contents.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 202–207)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds the import information for the installed package behind an extension entry point. This is needed to locate source files and migration directories.

**Data flow**: It takes an entry point, extracts the top-level module name, and asks Python where that module comes from. If Python cannot find real importable source, it raises an error.

**Call relations**: `extension_digest` uses this to find files to hash. `migration_locations` uses it to find an extension package’s `migrations` directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 210–215)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Finds the directory that belongs to an extension package. This gives the loader a stable place to look for extension database migrations.

**Data flow**: It receives Python module information. If the extension is a package directory, it returns that directory; if it is a single file, it returns the file’s parent directory.

**Call relations**: `migration_locations` calls this after `_entry_spec` so it can check whether the extension has a `migrations` folder.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 218–234)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes a source-code fingerprint for an installed extension. This lets the lockfile detect whether an extension’s installed files have changed since they were pinned.

**Data flow**: It receives an extension entry point, finds the package source, gathers source files while ignoring bytecode caches, reads their bytes, and passes the named file contents to `extension_content_digest`. It returns a string starting with `sha256:`.

**Call relations**: `load_manifests` calls this for each pinned extension in a lockfile and compares the result to the pinned digest.

*Call graph*: calls 2 internal fn (_entry_spec, extension_content_digest); called by 1 (load_manifests); 1 external calls (Path).


##### `extension_content_digest`  (lines 237–243)

```
def extension_content_digest(files: Mapping[str, bytes]) -> str
```

**Purpose**: Builds the actual SHA-256 digest for a set of extension files. It includes both file names and file contents so renaming or editing files changes the fingerprint.

**Data flow**: It receives a mapping from file name to file bytes. It sorts the names, hashes each name and each file’s contents into one combined hash, and returns the final digest string.

**Call relations**: `extension_digest` gathers the files from disk and delegates the stable hashing step to this function.

*Call graph*: called by 1 (extension_digest); 1 external calls (sha256).


##### `migration_locations`  (lines 246–263)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Lists the database migration folders provided by active extensions. These are extra database change scripts that should be applied alongside core migrations.

**Data flow**: It discovers installed extensions, loads the active manifests, optionally narrowed by a pack, then finds each active installed extension’s package directory. If that directory has a `migrations` subfolder, its path is added to the returned tuple.

**Call relations**: Migration setup calls this to know which extension migration branches to include. It relies on `load_manifests` for the active set and on `_entry_spec` plus `_package_dir` to find files on disk.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 266–289)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Decides which extension manifests are active for this run. It enforces the lockfile when present, or loads all discovered extensions in development mode.

**Data flow**: It discovers installed extensions and checks for a lockfile. Without one, it returns all discovered manifests. With one, it reads pinned extensions, verifies each is installed, checks each digest, and returns only those manifests. If a pack name is provided, it passes the active set to `_pack_manifests` for further narrowing.

**Call relations**: This is the main doorway for extension declarations. Other startup builders, such as `migration_locations`, use its result so every part of the system sees the same pinned extension set.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 292–325)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Turns a selected pack into the exact manifest list that should be active. It ensures every extension named by the pack is installed and active.

**Data flow**: It receives a pack name and the already-active extension manifests. It finds the declared pack, collects the manifests for its bundled extensions, checks for missing or conflicting names, then adds a synthetic manifest for the pack’s own skills and onboarding content.

**Call relations**: `load_manifests` calls this when configuration selects a pack. It calls `discovered_packs` to find installed packs and returns the pack-specific manifest list to the loader.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 328–337)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects the command-line credential declarations from connector extensions. These declarations tell the system which environment variables represent usable connector grants.

**Data flow**: It receives active manifests, walks through each manifest’s connectors, and keeps connectors that declare a CLI credential. It returns a dictionary keyed by provider name.

**Call relations**: `injecting_slots` calls this so connector CLI environment variables share the same conflict checks as credential slot exports.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 340–413)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into a sandbox and checks that their environment variables, sentinels, host choices, and metering dimensions do not conflict. This prevents quiet authentication mistakes.

**Data flow**: It receives active manifests, extracts credential slots with injection rules, gathers declared slot names and connector CLI exports, then validates that no two different meanings claim the same sandbox variable or sentinel. It returns the safe tuple of injectable credential slots.

**Call relations**: Credential export and proxy rule code use this as the trusted list. It calls `connector_clis` so connector credentials and injected slots are checked in one shared namespace.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 416–496)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the complete tool set available during a turn. It combines built-in tools, extension tools, connector tools, and object-operation tools, while binding each extension tool to its own scoped context.

**Data flow**: It receives active manifests plus optional services such as credential storage, indexing, embeddings, blob storage, audience, and URL settings. For each manifest with tools or object kinds, it checks credential requirements, creates an `ExtensionContext`, adds declared tools, records which context owns each tool, registers object kinds, and finally adds object verb tools. It returns the tool definitions and a map from extension tool name to context.

**Call relations**: The turn engine uses this before dispatching tools. It calls `context_for` to create per-extension contexts, `core_object_kinds` to add core object types, and `object_registry` plus `ObjectVerbs` to turn object kinds into usable tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `member_object_registry`  (lines 499–545)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object registry used by member-facing reads outside a live turn, such as a portal page. It includes core object kinds and extension object kinds.

**Data flow**: It receives active manifests and optional service handles. For every manifest with object kinds, it checks whether credentials are required, creates a workspace-level extension context, binds each object kind to that context, adds core object kinds, and returns the final registry.

**Call relations**: Portal or member-read code uses this instead of the turn tool builder because there is no conversation audience. It shares `core_object_kinds` and `object_registry` with the turn path.

*Call graph*: calls 1 internal fn (core_object_kinds); 3 external calls (__init__, context_for, object_registry).


##### `core_object_kinds`  (lines 548–584)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, *, public_base_url: str | None=None, artifact_token_secret: str='') -> tuple[BoundKind, ...]
```

**Purpose**: Creates the object kinds owned by core itself: credentials, extensions, and artifacts. These let the system expose core state through the same object interface used by extensions.

**Data flow**: It receives active manifests, optional credential storage, and URL/signing settings. It builds an object kind for credentials from declared slots, an object kind for extensions from named manifests, and an artifact object kind for signed artifact links. It returns these as bound core kinds with no extension context.

**Call relations**: `turn_tools`, `member_object_registry`, and `validate_ext_tools` all call this so core objects are present wherever object tools or registries are built.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 7 external calls (__init__, __init__, __init__, __init__, named_extensions, declared_slots, artifact_object).


##### `skill_registry`  (lines 587–611)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the deploy’s loadable skill registry. It combines core skills, skills contributed by active manifests, and optional generated skills, while rejecting duplicate names.

**Data flow**: It starts with built-in core skills. For each manifest skill spec, it reads skills from disk and adds them if their names are unused. Then it adds generated skills with the same collision check. It returns a `SkillRegistry` containing all skills and a record of which ones were bundled rather than generated.

**Call relations**: Startup code uses this to prepare skill lookup and skill-index rendering. It delegates disk discovery to `discover_skills` and finishes by constructing `SkillRegistry`.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 614–618)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. Subagents are named helper personas or workers that can be made available during a run.

**Data flow**: It receives manifests, reads each manifest’s subagent profiles in manifest order, and returns them as one tuple.

**Call relations**: The serving layer can use this returned list to build the subagent registry. Duplicate profile handling happens later when that registry is constructed.


##### `durable_surfaces`  (lines 621–627)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds which extension surfaces need durable writeback. A surface is durable here if it declares a post handler, meaning replies should be delivered through a poller-backed path.

**Data flow**: It receives manifests, inspects their surface declarations, keeps surfaces with a `post` handler, and returns their names as a frozen set.

**Call relations**: Admission or conversation setup code uses this to decide when to register writeback rows for turns entering those surfaces.


##### `turn_subagent_grants`  (lines 630–640)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects extra tool grants that extensions give to subagent profiles they may not own. This lets one extension widen another profile’s usable tools without editing that profile directly.

**Data flow**: It receives manifests, walks through each subagent tool grant, unions tool names by target profile, and returns a dictionary from profile name to frozen set of granted tools.

**Call relations**: The turn loop can fold these grants into each subagent profile’s own tool list, then intersect with the live tool registry so missing profiles or tools do not break the run.


##### `turn_member_skills`  (lines 643–685)

```
async def turn_member_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, agent_name: str) -> tuple[tu
```

**Purpose**: Builds the member-saved skill cards available to one agent, plus a loader that can materialize a selected skill later. It filters cards by agent targeting and avoids failing the turn on duplicate names.

**Data flow**: It receives manifests, services, and the agent name. For each manifest with a member skill provider, it checks that credentials are available, creates an extension context, asks the provider for cards, skips cards not meant for this agent, records the first provider for each skill name, and returns the cards plus a materializer function.

**Call relations**: Turn setup uses this to show and load member skills. It calls `context_for` for provider access and logs name collisions instead of throwing away the whole turn.

*Call graph*: 2 external calls (context_for, log).


##### `turn_member_skills.materialize`  (lines 678–683)

```
async def materialize(name: str) -> RuntimeSkill | None
```

**Purpose**: Loads one member-saved skill by name using the provider recorded earlier. It returns nothing when the requested skill name was not advertised.

**Data flow**: It receives a skill name, looks it up in the provider map captured by `turn_member_skills`, and if found asks that provider to materialize the runtime skill using its extension context. It returns the loaded skill or `None`.

**Call relations**: This nested function is returned by `turn_member_skills`. The turn code calls it later when it needs the full skill content behind a card.


##### `member_skill_listing`  (lines 688–714)

```
async def member_skill_listing(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Builds the full list of member-saved skills for management views. Unlike turn-specific skill cards, it does not filter by agent targeting.

**Data flow**: It receives manifests and optional services. For each member skill provider, it checks credentials, creates an extension context, asks the provider to materialize all skills, keeps the first skill for each name, logs duplicates, and returns the collected runtime skills.

**Call relations**: Portal management pages use this to list the workspace’s saved skills. It follows the same credential and duplicate-name rules as `turn_member_skills`.

*Call graph*: 2 external calls (context_for, log).


##### `index_backend`  (lines 720–740)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and builds the configured workspace index backend. An index backend is the component that stores and searches indexed text or vectors.

**Data flow**: It receives manifests, an optional configured backend name, and optional credential storage. It uses the configured name or `default`, searches extension index specs for that name, checks credential requirements, creates an extension context, and returns the backend from the spec factory. If none is registered, it raises `NotRegisteredError`.

**Call relations**: Startup or workspace setup calls this once the active manifests are known. It uses `context_for` to give the selected extension its scoped access before calling the factory.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 743–763)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and builds the configured embedding client. An embedding client turns text into numeric vectors used for search and memory features.

**Data flow**: It receives manifests, an optional configured backend name, and optional credential storage. It uses the configured name or `default`, searches extension embed specs, checks credential requirements, creates an extension context, and returns the client produced by the spec factory. If no extension provides the name, it raises `NotRegisteredError`.

**Call relations**: Startup code uses this before wiring indexing, memory tools, and jobs. Like `index_backend`, it depends on manifests as the source of registered backend choices.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 766–791)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider, if an active extension declares it. Memory search is a pluggable way to search stored conversational or workspace memory.

**Data flow**: It receives manifests, credential storage, optional index and embedding clients, and a provider name. It finds matching memory search specs, returns `None` if none exist, rejects duplicates, checks credential needs, creates an extension context, builds the provider, and wraps it in `MemorySearch`.

**Call relations**: Memory-related setup calls this after index and embedding services are available. It uses `context_for` so the provider runs with the declaring extension’s permissions.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 794–830)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Checks extension tools and object kinds at boot so bad registrations fail before any user turn starts. It catches tool name collisions, object registry problems, and missing credential keys.

**Data flow**: It receives manifests and optional credential storage. It collects built-in tools and extension-declared tools, checks credential requirements for tool-declaring extensions, builds an object registry including core and extension object kinds, converts object kinds into tools, and constructs a `ToolRegistry` to trigger validation.

**Call relations**: Boot validation calls this as an early smoke test. It uses `core_object_kinds`, `object_registry`, `ObjectVerbs`, and `ToolRegistry`, mirroring the later `turn_tools` construction without needing a live workspace context.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.__post_init__`  (lines 871–874)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that every hook in a hook chain belongs to the same audience as the chain. This prevents a hook prepared for one conversation audience from being fired in another.

**Data flow**: After a `HookChain` is created, it flattens all bound hooks and compares each hook context’s audience with the chain’s audience. If any differ, it raises a value error; otherwise construction completes.

**Call relations**: This runs automatically when `turn_hooks` constructs a `HookChain`. It is a final consistency check before turn events can fire hooks.


##### `HookChain.fire`  (lines 876–954)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one turn event and combines their results. Hooks can deny an action, modify tool input or output, inject extra context, or simply observe.

**Data flow**: It receives an event, event payload, optional turn and agent records, and the speaker member id. It selects hooks for the event, skips tool-specific hooks that do not match, gives each hook the current payload in a `HookContext`, enforces a timeout, checks whether the returned outcome is allowed for that event, and folds outcomes into a `HookResolution`. Gating events fail closed on non-best-effort hook errors; non-gating failures are logged and swallowed.

**Call relations**: The turn engine calls this at lifecycle points such as before tool use, after tool use, prompt submission, and stop. It uses `HookContext` to call extension code, `replace` to pass modified payloads forward, and returns a `HookResolution` that the engine applies.

*Call graph*: 5 external calls (__init__, __init__, __init__, timeout, replace).


##### `turn_hooks`  (lines 957–1001)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain used during a turn. It binds each declared turn-lifecycle hook to the extension context it needs to run.

**Data flow**: It receives manifests, credential storage, optional services, a tailer, audience, and public URL. For each manifest with hooks, it requires credential storage, creates an extension context, keeps only turn-lifecycle hook events, groups them by event, wraps each as a bound hook, and returns a `HookChain`.

**Call relations**: Turn setup calls this before events start firing. It creates the structure later consumed by `HookChain.fire`.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 1016–1027)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Runs hooks that observe a newly recorded connection. These hooks are best-effort: a failure is logged but does not undo the recorded connection.

**Data flow**: It receives a `ConnectionRecorded` payload. For each bound connection hook, it creates a hook context, runs the handler with a timeout, and logs any exception instead of raising it.

**Call relations**: The connection flow calls this after a connection has landed and been committed. It uses the hooks prepared by `connection_hooks`.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 1030–1056)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the hook chain for connection-recorded events. This lets extensions react when a member completes a connection, such as creating related feeds or records.

**Data flow**: It receives manifests, credential storage, and optional index or embedding services. It finds hooks whose event is `connection_recorded`, requires credential storage for any extension declaring them, creates an extension context, binds the hooks, and returns a `ConnectionHookChain`.

**Call relations**: Connection setup uses this to prepare the chain that `ConnectionHookChain.fire` later publishes to. It mirrors turn hook binding but only for the control-plane connection event.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import/package discovery`

This file is intentionally empty, but it still has a useful job. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as a package, meaning other code can import modules from it using normal dotted names. Think of it like a label on a folder in a filing cabinet: the label may not contain documents itself, but it makes the folder recognizable and usable. Without this file, some Python tools or older import setups might not reliably recognize `extensions/documents/ufo_ext_documents` as an importable package. There is no runtime behavior here, no setup code, and no hidden side effect. Its value is structural: it keeps the package layout clear and import-friendly.

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-installation-lock` — The saved list of installed extensions and exact versions that should be loaded again consistently.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-tool-catalog` — The current list of tools the agent may call, with their names, inputs, permissions, and implementations.
- `reg-skill-store` — The shared library of built-in and user-created skills that can be selected, checked, and loaded into a turn.
- `reg-model-catalog` — The shared list of available AI models, their abilities, prices, limits, and required credentials.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-egress-policy` — The shared network exit rules that decide which outside addresses sandboxes may contact and when secrets may be added.
- `reg-object-registry` — The shared object front desk that gives stable names, views, permissions, and change history for workspace records.
- `reg-extension-data-store` — Durable extension-scoped key/value or JSON state used by installed extensions beyond their manifest capabilities and lockfile selection.
- `reg-extension-catalog-update-cache` — Cached public extension catalog and update/compatibility check metadata used when selecting, installing, bundling, or refreshing extensions.
