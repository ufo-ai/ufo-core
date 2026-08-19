# Configuration, pack selection, and extension discovery  `stage-3`

This stage happens during startup. It decides what version of the system is being assembled before any real user work begins. First, the configuration file defines the deployment settings and stops the server early if something important is missing or unsafe. Pack recipes then act like preset modes: they choose which extensions, skills, services, and test or production pieces should be loaded.

Next, extension manifests work like registration cards. They tell the host about tools, agents, web pages, background jobs, credentials, data types, and required capabilities. The extension store lets the command-line tool search, install, or remove extension pins safely. The extension loader then finds the installed packages and turns their declarations into usable parts of the running system.

Several helpers finish the setup. Sandbox selection chooses a safe place for agent-run code, either local or extension-provided. Proxy startup helpers centralize model-provider access and privileged database setup. Governance protects agent prompt changes by requiring approval before writing them. Provisioning copies extension-shipped agents into a workspace without overwriting local edits. Together, these pieces build the system’s available abilities before the main work loop starts.

## Sub-stages

- [Pack recipes](stage-3.1.md) `stage-3.1` — 8 files
- [Extension manifests and capability registration](stage-3.2.md) `stage-3.2` — 23 files

## Files in this stage

### Deployment configuration
Defines and validates the deployment settings that the rest of startup relies on.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the project's configuration gatekeeper. It says what settings a UFO deployment must provide, what defaults are safe to use, and which combinations are invalid. In everyday terms, it is like the checklist a building manager completes before opening the doors: database address, file storage, model choices, sandbox settings, web addresses, extension options, and so on.

Most of the file is made of Pydantic models. Pydantic is a validation library: it turns raw data into typed Python objects and rejects values that do not fit the rules. Each small config class covers one area, such as the database, blob storage, AI models, sandboxes, browser support, connectors, or artifacts. The top-level Config class gathers all of those sections into one complete deployment config.

The important behavior is that this file prefers to “fail loud.” For example, filesystem blob storage must name a root directory, S3 blob storage must name a bucket, model settings cannot be left as the vague value “auto,” and sandbox public ingress URLs must be secure HTTPS hostnames with no extra path or credentials. This prevents a server from starting with settings that would later cause confusing failures, unsafe routing, or broken links.

At the bottom, load_config finds the config file, reads TOML text from disk, and validates it into a Config object used by the rest of the system.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 39–52)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This fills in the separate DBOS system database URL when the deployer did not write one explicitly. DBOS is the system store used by the workflow layer, and keeping it separate from the main application database avoids mixing internal workflow data with user-facing data.

**Data flow**: It starts with the database section, especially database.url and possibly database.system_url. If system_url is already set, it leaves everything alone. If it is missing, it builds a sibling database name from the main URL: SQLite gets a nearby file name with _dbos added, while PostgreSQL gets a related database name and a synchronous driver form. The same DatabaseConfig object comes out with system_url filled in.

**Call relations**: This runs automatically while Pydantic validates the database config. Other code that later asks for database.system_url can rely on it being present, either because the user supplied it or because this validator derived it during config loading.


##### `BlobConfig._backend_complete`  (lines 74–79)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This checks that the blob storage settings include the required details for the chosen storage type. Blob storage is where larger objects such as transcripts, compaction records, and shared artifacts live.

**Data flow**: It receives the blob config after basic parsing. If the backend is filesystem, it checks that a local root path was provided. If the backend is s3, it checks that a bucket name was provided. If a required value is missing, it raises an error; otherwise, the unchanged BlobConfig object continues onward.

**Call relations**: This is called automatically during config validation. It protects later storage code from discovering too late that it has no directory to write to or no S3 bucket to address.


##### `ModelsConfig._models_concrete`  (lines 102–113)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This makes sure every deploy-level model setting names a real model, not the placeholder value 'auto' and not an empty string. That matters because these settings decide which AI model is actually paid for and called at runtime.

**Data flow**: It reads the three model choices: auto_model, ambient_reply_model, and background_jobs_model. For each one, it checks that the value is concrete. If any value is empty or still says 'auto', it raises a clear configuration error. If all are usable model IDs, the ModelsConfig object passes through unchanged.

**Call relations**: This validator runs when the models config is built. Later agent turns, ambient reply decisions, and background jobs can use these fields without having to repeat the same safety checks.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 201–225)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This verifies that the public base URL for sandbox ingress is a simple, secure HTTPS host. Sandbox ingress is the public doorway used to reach web services running inside a conversation's sandbox.

**Data flow**: It reads sandbox.ingress_public_url. If it is absent, it allows the config because some deployments may not expose sandbox sites. If it is present, it splits the URL into parts, then requires HTTPS and a hostname. It rejects paths, query strings, fragments, usernames, and passwords because sandbox site addresses are made by putting a subdomain label in front of the host. The result is either the same SandboxConfig object or a clear validation error.

**Call relations**: This runs during sandbox config validation and uses URL parsing to inspect the address. It protects two later readers of the setting: the ingress service that routes incoming requests, and code that creates public links to sandbox surfaces.

*Call graph*: 1 external calls (urlsplit).


##### `config_path`  (lines 351–352)

```
def config_path() -> Path
```

**Purpose**: This decides which configuration file path should be used. It lets an operator override the default ufo.toml location with the UFO_CONFIG environment variable.

