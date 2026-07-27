# Capability discovery and extension activation  `stage-2`

This stage runs during startup, before the system begins its main work. Its job is to decide what extra abilities are allowed and make them available to the host. The extension store is like an app-store manager for the command-line tool: it can search a catalog, install an extension by recording it in a lockfile, or remove it. The lockfile is the trusted saved list of extensions that should be loaded.

The loader is the main switchboard. It reads the installed and pinned extensions, checks that the lockfile still matches what is present, then reads each extension’s manifest, which is a small description of what the extension contributes. From that it registers usable pieces such as tools, hooks, skills, credential slots, search or sandbox backends, sources, models, jobs, surfaces, and onboarding steps.

The many `__init__.py` files in extension folders mostly act as nameplates. They make folders importable Python packages, so the loader can reach extensions like Slack, web, research, sites, scheduled tasks, skill creation, and self-improvement.

## Files in this stage

### Extension store and loader
Core extension infrastructure manages the lockfile/catalog path and turns trusted manifests into active host capabilities.

### `core/src/ufo/ext/store.py`

`domain_logic` · `extension administration, when ufoctl ext searches, installs, or removes extensions`

This file solves a practical bookkeeping problem: an extension may be listed as available, but UFO also needs a reliable saved record of exactly which extensions should load, including a digest, which is a fingerprint of the installed package contents. Think of the catalog as a shop shelf and the lockfile as the receipt that says exactly what was chosen.

The catalog is read from a TOML file, a simple configuration-file format. Each catalog entry has a name, a version, and an optional disabled flag. Disabled entries are “bundle-only”: they can be pinned by a bundle command, but this store refuses to install them directly.

The main class, ExtensionStore, connects one catalog to one lockfile. Searching looks through catalog names and marks which ones are already pinned in the lockfile. Installing first checks that the requested name exists in the catalog and is not disabled. It then verifies that the Python package is actually installed in the current environment, builds an ExtensionPin with the package version and digest, and writes the updated lockfile. Removing does the reverse: it checks that the extension is currently pinned, then rewrites the lockfile without it.

A key safety behavior is that the store will not pin an extension merely because it appears in the catalog. The extension must be discoverable in the current Python environment, otherwise installation fails loudly.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a validated Catalog object. This gives the rest of the store a clean, predictable list of available extensions instead of raw text.

**Data flow**: It receives a filesystem path. It reads the text at that path, parses the TOML text into ordinary data, then validates that data against the Catalog shape. The result is a Catalog containing catalog entries with names, versions, and disabled flags.

**Call relations**: This is the doorway from a catalog file into the store logic. It relies on the path object to read the file and on tomllib to understand TOML, then hands back a Catalog that can be used to build an ExtensionStore.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Looks up the installed version of the UFO package itself. This is used when creating a new lockfile so the lockfile records which UFO version it belongs with.

**Data flow**: It takes no input from the caller. It asks Python package metadata for the version of the installed package named "ufo". It returns that version as a string.

**Call relations**: ExtensionStore._write calls this only when there is no existing lockfile to copy a version from. In that case, this function provides the version anchor for the new lockfile.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an installed extension. A pin records the extension name, the version from its manifest, and a digest fingerprint of its installed source.

**Data flow**: It receives an extension name. It asks the extension loader what extensions are discovered in the current Python environment, looks up that name, and fails if the package is not actually installed. If found, it reads the extension's manifest version, computes a digest for the installed entry, and returns an ExtensionPin.

**Call relations**: ExtensionStore.install calls this after checking the catalog. This function is the safety gate that prevents the store from pinning a catalog entry that is not really present in the running environment.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and reports which matching extensions are already installed according to the lockfile. It is used to show a user what is available and what is already pinned.

**Data flow**: It receives a search query string. It reads the current pins from the lockfile, makes a set of pinned extension names, then scans every catalog entry whose name contains the query text. It returns StoreListing objects that include the catalog name, version, disabled status, and whether that name is currently pinned.

**Call relations**: This is the read-only path through ExtensionStore. It calls ExtensionStore._pins to learn the current lockfile state, then packages catalog entries into StoreListing results for callers such as a command-line search command.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins an extension into the lockfile so UFO will load it later. It enforces the catalog rules: the extension must be listed, must not be marked disabled, and must actually be installed in the current environment.