**Data flow**: It reads the process environment for UFO_CONFIG. If that variable is set, it turns its value into a Path object. If it is not set, it uses the default path ufo.toml. The output is the Path that load_config should try to read.

**Call relations**: load_config calls this when no explicit path was passed in. This keeps the file-location rule in one small place so startup code can simply ask for the current config path.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 355–361)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This reads the deployment config file and turns it into a validated Config object. It is the main entry used by the rest of the system when it needs configuration.

**Data flow**: It starts with an optional path. If no path was provided, it asks config_path for the right file location. It checks that the file exists and raises a clear FileNotFoundError if it does not. Then it reads the file text, parses it as TOML, and asks the Config model to validate and assemble all sections. The output is a fully checked Config object.

**Call relations**: This is the top-level loader in this file. It calls config_path for the default location and tomllib.loads to parse TOML text. During Config validation, the section validators such as the database, blob, models, and sandbox checks run automatically before the config is handed to the rest of the application.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### Extension catalog and loading
Manages extension selection from catalogs and turns installed extension declarations into runtime capabilities.

### `core/src/ufo/ext/store.py`

`domain_logic` · `command handling`

This file is the bridge between “extensions that are available” and “extensions this UFO deployment should load.” The catalog is like a shop shelf: it lists extension names, versions, and whether an item is disabled for normal install. The lockfile is like a receipt or reservation slip: it records the exact extensions that UFO should boot with, including a digest, which is a fingerprint of the installed extension source.

The file defines small data shapes for catalog entries, whole catalogs, and search results. It can read a catalog from a TOML file, which is a human-readable configuration format. It can also ask Python what version of UFO is installed, because lockfiles are tied to a UFO version.

The main worker is `ExtensionStore`. Given one catalog and one lockfile path, it can search for matching extension names, install a catalog entry, or remove an installed entry. Installing does not download anything. Instead, it checks that the extension is already present in the current Python environment, computes its digest, and writes that exact pin to the lockfile. This matters because the loader later trusts the lockfile when deciding what to load. Without these checks, UFO could be told to load an extension that is missing, disabled, or different from what was intended.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file and turns it into a validated catalog object. Someone would use this before searching or installing, so the store knows what extensions are supposed to be available.

**Data flow**: It receives a file path. It reads the text from that file, parses the TOML text into ordinary data, and validates that the data matches the expected catalog shape. It returns a `Catalog` object containing the listed extensions.

**Call relations**: This is the intake point for catalog data. Higher-level command code can call it before creating an `ExtensionStore`; after that, store methods use the resulting catalog rather than reading the file again.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Looks up the installed version of the UFO package. This is used when creating a new lockfile so the lockfile records which UFO version it belongs to.

**Data flow**: It takes no input from the caller. It asks Python’s package metadata system for the version of the installed package named `ufo`, then returns that version string.

**Call relations**: `ExtensionStore._write` calls this only when it needs to create a lockfile that does not already exist. Existing lockfiles keep their recorded UFO version, while new ones get the current installed version.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an extension that is already installed. A pin records the extension name, its declared version, and a digest, which is a fingerprint used to identify the installed source.

**Data flow**: It receives an extension name. It asks the extension loader what extensions have been discovered in the current Python environment. If the name is missing, it raises an error instead of inventing a pin. If found, it reads the extension’s manifest version, computes a digest from the discovered package entry, and returns an `ExtensionPin`.

**Call relations**: `ExtensionStore.install` calls this after confirming the name exists in the catalog and is not disabled. This function is the safety check that connects a catalog entry to a real installed Python package before the lockfile is changed.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog for extension names containing a given piece of text and marks which results are already installed in the lockfile. It gives users a useful “what is available and what do I already have?” view.

**Data flow**: It receives a search string. It reads the current pins from the lockfile, builds a set of pinned extension names, then walks through the catalog entries whose names contain the search string. It returns a tuple of `StoreListing` results, each showing the name, catalog version, disabled flag, and whether that extension is currently pinned.

**Call relations**: This is used by store-facing command code when a user searches extensions. It relies on `ExtensionStore._pins` to learn the current installed state, but it does not write anything or change the lockfile.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins one extension into the lockfile so UFO will load it later. It refuses unknown catalog names and entries marked disabled, because disabled entries are meant only for bundling rather than normal install.

**Data flow**: It receives an extension name. It finds the matching catalog entry, stops with a clear error if the entry is absent or disabled, then asks `pin_for` to create a real pin from the installed Python package. It reads the existing pins, replaces any old pin with the same name, writes the updated pin list to the lockfile, and returns the new pin.

**Call relations**: This is the main install path for the extension store. It calls `pin_for` to verify and describe the installed extension, uses `_pins` to preserve other existing pins, and hands the final list to `_write` so the lockfile is updated.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile so UFO will no longer load that extension. It reports an error if the extension was not pinned in the first place.

**Data flow**: It receives an extension name. It reads the current pins, checks whether any pin has that name, and raises an error if none do. Otherwise, it filters that pin out and writes the remaining pins back to the lockfile. It returns nothing; the change is the updated file.

**Call relations**: This is the uninstall counterpart to `ExtensionStore.install`. It uses `_pins` to see what is currently recorded, then calls `_write` to save the shortened pin list.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current extension pins from the lockfile, or returns an empty list if there is no lockfile yet. It gives the rest of the store one simple way to ask, “what is currently installed?”