**Data flow**: It receives an extension name. It searches the catalog for that exact name; if missing, it raises an error. If the entry is disabled, it raises an error explaining that it is bundle-only. Otherwise it asks pin_for to create the real installed-package pin, removes any older pin for the same name, writes the updated pin list to the lockfile, and returns the new pin.

**Call relations**: This is the main write path for adding an extension. It uses pin_for to prove the package exists and compute its fingerprint, uses ExtensionStore._pins to preserve other existing pins, and hands the final tuple of pins to ExtensionStore._write.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. This stops that extension from being listed as installed by the store and from being loaded through this lockfile.

**Data flow**: It receives an extension name. It reads all current pins, checks that at least one pin has that name, and raises an error if not. If the pin exists, it filters that pin out and writes the remaining pins back to the lockfile. It returns nothing.

**Call relations**: This is the reverse of install. It calls ExtensionStore._pins to see what is currently pinned, then calls ExtensionStore._write to save the shortened list.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current extension pins from the lockfile, or returns an empty list if no lockfile exists yet. It hides the “file may not exist” detail from search, install, and remove.

**Data flow**: It uses the store's lockfile path. If the file exists, it reads and parses the lockfile and returns its extensions tuple. If the file does not exist, it returns an empty tuple.

**Call relations**: ExtensionStore.search, ExtensionStore.install, and ExtensionStore.remove all call this before deciding what to show or change. It is the shared read helper for the store's current installed-extension state.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete new set of extension pins to the lockfile. It also preserves the lockfile's existing UFO version when possible, so changing extensions does not accidentally change that recorded version.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. If a lockfile already exists, it reads that file and reuses its stored UFO version. If not, it asks ufo_version for the current installed UFO version. It then builds a Lockfile object from that version and the given pins, and writes it to disk.

**Call relations**: ExtensionStore.install and ExtensionStore.remove call this after they have decided the new pin list. This function is the final save step: it packages the chosen pins into a Lockfile and delegates the actual file writing to the loader's write_lockfile helper.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/ext/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a sign on a folder saying, “this folder is a package you can import from.” This particular file is empty, so it does not run setup code, define helper functions, or expose any named objects directly. Its value is structural: it lets the rest of the project treat `core/src/ufo/ext` as the home for extension-related code. Without this file, some Python environments or tooling might not recognize the folder as an importable package, which could make imports fail or behave differently. Think of it as a labeled drawer in a filing cabinet: the drawer may not contain instructions itself, but the label helps the system know where related items belong.


### `core/src/ufo/ext/loader.py`

`orchestration` · `startup, config load, and per-turn setup/hooks`

Extensions are how UFO grows new abilities without changing the core program. This file is the switchboard for those abilities. It discovers installed extension packages through Python entry points, asks each one for a manifest, and rejects unsafe situations such as two extensions using the same name. If a lockfile exists, it acts like a sealed shipping list: only the named extensions may load, and their source code must still match the recorded digest. Without this check, a deployed system could silently run different extension code than the operator approved.

After discovery, the file translates manifests into the runtime pieces UFO needs. It builds the tool list for a turn, registers object kinds, gathers skill definitions, chooses index and embedding backends, exposes connector command-line credentials, validates credential injection rules, and assembles reactive hooks that can approve, modify, or observe turn events.

A useful analogy is an airport gate. Extensions may arrive installed in the environment, but this loader checks their ticket, verifies their identity, and then sends each one to the correct lane: tools, hooks, migrations, skills, credentials, or backends. It deliberately fails loudly on ambiguity or missing security setup, because silent guessing here would mean the assistant runs with the wrong powers.

#### Function details

##### `lockfile_path`  (lines 136–137)

```
def lockfile_path() -> Path
```

**Purpose**: Finds the lockfile path the loader should use. It lets an operator override the default `ufo.lock` file with an environment variable.

**Data flow**: It reads the `UFO_LOCKFILE` environment variable. If that variable is set, it turns that value into a filesystem path; otherwise it uses `ufo.lock`. The result is a `Path` object pointing to the lockfile location.