**Data flow**: It uses the store’s lockfile path. If the file exists, it reads and parses the lockfile and returns its extension pins. If the file does not exist, it returns an empty tuple, meaning no extensions are currently pinned.

**Call relations**: `search`, `install`, and `remove` all call this before deciding what to show or change. This keeps lockfile reading in one place instead of duplicating that logic across the store.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete new set of extension pins to the lockfile. It keeps the lockfile’s existing UFO version if one is already present, or records the current UFO version when creating a new lockfile.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. It checks whether the lockfile already exists; if so, it reads the existing UFO version as the anchor, and if not, it asks `ufo_version` for the installed UFO version. It builds a new `Lockfile` object with that version and the given pins, then writes it to disk.

**Call relations**: `install` and `remove` call this after they have decided the final pin list. This function is the final handoff from in-memory store decisions to the on-disk lockfile that the extension loader will later boot from.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and per-turn/request setup`

Extensions are how outside code adds abilities to UFO without editing the core project. This file acts like a careful theater stage manager: it reads the cast list, checks every actor is the right one, then places each actor on the right part of the stage. It discovers extensions through Python “entry points”, which are package-advertised functions. Each one returns a Manifest, a structured description of what the extension contributes.

A lockfile can pin the exact extension set. When present, the loader only accepts the named extensions and verifies a source-code digest, meaning a fingerprint of the installed files. If code changed after pinning, startup fails instead of quietly running different code. Without a lockfile, all discovered extensions are active, which is useful during development.

After loading manifests, this file builds the practical runtime pieces: the tool list a turn can call, credential injection rules for sandboxed work, object registries, skill registries, memory and embedding backends, and hook chains. Hooks are extension callbacks that run at moments like “before a tool is used” or “after a connection is recorded.” Some hooks can block an action; if those fail, the system refuses the action for safety. Others are observe-only; their failures are logged and ignored so they do not break the user’s turn.

#### Function details

##### `lockfile_path`  (lines 156–157)

```
def lockfile_path() -> Path
```

**Purpose**: Chooses where the extension lockfile lives. It lets operators override the default path with an environment variable, while keeping a normal default for local runs.

**Data flow**: It reads the process environment for the lockfile path setting. If that setting is present, it turns it into a Path object; otherwise it uses the default file name. It returns the Path the loader should check.

**Call relations**: When load_manifests decides which extensions are active, it asks this helper where to look for the lockfile before deciding whether to run in pinned mode or development mode.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 160–161)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads and validates the lockfile from disk. The lockfile says which extensions are allowed and what exact source digest each one must have.

**Data flow**: It receives a file path, reads the file text, and asks the Lockfile data model to parse and validate the JSON. It returns a Lockfile object or raises an error if the file is not valid.

**Call relations**: load_manifests calls this after it finds a lockfile, so the pinned extension list can be checked before anything is accepted as active.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 164–165)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a validated lockfile back to disk in a readable JSON format. Tools that pin or bundle extensions use this to save the operator’s chosen extension set.

**Data flow**: It receives a path and a Lockfile object. It converts the object to indented JSON, adds a final newline, and writes that text to the path. It changes the filesystem and returns nothing.

**Call relations**: This is the writer-side companion to read_lockfile. The loader reads the same shape of file that command-line tooling writes.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 168–182)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension in the current Python environment. It also refuses duplicate extension names and blocks third-party extensions from declaring privileged member-context access.

**Data flow**: It asks Python’s package metadata for all entry points in the UFO extension group. For each entry point, it loads the callable, calls it to get a Manifest, validates safety rules, and stores the manifest together with the entry point under the manifest name. It returns a dictionary keyed by extension name.

**Call relations**: load_manifests uses this as the raw installed-extension inventory. migration_locations also uses it to connect active manifests back to their installed package directories.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 185–196)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds installed packs, which are named bundles of extensions plus pack-level skills or onboarding steps. It refuses two packs with the same name.

**Data flow**: It reads Python package entry points for the pack group, loads each zero-argument callable, calls it to get a Pack, and stores each Pack by name. The result is a dictionary of all known packs.

**Call relations**: _pack_manifests calls this when configuration asks to activate one pack, so it can find the pack’s declared extension bundle.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 199–204)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds the import information for the top-level Python package that owns an extension entry point. This is needed to locate source files and migrations.

**Data flow**: It receives an entry point, takes the first part of its module name, and asks Python import machinery where that package or module comes from. It returns the import spec, or raises an error if the source cannot be found.

**Call relations**: extension_digest uses this to know what files to fingerprint. migration_locations uses it to find where an extension’s migration folder would be.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 207–212)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the directory that represents an extension’s package on disk. That directory is where extension database migrations are expected to sit.

**Data flow**: It receives an import spec. If the extension is a package, it returns the package directory; if it is a single file module, it returns that file’s parent directory.

**Call relations**: migration_locations uses this after _entry_spec so it can look for a migrations folder beside the extension’s code.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 215–235)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes a stable fingerprint for an installed extension’s source code. This lets the lockfile detect tampering or accidental drift.

**Data flow**: It receives an extension entry point, finds the source package, gathers source files while ignoring bytecode cache files, and hashes both file names and file contents in sorted order. It returns a string beginning with the sha256 prefix.

**Call relations**: load_manifests calls this for each pinned extension. If the computed digest does not match the lockfile, startup stops rather than running unexpected code.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 238–255)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Collects database migration folders from active extensions. Migrations are versioned database changes, and only active extensions should add their tables or schema changes.

**Data flow**: It discovers installed extensions, loads the active manifests, finds the package directory for each active installed extension, and checks for a migrations folder. It returns a tuple of folder paths as strings.

**Call relations**: Migration application code can call this to layer extension migrations alongside core migrations. It relies on load_manifests so inactive installed extensions do not affect the database.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 258–281)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Decides the active extension set for this run. It enforces the lockfile when present, or loads all discovered extensions when no lockfile exists.

**Data flow**: It first discovers installed extensions and checks the configured lockfile path. Without a lockfile, it returns all discovered manifests. With a lockfile, it reads pinned entries, verifies each extension is installed, checks its digest, and returns only the pinned manifests. If a pack name is supplied, it narrows the result through _pack_manifests.

**Call relations**: This is the central source of truth for active manifests. Other builders, such as migration_locations and the rest of the runtime setup, depend on this result so they all see the same extension set.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 284–316)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Turns a selected pack into the exact manifests that should be active for that pack. It ensures every bundled extension is installed and active.

**Data flow**: It receives a pack name and the already-active extension map. It discovers installed packs, finds the requested pack, collects the manifests for each bundled extension, checks for name collisions, then appends a synthetic Manifest for the pack’s own skills and onboarding steps. It returns the narrowed tuple of manifests.

**Call relations**: load_manifests delegates to this when configuration selects a pack. discovered_packs supplies the pack definitions, and the synthetic Manifest lets pack-level features flow through the same later code paths as normal extensions.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 319–328)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Builds a lookup of connector command-line credential settings by provider name. These settings tell the system which environment variable represents a usable grant for a connector.

**Data flow**: It receives active manifests, walks through their connectors, and keeps only connectors that declare a CLI credential. It returns a dictionary from provider name to CliCredential.

**Call relations**: injecting_slots calls this while checking all sandbox environment-variable claims, so connector credential variables and slot-injection variables are validated together.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 331–404)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into sandboxed work and validates that their names, sentinels, hosts, and environment variables do not conflict. This prevents silent authentication mistakes.

**Data flow**: It receives active manifests, extracts credential slots with injection rules, records connector CLI environment variables, and checks several shared namespaces: sentinel values, exported environment variable names, host choices, and metering dimensions. It returns the safe tuple of injectable slots, or raises an error if two declarations would collide or mislead the system.

**Call relations**: This sits between extension declarations and sandbox execution. It calls connector_clis so all credential-related environment variables are checked in one place before the engine or egress proxy uses them.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 407–471)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the full tool set available during a conversation turn. It combines built-in tools, extension tools, connector tools, and object-related tools, while remembering which extension context each extension tool should run under.

**Data flow**: It receives active manifests plus optional stores and services such as credentials, indexing, embeddings, blobs, audience, and URLs. For each extension with tools or object types, it verifies credential requirements, creates an ExtensionContext, adds declared tools, maps tool names to that context, binds object kinds, and finally adds object verb tools. It returns the complete tool tuple and the tool-to-context map.

**Call relations**: The turn engine uses this before dispatching tools. It calls context_for to create the scoped extension handle, core_object_kinds to add core credential and extension objects, and object_registry/ObjectVerbs to expose object operations as tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `member_object_registry`  (lines 474–508)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object registry used for member-facing reads outside a live turn, such as portal views. It includes core object kinds and extension object kinds.

**Data flow**: It receives active manifests and optional services. For each extension that declares object kinds, it checks whether credentials are required, creates a workspace-level ExtensionContext, binds the object kinds, adds core object kinds, and returns the registry keyed by object name.

**Call relations**: This mirrors part of turn_tools, but for reads that happen outside a conversation turn. It still uses core_object_kinds, context_for, and object_registry so the same object declarations behave consistently.

*Call graph*: calls 1 internal fn (core_object_kinds); 3 external calls (__init__, context_for, object_registry).


##### `core_object_kinds`  (lines 511–538)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None) -> tuple[BoundKind, ...]
```