**Call relations**: When `load_manifests` decides whether the deployment is pinned or in development mode, it first asks this function where the lockfile should be.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 140–141)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads and validates the lockfile from disk. This makes sure the pinned extension list has the expected shape before the loader trusts it.

**Data flow**: It receives a filesystem path, reads the file text, parses it as JSON, and turns it into a validated `Lockfile` object. The output is structured data containing the pinned UFO version and extension pins.

**Call relations**: When `load_manifests` sees that a lockfile exists, it calls this function before checking each pinned extension and digest.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 144–145)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a validated lockfile to disk in a readable JSON format. This is the save side of the lockfile contract used by tooling that pins extensions.

**Data flow**: It receives a path and a `Lockfile` object. It serializes the lockfile as indented JSON, adds a final newline, and writes that text to the target file. It changes the filesystem but returns nothing.

**Call relations**: This function is the counterpart to `read_lockfile`: command-line or bundling workflows can write the pinned set, and later `load_manifests` reads that same file at boot.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 148–158)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension available in the current Python environment. It also prevents two extensions from claiming the same manifest name.

**Data flow**: It asks Python for all entry points in the `ufo.extension` group. For each entry point, it loads and calls the extension's zero-argument factory to get a manifest. It returns a dictionary from manifest name to the manifest and its entry point, or raises an error if a name is duplicated.

**Call relations**: `load_manifests` uses this as the raw list of installed extensions before applying the lockfile or pack filter. `migration_locations` also uses it so it can find the source package that owns each active extension.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 161–172)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds every installed UFO pack. A pack is a bundled set of extensions and pack-level skills or onboarding steps.

**Data flow**: It asks Python for entry points in the `ufo.pack` group, loads each pack factory, calls it, and stores the resulting pack by name. It returns the name-to-pack dictionary, or raises an error if two packs use the same name.

**Call relations**: `_pack_manifests` calls this when configuration selects a pack, so the loader can narrow the active manifest set to exactly what that pack declares.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 175–180)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds the import information for the Python package that owns an extension entry point. The loader needs this to locate source files and migrations.

**Data flow**: It receives an entry point, takes the top-level module name, and asks Python's import system where that module comes from. It returns a module specification, or raises an error if no importable source can be found.

**Call relations**: `extension_digest` uses this to find the files that should be hashed. `migration_locations` uses it to find an extension's package directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 183–188)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Turns a module specification into the directory that belongs to the extension package. This is mainly used to look for an extension's database migration files.

**Data flow**: It receives a module specification. If the extension is a package with multiple files, it returns the package directory; if it is a single file module, it returns that file's parent directory.

**Call relations**: `migration_locations` calls this after `_entry_spec` so it can check whether a `migrations` folder exists beside the extension code.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 191–211)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the security fingerprint for an installed extension. This lets the loader detect if extension source code has changed since it was pinned.

**Data flow**: It receives an extension entry point, finds the package source, reads every relevant source file, and feeds both file names and file contents into SHA-256 hashing. It skips bytecode cache files because they are machine-generated. The output is a string starting with `sha256:`.

**Call relations**: `load_manifests` calls this for every lockfile-pinned extension and compares the result with the pinned digest. If they differ, boot stops instead of running drifted code.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 214–231)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Lists the database migration folders contributed by active extensions. This lets the database upgrader include extension tables only for extensions that are actually enabled.

**Data flow**: It discovers installed extensions, loads the active manifest set, optionally narrowed to a pack, and for each active installed extension looks for a `migrations` directory beside its package. It returns the matching directories as strings.

**Call relations**: It combines `discovered`, `load_manifests`, `_entry_spec`, and `_package_dir`: first decide which extensions count, then locate their packages, then report only the migration folders that exist.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 234–259)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Builds the active extension manifest list for this run. This is the central decision point for whether the system uses all discovered extensions, a locked pinned set, or one selected pack.

**Data flow**: It discovers installed extensions and checks for a lockfile. Without a lockfile, all discovered manifests become active. With a lockfile, each pinned extension must be installed and its digest must match. If a pack name is supplied, it then narrows the active set through `_pack_manifests`. The result is an ordered tuple of manifests.