**Purpose**: Creates the object kinds that UFO core itself exposes over the active extension set: credentials and extensions. These let users or tools inspect declared credential slots and installed extensions as objects.

**Data flow**: It receives manifests and an optional credential store. It builds a credential object kind from declared credential slots and a live store, builds an extension object kind from named manifests, wraps both as unowned BoundKind values, and returns them.

**Call relations**: turn_tools, member_object_registry, and validate_ext_tools call this whenever they need the core object types included beside extension-owned object types.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 6 external calls (__init__, __init__, __init__, __init__, named_extensions, declared_slots).


##### `skill_registry`  (lines 541–564)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the complete set of loadable skills for the deployment. Skills are reusable instruction or behavior packages, and duplicate names are rejected early.

**Data flow**: It starts with core skills, then walks active manifests and discovers skills from each declared skill path. It adds generated skills too, checking every name for collision. It returns a SkillRegistry containing the final name-to-skill mapping.

**Call relations**: Startup code can call this once after manifests are loaded. It delegates disk discovery to discover_skills and gives later skill resolvers one unambiguous registry.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 567–571)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized assistant role the system can make available.

**Data flow**: It receives manifests and flattens every manifest’s subagent profiles in load order. It returns them as a tuple without changing anything else.

**Call relations**: The serving layer can use this output to build the SubagentRegistry. Name conflicts are left for that registry to reject when it is constructed.


##### `durable_surfaces`  (lines 574–580)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds which conversation surfaces need durable writeback behavior. A surface is durable when it declares a post handler, meaning replies may need to be delivered later by a poller.

**Data flow**: It receives manifests, scans their surface declarations, keeps names whose surface has a post handler, and returns a frozen set of those names.

**Call relations**: Admission or turn setup code can use this to decide when to create writeback tracking rows for conversations entering those surfaces.


##### `turn_subagent_grants`  (lines 583–593)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects extra tool permissions that extensions grant to subagent profiles they may not own. This lets an extension widen a profile’s tools without hard-coding that change inside the profile itself.

**Data flow**: It receives manifests, walks all subagent tool grant declarations, groups tool names by target profile, unions duplicates, and returns a dictionary from profile name to frozen tool-name set.

**Call relations**: The turn loop can combine this with a profile’s own tool list, then intersect with the live tool registry. Unknown profiles or unavailable tools naturally disappear at that later intersection.


##### `turn_runtime_skills`  (lines 596–616)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Asks extensions for runtime-generated skills for the current agent or workspace. These are skills that cannot be fully known from static files at startup.

**Data flow**: It receives manifests, optional credentials, and optional index/embed services. For each manifest with a runtime skill provider, it requires a credential store, builds an ExtensionContext, awaits the provider, and appends the returned skills. It returns all runtime skills as a tuple.

**Call relations**: This is used during turn-related setup when live extension context is available. It calls context_for so each provider runs with the same scoped access model as other extension code.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 622–642)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Chooses and builds the workspace indexing backend. An index backend stores and searches text or vector data, and extensions provide named backend factories.

**Data flow**: It receives active manifests, an optional configured backend name, and an optional credential store. It chooses the configured name or the default, searches extension index specs for that name, checks credential requirements, creates an ExtensionContext, and returns the backend produced by the spec factory. If none is registered, it raises NotRegisteredError.

**Call relations**: Startup or workspace setup calls this when wiring search/index services. The NotRegisteredError path is intentionally distinct so tests and callers can tell “not registered” apart from other boot failures.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 645–665)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Chooses and builds the embedding client. An embedding client turns text into numeric vectors so the system can compare meaning, and extensions provide named implementations.

**Data flow**: It receives active manifests, an optional configured name, and an optional credential store. It chooses a name, scans embed specs, checks credentials if needed, builds an ExtensionContext, and calls the matching factory. It returns the EmbedClient or raises NotRegisteredError if no extension provides the selected name.

**Call relations**: This is resolved during service setup and then passed into indexing, memory, jobs, and extension contexts. It follows the same selection pattern as index_backend.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 668–693)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds a named memory search provider if one active extension declares it. Memory search is a feature for finding relevant stored memories.

**Data flow**: It receives manifests, optional credentials, optional index/embed services, and a provider name. It collects matching providers, returns None if there are none, rejects duplicates, checks credential requirements, builds an ExtensionContext, and wraps the provider’s built implementation in MemorySearch.

**Call relations**: Callers use this when they want the deploy’s configured memory search feature. It uses context_for so the provider runs under the extension that declared it.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 696–732)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Performs early boot validation for extension tools and object kinds. It makes misconfiguration fail at startup instead of during a user’s turn.

**Data flow**: It receives manifests and an optional credential store. It gathers built-in and extension tools, checks that credential-requiring tool extensions have a credential key, builds an object registry including extension and core kinds, adds object verb tools, and constructs a ToolRegistry to trigger duplicate-name and registration checks. It returns nothing if everything is valid.