**Call relations**: Many later derivations depend on this active manifest list. `migration_locations` calls it directly, and other startup flows use its output to build tools, hooks, skills, credentials, and backends.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 262–294)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Applies a selected pack to the active extension set. It makes sure the pack exists and that every extension the pack bundles is installed and active.

**Data flow**: It receives a pack name and the already-active manifests by extension name. It looks up the pack, gathers the manifests for each bundled extension, rejects missing inactive members, and appends a synthetic manifest for the pack's own skills and onboarding steps. It returns the narrowed tuple.

**Call relations**: `load_manifests` calls this only when configuration selects a pack. It relies on `discovered_packs` for the installed pack list and produces manifests that downstream code can read exactly like normal extension manifests.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 297–306)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These declarations tell UFO which environment variable should carry a connector's grant sentinel.

**Data flow**: It receives active manifests, scans every connector in every manifest, and keeps only connectors that declare CLI credential information. It returns a dictionary keyed by provider name.

**Call relations**: `injecting_slots` calls this while checking the shared sandbox environment variable namespace, so connector credentials and injected credential slots cannot silently overwrite each other.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 309–382)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that need to be injected into sandboxed tool runs and checks that their names, sentinels, hosts, and environment variables do not conflict. This prevents subtle authentication bugs where one secret or variable would silently win over another.

**Data flow**: It receives active manifests, extracts slots with injection rules, and also reads connector CLI credential exports. It checks that one sentinel is not shared by different slots, that metering dimensions agree for the same reachable host, that host-choice slots refer to declared slots, and that environment variable names do not carry different meanings. It returns the valid injectable slots or raises an error.

**Call relations**: It calls `connector_clis` so connector-provided environment exports and slot-provided exports are validated together. The proxy and sandbox export logic can then rely on one clean, conflict-free list.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 385–433)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[tuple[ToolDef, ...], dict[str, Exten
```

**Purpose**: Builds the complete tool set available during a turn. It includes core built-in tools, extension tools, connector tools, and generic object-operation tools.

**Data flow**: It receives manifests plus optional credential, index, and embedding services. It starts with built-in tools, then for each extension creates an extension context when needed, adds declared tools, records which extension context owns each tool, registers extension object kinds, adds core credential object kinds, and finally turns object kinds into object tools. It returns the tool definitions and a map from extension tool name to extension context.

**Call relations**: This is called when preparing turn execution. It calls `context_for` to create scoped extension access, `core_object_kinds` for core credential objects, and the object registry machinery so object kinds become usable tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `core_object_kinds`  (lines 436–459)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None) -> tuple[BoundKind, ...]
```

**Purpose**: Creates the core object kind that represents credentials. This lets credentials appear through the same object interface as extension-defined object types.

**Data flow**: It receives manifests and an optional credential store. It turns every declared credential slot into a `DeclaredSlot`, packages those into a credential object store, wraps that store in an `ObjectKind`, and returns it as a core-bound kind with no extension context.

**Call relations**: `turn_tools` uses this so credential objects become available during a turn. `validate_ext_tools` uses it at boot so object-tool registration is checked before the system starts serving work.

*Call graph*: called by 2 (turn_tools, validate_ext_tools); 4 external calls (__init__, __init__, __init__, __init__).


##### `skill_registry`  (lines 462–485)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the registry of loadable skills for the deployment. A skill is reusable instruction or behavior that can be loaded by name.

**Data flow**: It starts with core skills, scans every active manifest's skill paths, discovers skill files from disk, and adds them by name. It then adds any generated skills supplied at boot. If any name is reused, it raises an error instead of letting one skill hide another. It returns a `SkillRegistry`.

**Call relations**: Startup code can call this after manifests are loaded. It hands off to `discover_skills` for reading skill definitions and returns the registry used later by skill loading and skill-index rendering.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 488–492)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects all subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent the system can use.

**Data flow**: It receives manifests and flattens each manifest's subagent profile list into one tuple, preserving manifest order. It does not resolve duplicates itself.

**Call relations**: The serving layer uses this output to build the subagent registry. Duplicate profile names are expected to be rejected when that registry is constructed, so this function stays as a simple collector.


##### `durable_surfaces`  (lines 495–501)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds conversation surfaces whose replies should be delivered through durable writeback. A durable surface is one that declares a post handler, meaning the system must remember to send results back later.

**Data flow**: It receives manifests, scans their surface declarations, keeps surfaces that have a `post` handler, and returns their names as a frozen set.

**Call relations**: Admission or serving code uses this set when a turn enters a conversation. If the conversation is on one of these surfaces, it can register writeback work for the poller.


##### `turn_subagent_grants`  (lines 504–514)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Builds a map of extra tools that extensions grant to subagent profiles they may not own. This lets one extension add capability to a known profile without editing that profile.

**Data flow**: It receives manifests, walks every subagent tool grant, and unions tool names by target profile. It returns a dictionary from profile name to a frozen set of granted tool names.

**Call relations**: The turn loop can fold this map onto each profile's own tools, then intersect with the live tool registry. Unknown profiles or unavailable tools naturally disappear at that later intersection.


##### `turn_runtime_skills`  (lines 517–539)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Asks extensions for runtime skills that are generated or selected for a specific workspace. These are skills that cannot be fully known from static files alone.

**Data flow**: It receives manifests, a credential store, and optional index and embedding services. For each manifest with a runtime skill provider, it creates an extension context and awaits the provider. It gathers all returned runtime skills into a tuple. If a provider exists but no credential store is available, it raises an error.

**Call relations**: Per-turn setup uses this to add workspace-aware skills beside core and pack skills. It calls `context_for` so each provider runs with the same scoped access model as that extension's tools.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 545–565)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Chooses and constructs the index backend for the workspace. An index backend stores and searches indexed text or vectors, depending on the extension implementation.

**Data flow**: It receives active manifests, an optional configured backend name, and an optional credential store. It looks for an extension index spec matching the configured name or `default`, checks credential requirements, builds an extension context, and calls the spec factory. It returns the backend, or raises `NotRegisteredError` if no extension registered the selected name.

**Call relations**: Startup or workspace setup calls this after manifests are loaded. It uses `context_for` to give the selected backend its extension scope and clearly separates 'not registered' from other boot failures.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 568–588)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Chooses and constructs the embedding client for the deployment. An embedding client turns text into numeric vectors that search and memory systems can compare.