**Call relations**: Deployment startup can call this before serving traffic. It uses core_object_kinds, object_registry, ObjectVerbs, and ToolRegistry to exercise the same naming gates later runtime code depends on.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.__post_init__`  (lines 773–776)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that every hook in a HookChain belongs to the same audience as the chain. An audience is the intended visibility or conversation scope for the hook work.

**Data flow**: After a HookChain is created, it flattens all bound hooks and compares each hook context’s audience with the chain’s audience. If any differ, it raises a ValueError; otherwise the chain remains unchanged.

**Call relations**: turn_hooks creates HookChain instances. This method runs automatically after construction to catch mismatched hook context before any hook fires.


##### `HookChain.fire`  (lines 778–854)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one turn event and folds their results into one decision. Hooks can deny an action, modify tool input or output, or add extra context, depending on the event.

**Data flow**: It receives an event, a payload, optional turn and agent records, and an optional speaker member id. It selects hooks for the event, skips tool-specific hooks that do not match, builds a HookContext for each, runs the handler with a timeout, validates that the returned outcome is allowed, and updates the accumulated resolution. It returns a HookResolution describing denial, changed input/output, injected text, or swallowed failures.

**Call relations**: The turn engine calls this at hook points such as before tool use or after tool use. For safety, failures in gating events become a denial; failures in non-gating events are logged and ignored so observation hooks cannot break the turn.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 857–901)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the HookChain used during a conversation turn. It binds each declared turn-lifecycle hook to the ExtensionContext of the extension that owns it.

**Data flow**: It receives manifests, credentials, optional services, a tailer, audience, and optional public URL. It groups supported turn events, checks that hook-declaring extensions have credentials available, creates each extension context, wraps each hook spec as a BoundHook, and returns a HookChain.

**Call relations**: Turn setup calls this before the engine starts firing lifecycle events. It calls context_for for each hook-owning extension and relies on HookChain to enforce audience consistency.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 916–927)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Runs observe-only hooks after a connection has been recorded. These hooks let extensions react to a newly connected account, for example by creating related feeds or jobs.

**Data flow**: It receives a ConnectionRecorded payload and runs each bound handler with a timeout using a HookContext. If a handler fails or times out, it logs the failure and continues. It returns nothing and does not undo the recorded connection.

**Call relations**: The connect flow calls this after the connection is committed. Unlike gating turn hooks, these hooks cannot block the connection; failures are left for extension jobs or retries to repair.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 930–956)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the hook chain for connection-recorded events. It gathers only hooks that care about newly recorded connections.

**Data flow**: It receives manifests, credentials, and optional index/embed services. For each manifest with connection_recorded hooks, it checks credential availability, creates an ExtensionContext, binds each matching hook spec, and returns a ConnectionHookChain containing them.

**Call relations**: Connection setup code calls this to prepare the chain that ConnectionHookChain.fire will publish to after a successful connection handoff.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### Runtime backend setup
Selects sandbox and shared service backends while centralizing sensitive startup connectivity decisions.

### `core/src/ufo/sandbox/select.py`

`orchestration` · `startup`

A sandbox is a contained place where work can run without freely touching the host system. This file is the project’s “sandbox selector”: it takes the user’s configuration and the list of installed extension manifests, then picks exactly one sandbox carrier, meaning the backend that can create and run sandboxes.

It starts with a built-in option called `local`, which runs sandboxes through the core project’s own `LocalCarrier`. Then it adds any carriers advertised by extensions. If two carriers try to use the same backend name, it stops immediately. This avoids a dangerous guessing game where the same name could mean two different systems.

After building that menu, it looks up the backend named in `[sandbox] backend`. If nothing registered that name, it raises a clear error showing what names are available.

There is one extra safety check for remote, or “off-cluster,” sandboxes. A remote sandbox cannot automatically reach a proxy running only inside this process. So the configuration must provide `[sandbox] proxy_public_url`, and it must be an HTTPS URL. That matters because the proxy injects credentials, blocks traffic by default, and measures usage. Without this check, a remote sandbox might run with unsafe or unmetered network access.

#### Function details

##### `select_carrier`  (lines 12–51)

```
def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Chooses the one sandbox backend this process should use, based on configuration and extension-provided carrier registrations. It also enforces safety rules so duplicate names, unknown backends, and unsafe remote proxy settings fail loudly instead of becoming hidden misconfiguration.

**Data flow**: It receives the loaded `Config` and a tuple of extension `Manifest` objects. It builds a name-to-carrier map starting with the built-in `local` carrier, adds each extension carrier after checking for duplicate names, then looks up the configured sandbox backend. If the backend is missing, it raises a `NotRegisteredError`. If the chosen carrier runs remotely, it reads `proxy_public_url`, parses it as a URL, and rejects it unless it is a valid HTTPS address. If everything is valid, it creates the carrier by calling the selected factory and returns both the new carrier object and its `CarrierSpec` description.

**Call relations**: This function sits at the point where runtime setup turns configuration into a concrete sandbox backend. It creates the built-in `CarrierSpec` for the local carrier, uses `NotRegisteredError` when the requested backend name was never registered, and uses `urllib.parse.urlparse` to inspect the public proxy URL for remote carriers. The returned carrier is what later runtime code can keep and use to start sandbox sessions, while the returned spec tells callers important facts about that backend, such as whether it is remote and what sandbox sizes it supports.

*Call graph*: 3 external calls (__init__, __init__, urlparse).


### `core/src/ufo/proxy_serve.py`

`orchestration` · `startup`

This file is a small but important safety gate for shared UFO services. Sandboxes are not allowed to freely call the internet. They need explicit egress rules, meaning rules for what outbound network traffic is allowed. The `model_rule_base` function builds the common set of rules that lets a sandbox reach configured AI model providers, such as Anthropic or OpenAI, but only when the matching API key is actually present in the environment. It also arranges for placeholder secrets to be swapped for the real key when the request leaves the sandbox. If no model key is available, it stops immediately, because a sandbox with no allowed model route would fail later in a more confusing way.

The file also defines `owner_dsn`, which chooses the database connection string used by a shared ingress service. This connection bypasses RLS, or row-level security, which is a database feature that normally limits which rows a user can see. Because this shared service can serve many workspaces, the code expects queries to be explicitly filtered by workspace instead. That makes the owner connection powerful and sensitive, so the helper fails loudly if the required secret is missing. It also rewrites a plain PostgreSQL URL so the async PostgreSQL driver used by this project is selected.

#### Function details