**Data flow**: It receives manifests, an optional configured name, and an optional credential store. It searches extension embedding specs for the configured name or `default`, checks whether credentials are required, creates an extension context, and calls the factory. It returns the embedding client, or raises `NotRegisteredError` if no matching provider exists.

**Call relations**: Boot code resolves this once and passes the resulting client to indexing, memory, and extension contexts. Like `index_backend`, it uses `context_for` for scoped extension access.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 591–616)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider if an active extension registers it. Memory search is the component that retrieves relevant stored memory for a turn.

**Data flow**: It receives manifests, credential and search dependencies, and a provider name. It finds matching memory search specs. If none exist, it returns `None`; if more than one extension claims the same name, it raises an error. For the single match, it checks credentials, builds context, constructs the provider, and wraps it as `MemorySearch`.

**Call relations**: Workspace or turn setup can call this when memory search is enabled. It uses `context_for` so the provider sees the correct extension scope and optional index/embed services.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 619–655)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Checks extension tool and object registrations at boot before any turn runs. It is a safety check that catches name collisions and missing credential setup early.

**Data flow**: It receives manifests and an optional credential store. It collects built-in tools and extension tools, rejects extensions that declare credential slots without a credential store, builds an object registry including core and extension kinds, turns object verbs into tools, and finally constructs a `ToolRegistry` to force duplicate-name validation. It returns nothing if validation succeeds.