##### `model_rule_base`  (lines 17–36)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic outbound network rules that every sandbox needs in order to call configured AI model providers. It only enables providers whose API key environment variable is set, and it refuses to continue if none are available.

**Data flow**: It receives the project configuration and reads the environment variable names stored under the model settings. For each configured provider, it looks up the real API key in the process environment, asks `derive_model_rules` to turn that provider probe and key into egress rules, gathers all allowed host names into one `ScopeRule`, and keeps any extra rules alongside it. It returns a tuple of rules ready to be added to a sandbox, or raises an error if no provider key was found.

**Call relations**: During service setup, callers use this as the shared source of truth for model-provider access. Inside the function, it hands each provider probe and key to `ufo.egress_rules.derive_model_rules`, then wraps the collected allowed hosts in a `ScopeRule` so the sandbox egress layer knows which model hosts are reachable.

*Call graph*: 2 external calls (__init__, derive_model_rules).


##### `owner_dsn`  (lines 39–51)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Chooses the privileged database connection string for the shared ingress service. It looks first in the `UFO_OWNER_DSN` environment variable, then falls back to the configuration file, and stops with a clear error if neither is set.

**Data flow**: It receives the project configuration and reads one environment variable plus the configured `database.owner_url` value. It selects the first available connection string, checks that one exists, then rewrites a leading `postgresql://` prefix to `postgresql+psycopg://` so the intended async PostgreSQL driver is used. It returns the final database URL as a string, or raises an error if no owner database URL was provided.

**Call relations**: Shared ingress setup calls this when it needs to open the database as the owner role rather than using a workspace-scoped database URL. Unlike `model_rule_base`, it does not hand off to other project helpers; its job is to validate and normalize the secret before the database layer uses it.


### Agent provisioning and governance
Copies extension-provided agents into workspaces and protects later prompt changes through approval proposals.

### `core/src/ufo/governance.py`

`domain_logic` · `during agent configuration change proposal and approval`

This file is a safety gate for changing an agent's configuration, specifically its prompt. Instead of letting code overwrite an agent prompt directly, it uses a proposal workflow: someone proposes a new prompt, the system records what prompt version they thought they were changing, and later an approval step checks that the same old prompt is still in place before applying the new one. This is like editing a shared document only if nobody else has changed the paragraph since you last read it.

The key idea is a digest, which is a short fingerprint made from the prompt text. When a proposal is opened, the file stores the fingerprint of the current prompt that the proposer expects, plus the fingerprint and text of the new prompt. When the proposal is approved, the file locks and rereads the agent's current prompt from the database. If the current fingerprint does not match the proposal's expected fingerprint, the proposal is rejected instead of overwriting newer work. If it does match, the prompt is updated and the proposal is marked approved.

All database work happens inside a workspace transaction, meaning the checks and writes are grouped together for one workspace. The file also logs important outcomes, such as proposals being approved or rejected.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: Creates a stable fingerprint for a prompt. The system uses this fingerprint to tell whether a prompt is still exactly the same as it was when a proposal was made.

**Data flow**: It takes prompt text as input, turns it into bytes, runs it through SHA-256, which is a standard one-way fingerprinting method, and returns the fingerprint as a string. It does not change anything outside itself.

**Call relations**: When a change is proposed, Governance.propose_change uses this to record the fingerprint of the new prompt. When a proposal is approved, Governance.approve_proposal uses it again to compare the proposal's expected old prompt with the agent's current prompt.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Opens a new proposal to change an agent's prompt. It records the requested change but deliberately does not update the agent yet.

**Data flow**: It receives an AgentChange that names the agent, says what old prompt fingerprint the proposer expects, and includes the new prompt text. It creates a new proposal ID, checks that the agent exists in this workspace, stores the proposal in the database with a pending status, and returns a ProposalRef containing the new proposal ID. If the agent is not found in the workspace, it raises an error instead of creating a proposal.

**Call relations**: This is the first half of the governance flow. It opens a database transaction with workspace_tx, uses sqlalchemy queries to check and insert rows, calls prompt_digest to fingerprint the proposed new prompt, and returns a ProposalRef so later code can refer to the proposal during approval.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: Approves a pending proposal only if it is still safe to apply. If the agent prompt has changed since the proposal was created, it rejects the proposal instead of overwriting newer data.

**Data flow**: It receives a proposal ID, loads that proposal for the current workspace, and verifies that it exists and is still pending. It then reads and locks the target agent's current prompt in the database so another update cannot slip in during the check. If the current prompt fingerprint differs from the proposal's expected fingerprint, it marks the proposal rejected and logs that result. If the fingerprints match, it writes the proposed prompt to the agent, marks the proposal approved, and logs the approval.

**Call relations**: This is the second half of the governance flow, used after Governance.propose_change has created a pending proposal. It uses workspace_tx so the read, safety check, and database updates happen as one protected unit. It calls prompt_digest for the safety comparison, uses sqlalchemy to read and update proposal and agent rows, and calls ufo.o11y.log to record whether the proposal was approved or rejected.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `core/src/ufo/provisioning.py`

`domain_logic` · `workspace provisioning / first use of active extensions`

Extensions can ship ready-made agents, but a workspace needs its own database row for each agent before people can use it. This file is the bridge between those two worlds. It reads the active extension manifests, finds each declared agent, and makes sure the workspace has a matching agent row.

The important rule is: once an agent is placed into a workspace, the extension stops owning the live copy. The row records which extension and version created it, but later extension versions do not rewrite it. That protects any edits a member makes.

For each provisioned agent, the code first checks whether this exact extension/name pair has already been added. If so, it reports that it is already present. If not, it checks whether the workspace already has an ordinary, member-owned agent with the same name and exactly the same settings. In that special case, it “adopts” the existing row by marking it as provisioned, instead of making a duplicate.

If neither case applies, it creates a new agent. If the preferred name is already taken, it tries clear variants like the declared name plus the extension name, then numbered versions. This is like putting a label on a duplicate folder rather than replacing the original folder. The database insert is also written to tolerate a race where two requests try to create the same provisioned agent at the same time.

#### Function details

##### `AgentProvisioning.apply`  (lines 48–56)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies all agent provisions from all active extension manifests to one workspace. A caller uses this when it wants the workspace to have the agents that its installed extensions ship.

**Data flow**: It receives a workspace ID and reads the manifests stored on the AgentProvisioning object. It enters that workspace’s context, walks through every manifest and every agent provision inside it, asks _one to apply each provision, and returns a tuple of outcomes saying what happened for each agent.

**Call relations**: This is the public starting point for the file’s work. It sets the workspace context with ws so lower-level code runs for the right workspace, then repeatedly hands individual provisions to AgentProvisioning._one, which does the database checks and writes.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 58–109)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Applies one extension-declared agent to one workspace. It decides whether the agent is already there, can be adopted from an identical existing member-created agent, or must be newly created.

**Data flow**: It takes a workspace ID, one extension manifest, and one agent provision. Inside a workspace database transaction, it first looks for an existing row already marked as coming from this extension and declared name. If found, it returns a “present” outcome. Otherwise it looks for an unprovisioned agent with the same name and compares its settings. If the settings match, it updates that existing row to record the extension source and returns an “adopted” outcome. If no suitable row exists, it chooses a free name, creates a new agent row, and returns a “created” outcome.

**Call relations**: AgentProvisioning.apply calls this once per provision. This function is the decision-maker: it uses _identical to judge whether an existing row is truly the same agent, _free_name to avoid name collisions, and _create to write a new row when needed. It wraps the database work in workspace_tx so the checks and writes happen safely as one unit.

*Call graph*: calls 3 internal fn (_create, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._free_name`  (lines 111–133)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Finds a usable agent name when an extension’s requested name might already be taken. It protects existing agents by choosing a variant instead of overwriting anything.

**Data flow**: It receives a database connection, workspace ID, extension name, and declared agent name. It reads all agent names already used in that workspace. It then tries the declared name first, then a name with the extension appended, then numbered versions, and returns the first one not already taken. If it cannot find one within the configured limit, it raises an error.

**Call relations**: AgentProvisioning._one calls this only when it needs to create a new provisioned agent. The name it returns is passed straight to _create, so the new row can be inserted under a name that members can distinguish from any existing agent.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 135–188)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Creates the actual workspace agent row for one provisioned agent. It records the extension source and initial settings, but does not grant extra powers or credentials by itself.

**Data flow**: It receives an open database connection, workspace ID, manifest, provision, and final chosen name. It reads the icons already used in the workspace so it can choose a suitable automatic icon. It then builds an insert containing the agent’s prompt, model, tools, visibility, sandbox and internet settings, setup information, extension identity, extension version, timestamps, and a new unique ID. The database receives this insert, but if another request already inserted the same provisioned agent first, the insert quietly does nothing instead of failing.

**Call relations**: AgentProvisioning._one calls this after it has decided a new row is needed and _free_name has chosen a name. This function delegates icon choice to auto_agent_icon and ID creation to uuid4, then relies on the database conflict rule to make concurrent provisioning safe.

*Call graph*: called by 1 (_one); 4 external calls (execute, select, auto_agent_icon, uuid4).


##### `AgentProvisioning._identical`  (lines 190–203)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing unprovisioned agent row has the same settings as an extension provision would create. This allows the system to adopt a matching existing agent instead of making a duplicate.

**Data flow**: It receives a database row and an agent provision. It compares the row’s stored prompt, model, reasoning setting, internet permission, sandbox size, visibility, and tools against the provision’s specification. It returns true only when all compared values match.

**Call relations**: AgentProvisioning._one uses this during the adoption path. If _identical says the existing row really matches the provision, _one updates that row to mark it as provisioned; otherwise _one treats the provision as needing a separate newly named agent.

*Call graph*: called by 1 (_one).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-extension-install-state` — The persisted extension-store pins, install/remove choices, and update-check metadata used to decide which extension packages should be discovered and loaded.
- `reg-prompt-change-proposals` — The durable proposal and governance state for suggested agent prompt or behavior changes, including approval and offline-improvement outcomes before agent settings are rewritten.
- `reg-skill-assets-state` — The discovered skill packages, dependency metadata, copied helper files, and per-agent skill asset state used when building prompts and executing skill-backed work.
- `reg-sandbox-image-cache` — The local or remote sandbox image/build cache and validation state used to choose, compare, and launch safe execution environments.
- `reg-extension-kv-store` — Per-workspace extension key/value JSON and setup marker state saved outside core schemas, used by extension setup, runtime behavior, jobs, and cleanup migrations.