**Call relations**: Boot code uses this as a preflight check. It calls `core_object_kinds`, object registry creation, object verb tool generation, and `ToolRegistry` construction so the same registration gates used at runtime are tested up front.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.fire`  (lines 691–766)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, audience_member_id: UUID | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one turn event and combines their results. Hooks are extension callbacks that can deny an action, edit tool input or output, or inject extra context.

**Data flow**: It receives an event, the event payload, optional turn and agent records, and optional speaker/audience member IDs. It walks the hooks for that event in order, skips tool-specific hooks when the tool name does not match, builds a hook context, runs each hook with a timeout, and checks whether the returned outcome is allowed for that event. A deny stops the chain. Modifications are folded forward so later hooks see earlier changes. Injected text is joined together. It returns a `HookResolution` describing the final denial, modified values, and injected text.

**Call relations**: The turn engine calls this at hook points such as before tool use or after tool use. It creates `HookContext` objects for handlers, uses `asyncio.timeout` to prevent hanging hooks, raises `HookOutcomeNotAllowed` internally for invalid outcomes, and logs swallowed failures for non-gating events. Gating events fail closed by returning a denial if a hook crashes or times out.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 769–795)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> HookChain
```

**Purpose**: Builds the hook chain for a turn from active extension manifests. It binds each hook to the extension context it should run under.

**Data flow**: It receives manifests, a credential store, and optional index and embedding services. For each manifest with hooks, it requires a credential store, creates an extension context, ignores `page_change` hooks because those are driven elsewhere, groups the remaining hooks by event, and returns a `HookChain`.

**Call relations**: Per-turn setup calls this after manifests are loaded. It creates `BoundHook` records and a `HookChain`; later, the turn engine calls `HookChain.fire` whenever one of the grouped events occurs.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### Interactive research packages
These package markers make the REPL and research extensions importable for discovery and later activation.

### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `package import`

In Python, a folder can be treated as an importable package when it has an `__init__.py` file. This file is that marker for the REPL extension package. A REPL, short for “read-eval-print loop,” is an interactive command area where a user can type something, have it run, and see the result.

This particular file is empty, so it does not set up objects, run startup code, or expose helper functions. Its value is structural: it tells Python and project tools that `extensions/repl/ufo_ext_repl` is a package namespace. Without it, some import paths or packaging tools could fail to recognize this directory in environments that still expect explicit package markers.

An everyday analogy is a labeled folder in a filing cabinet. The folder may be empty at the front, but the label still matters because it tells people and tools where the related documents belong.


### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other code can refer to modules inside `extensions/research/ufo_ext_research` using normal Python import paths. Think of it like putting a label on a folder so the Python runtime knows, “this folder is part of the program’s module system.” Because the file has no code, importing this package does not create objects, run setup steps, or change program behavior on its own. Its value is structural: without it, some Python versions or tooling might not recognize the directory as a package, which could make imports less reliable.


### Automation and skill packages
These extensions expose scheduled work, offline self-improvement, site handling, and skill-authoring areas to the loader.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as a package, meaning a named group of related modules. Here, it belongs to the `ufo_ext_scheduled_tasks` extension, so its main job is to make that extension importable by the rest of the system.

There is no code inside this file, so it does not start tasks, load settings, or change data. Its value is structural: without it, some Python tooling or older import behavior might not recognize this directory as a package. You can think of it like a label on a drawer. The label does not contain the tools, but it tells the system that the drawer exists and can be opened by name.

If future package-wide setup is needed for the scheduled-tasks extension, this file is where that setup could be placed. Right now, it intentionally stays empty.


### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `package import`

This file does not contain running code. Its main job is to label this folder as the home of the self-improvement extension and give readers a short summary of what that extension is for.

The extension’s purpose is to support a careful improvement loop. Instead of changing prompts automatically while the system is running, it looks back at saved trajectories, meaning records of what happened during previous runs. It uses those records like a coach reviewing game footage. From that review, it can suggest changes to prompts, but those changes are governed: they are opened for a member to approve rather than silently applied.

This matters because prompt changes can strongly affect how an AI system behaves. Without a governed review step, the system could drift in unexpected ways. This package-level file sets the expectation that self-improvement here is offline, replay-based, and approval-driven.


### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, an `__init__.py` file tells the import system that a directory should be treated as a package, meaning its files can be reached with package-style imports such as `ufo_ext_sites.some_module`. Think of it like a label on a folder: the label does not do any work itself, but it tells the rest of the system that the folder belongs to the project’s organized set of importable code. Without this file, some tools or Python setups might not recognize this directory as a normal package, which could make imports or extension discovery less reliable. Because the file is empty, it does not set up state, define helpers, register plugins, or run startup logic.


### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `startup and per-turn skill discovery`

This file does not contain executable code. Its job is to identify this folder as a Python package and give a short summary of the extension’s purpose. In plain terms, this extension supports a workflow for writing new skills, defines a `skill` as a kind of object the system can understand, and helps make saved workspace skills available during each turn of execution. A “turn” here means one cycle where the system responds to a user or task, and a “registry” is like a catalog of available tools or abilities. The important idea is that skills saved in a workspace are not useful unless the runtime can find them when a turn begins. This package is where that feature area is grouped. Without this package marker, Python might not treat the directory as importable code, and other parts of the project could have trouble referring to this extension in a normal way.


### Communication and source packages
These importable extension packages provide integration points for Slack and external source capabilities.

### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that package is `ufo_ext_slack`, which likely contains the code for a Slack-related extension elsewhere in the same directory tree.

Because the file is empty, it does not run setup code, expose shortcut imports, or define any functions or classes. Its value is structural: it lets other code refer to this extension by package name, much like putting a label on a folder so the filing system knows it belongs together.

Without this file, some Python environments or tooling might not recognize `ufo_ext_slack` as a normal package, which could make imports less reliable. Nothing happens here at runtime beyond Python noticing that the package exists.


### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `extensions/sources/ufo_ext_sources` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label mainly tells Python, “this drawer belongs to the project and can be opened by name.” Because the file is empty, it does not run setup code, expose shortcut names, or change how the source extension works. Its value is structural: without it, some Python environments or packaging tools might not recognize this directory as a package, which could make imports fail.


### Built-in and web packages
These package markers make the built-in UFO, web, and YC extension areas available for manifest discovery.

### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder usually needs an `__init__.py` file to be treated as an importable package, meaning other parts of the project can refer to code inside it using dotted names like `ufo_ext_ufo.some_module`. Think of it like a label on a drawer: the drawer may contain useful tools elsewhere, but this label simply tells Python that the drawer is part of the organized system. Because the file has no code, it does not run setup steps, expose shortcuts, or change program behavior directly. What would break without it depends on the Python version and packaging style, but in many projects removing it can make imports fail or make packaging tools overlook this directory.


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other code can refer to this folder by name, such as importing modules from `ufo_ext_web`.

There is no runtime logic here: no functions, classes, settings, or side effects. Its value is structural. It tells Python and project tooling, “this directory belongs together as a package.” Without it, depending on the Python version and how the project is loaded, imports from this web extension package might fail or behave differently.

A simple analogy is a label on a filing cabinet drawer. The label does not contain the documents, but it tells everyone that the drawer is a recognized place where related files live.


### `extensions/yc/ufo_ext_yc/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a directory can include an `__init__.py` file to tell Python, and tools built around Python, that the directory should be treated as an importable package. Think of it like a label on a folder: the label does not do the work inside the folder, but it lets other parts of the system find and refer to that folder reliably.

Because this file has no code, it does not create objects, read settings, call services, or change program behavior directly. Its value is structural. Without it, some import systems, packaging tools, or older Python-compatible workflows might not recognize `extensions/yc/ufo_ext_yc` as a normal package. That could make it harder or impossible for other code to import modules from this extension area.

In short, this file exists so the surrounding `ufo_ext_yc` extension package has a clear Python package boundary, even though the actual behavior lives in other files.

## 📊 State Registers Touched

- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-model-catalog` — The shared list of available AI models, providers, limits, prices, and client adapters.
- `reg-tool-catalog` — The shared catalog of tools the model may call, including their names, schemas, handlers, and safety properties.
- `reg-skill-inventory` — The built-in and user-created skill folders, metadata, dependencies, and workspace-specific skill records.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-search-index` — The searchable text chunks, embeddings, and selected index backend used to find relevant stored content.
- `reg-scheduled-jobs` — The background job and scheduled-task state that records what should run later, what is claimed, and what repeats.
- `reg-extension-store` — The per-workspace extension-owned storage where plugins keep their own durable records without private tables.
- `reg-sample-extension-note` — Workspace-scoped note stored by the sample extension to prove extension migrations and SDK storage contracts work end to end.
- `reg-extension-catalog-cache` — The searchable catalog metadata for available extensions or packs, distinct from the installed extension lockfile and loaded capability set.
