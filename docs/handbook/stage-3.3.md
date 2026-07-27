# Core extension discovery and pinning  `stage-3.3`

This stage is shared behind-the-scenes support for UFO’s extension system. Extensions are add-on packages that teach UFO new commands, object types, tools, hooks, skills, credential rules, or storage backends. Before the CLI or server can use those additions, UFO must know which extensions are installed, which ones are allowed, and what they provide.

The loader is the doorway. It searches the installed Python packages for UFO extension declarations, reads what each one offers, and activates those declarations so the rest of the system can use them. In practice, it turns “this extension provides a tool” into an actual tool UFO can call.

The store manages the extension catalog and lockfile for the `ufoctl ext` command. The catalog is like a shelf of available add-ons. The lockfile is the saved shopping list of the exact extensions UFO should load later, including a digest, or fingerprint, to prove which installed package was pinned. Together, the store chooses and records extensions, while the loader brings the recorded extensions to life.

## Files in this stage

### Extension catalog and activation
Manages the pinned extension catalog and then loads pinned extensions into UFO runtime declarations.

### `core/src/ufo/ext/store.py`

`domain_logic` · `extension search/install/remove command handling`

This file is the small “app store counter” for UFO extensions. The catalog says what extensions are available. The lockfile says what extensions this deployment has chosen to use. Without this file, a user could not reliably turn catalog entries into locked, repeatable extension choices.

The file defines simple shapes for catalog data: each catalog entry has a name, version, and a `disabled` flag. Disabled entries are special: they are allowed for bundling, but normal install refuses them. That prevents users from accidentally enabling extensions that are meant to be packaged another way.

The main worker is `ExtensionStore`. It is given one catalog and one lockfile path. Searching compares catalog entries with what is already pinned, so results can say whether an extension is already installed in this deployment. Installing first checks that the name exists in the catalog and is not disabled. It then asks the extension loader what Python packages are actually present in the current environment, builds a pin with the extension version and digest, and writes a new lockfile. Removing does the reverse: it checks that the extension is pinned, then rewrites the lockfile without it.

A key safety detail is that catalog presence is not enough. The extension must also be installed in the current Python environment before it can be pinned.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked `Catalog` object. This gives the rest of the code a trusted, predictable view of what extensions the store offers.

**Data flow**: It receives a file path. It reads the file text, parses it as TOML, which is a human-readable configuration format, and validates the result against the catalog shape. It returns a `Catalog` containing the available extension entries.

**Call relations**: This is the doorway from the catalog file into the store logic. Later, an `ExtensionStore` can use the returned catalog to search, install, or reject extension names.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Finds the installed version of the main `ufo` package. The lockfile uses this as an anchor so the pinned extensions are tied to the UFO version that created the file.

**Data flow**: It takes no project data as input. It asks Python package metadata for the installed `ufo` version and returns that version string.

**Call relations**: When `ExtensionStore._write` creates a brand-new lockfile, it calls this function to record the current UFO version. If an existing lockfile is being rewritten, `_write` keeps the old anchor instead.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an extension that is already installed in the current Python environment. A pin records the extension name, its manifest version, and a digest, which is a fingerprint of the installed source.

**Data flow**: It receives an extension name. It asks the loader for discovered installed extensions, looks up that name, and fails with a clear error if the package is not present. If found, it reads the extension's manifest version, computes the source digest, and returns an `ExtensionPin` for the lockfile.

**Call relations**: `ExtensionStore.install` calls this after it has confirmed the catalog allows the extension. This function is the safety check that prevents the store from pinning something that exists only in the catalog but is not actually installed locally.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and marks which matching extensions are already pinned in the lockfile. This is what lets a user see both what is available and what is already selected.

**Data flow**: It receives a query string. It reads the current pins from the lockfile, scans catalog entries whose names contain the query text, and creates one `StoreListing` for each match. Each listing includes the catalog name, version, disabled status, and whether it is already installed according to the lockfile.

**Call relations**: This is called when the user wants to browse or search extensions. It relies on `_pins` to know the current lockfile state, then returns simple listing objects for display by higher-level command code.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Adds or updates one extension pin in the lockfile. It enforces the store rules: the extension must be in the catalog, must not be disabled, and must be installed in the current environment.

**Data flow**: It receives an extension name. It looks for that name in the catalog, rejects unknown names, rejects disabled bundle-only entries, then calls `pin_for` to create the exact pin. It reads the existing pins, replaces any old pin with the same name, writes the updated lockfile, and returns the new pin.

**Call relations**: This is the main install path. It uses `pin_for` to translate an allowed catalog entry into a real installed-extension pin, uses `_pins` to preserve other pinned extensions, and hands the final list to `_write` so the lockfile is updated.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes one extension from the lockfile. It refuses to remove a name that is not currently pinned, so users get a clear error instead of a silent no-op.

**Data flow**: It receives an extension name. It reads the existing pins, checks whether any pin has that name, and raises an error if none do. Otherwise it filters that pin out and writes the remaining pins back to the lockfile.

**Call relations**: This is the uninstall-like path for extension pins. It uses `_pins` to inspect the current lockfile and `_write` to save the lockfile after the selected extension has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current extension pins from the lockfile, if the lockfile exists. If there is no lockfile yet, it treats the deployment as having no pinned extensions.

**Data flow**: It uses the store's lockfile path. If the file exists, it reads and returns the lockfile's extension pins. If it does not exist, it returns an empty tuple.

**Call relations**: This is the shared “what is currently selected?” helper. Search, install, and remove all call it before deciding what to show or how to rewrite the lockfile.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete lockfile with a given set of extension pins. It also preserves the existing UFO version anchor when rewriting an old lockfile, or records the current UFO version when creating a new one.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. It checks whether the lockfile already exists. If it does, it reads the existing UFO version from it; if not, it asks `ufo_version` for the installed package version. It then builds a new `Lockfile` object and writes it to disk.

**Call relations**: Install and remove both finish by calling this function. They decide what the pin list should be; `_write` is the final step that turns that decision into the saved lockfile the extension loader will use later.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and per-turn setup`

Extensions are extra packages that add abilities to UFO. This file is the trusted loader for them. It discovers installed extensions through Python “entry points” (named plugin hooks published by installed packages), checks that their names do not collide, and decides which ones are active. If a lockfile exists, it acts like a sealed shopping list: only the pinned extensions may load, and their installed source code must match the recorded digest. If the code changed, startup fails instead of quietly running something different.

After the active manifests are known, this file derives all the pieces UFO needs from them. It builds the tool list for a turn, binds each extension tool to an ExtensionContext, gathers database migration folders, validates credential injection rules, collects skills and subagent profiles, chooses index/embed/memory backends, and builds the hook chain that can react before or after turn events.

The important idea is that extensions declare what they provide; they do not register themselves by calling into global state. This keeps boot predictable. Like an event organizer reading all vendor applications before opening the doors, the loader checks for duplicate names, missing credentials, unsafe environment-variable conflicts, and unsupported hooks early, so broken extensions fail at startup rather than halfway through a user turn.

#### Function details

##### `lockfile_path`  (lines 136–137)

```
def lockfile_path() -> Path
```

**Purpose**: Finds the lockfile path UFO should use. This lets deployments override the default lockfile location with an environment variable.

**Data flow**: It reads the UFO_LOCKFILE environment variable. If that variable is set, it turns that value into a Path; otherwise it uses the default path ufo.lock. The result is a filesystem path object.

**Call relations**: load_manifests calls this first when deciding whether the system is in pinned mode or development mode.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 140–141)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads and validates the lockfile from disk. The lockfile says exactly which extensions are allowed to run and what their source-code digest must be.

**Data flow**: It receives a path, reads the file text, and asks the Lockfile model to parse and validate the JSON. It returns a Lockfile object with structured extension pins.

**Call relations**: load_manifests calls this when the lockfile path exists, then uses the returned pins to check installed extensions.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 144–145)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a validated lockfile object to disk as readable JSON. This is the counterpart to read_lockfile for tools that pin an extension set.

**Data flow**: It receives a path and a Lockfile object. It converts the lockfile to indented JSON, adds a final newline, and writes that text to disk. It does not return a value.

**Call relations**: No in-file caller is shown; command-line or bundling code can use it to create the file that load_manifests later reads.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 148–158)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension available in the current Python environment. It also rejects duplicate extension names so later code can trust names are unique.

**Data flow**: It asks Python for entry points in the ufo.extension group. Each entry point is loaded and called to get a Manifest. The function returns a dictionary from manifest name to the manifest plus the entry point it came from.

**Call relations**: load_manifests uses this to know what can be activated. migration_locations also uses it so it can find source packages for active extensions.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 161–172)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds every installed UFO pack. A pack is a named bundle of extensions plus its own skills or onboarding steps.

**Data flow**: It asks Python for entry points in the ufo.pack group. Each entry point is loaded and called to get a Pack. The function returns a dictionary from pack name to Pack, and raises an error if two packs claim the same name.

**Call relations**: _pack_manifests calls this when load_manifests needs to narrow the active extensions to a selected pack.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 175–180)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds the import information for the top-level Python package behind an extension entry point. This is needed to locate the package files on disk.

**Data flow**: It receives an EntryPoint, takes the first part of its module name, and asks Python import machinery where that package lives. It returns a ModuleSpec, or raises an error if the package has no importable source.

**Call relations**: extension_digest uses it to hash an extension’s source files. migration_locations uses it to find an extension’s package directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 183–188)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the directory that belongs to an extension package. UFO uses this to look beside the package for a migrations folder.

**Data flow**: It receives a ModuleSpec. If the extension is a package directory, it returns that directory. If it is a single Python file, it returns the file’s parent directory.

**Call relations**: migration_locations calls this after _entry_spec so it can check whether the extension has database migrations.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 191–211)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the source-code fingerprint used to verify a pinned extension. If any meaningful source file changes, the digest changes too.

**Data flow**: It receives an extension entry point, finds the installed package source, gathers all files except bytecode cache files, and hashes both each relative filename and its contents in a stable order. It returns a string starting with sha256:.

**Call relations**: load_manifests calls this for each pinned extension in a lockfile and compares the result with the recorded digest.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 214–231)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Lists database migration directories supplied by active extensions. These folders let extensions add their own database changes alongside UFO’s core migrations.

**Data flow**: It discovers installed extensions, loads the active manifests, finds each active extension’s package directory, and checks for a migrations subdirectory. It returns the matching directory paths as strings.

**Call relations**: It depends on discovered, load_manifests, _entry_spec, and _package_dir. Migration-running code can use the returned paths to include extension migrations.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 234–259)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Determines the active extension manifests for this run. This is the central gate that enforces the lockfile when one exists.

**Data flow**: It discovers installed extensions and checks the lockfile path. Without a lockfile, it activates every discovered extension. With a lockfile, it loads each pin, requires the extension to be installed, verifies its digest, and returns only the pinned manifests. If a pack is requested, it narrows the result through _pack_manifests.

**Call relations**: migration_locations calls this to know which extensions count as active. Most higher-level startup code is expected to begin from this result before deriving tools, hooks, skills, and backends.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 262–294)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Builds the manifest list for one selected pack. It ensures the pack’s bundled extensions are installed and active, then appends the pack’s own manifest-like contributions.

**Data flow**: It receives a pack name and the already-active extension map. It discovers installed packs, finds the named one, collects the manifests for its declared extensions, checks for missing or colliding names, and creates a Manifest for the pack’s own skills and onboarding steps. It returns those manifests in order.

**Call relations**: load_manifests calls this only when configuration selects a pack. It uses discovered_packs to find the pack declaration.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 297–306)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These declarations tell UFO which environment variables can expose connector grant sentinels.

**Data flow**: It receives active manifests, walks through their connectors, and keeps only connectors that declare a CLI credential. It returns a dictionary keyed by OAuth provider name.

**Call relations**: injecting_slots calls this so credential-slot environment variables can be checked against connector CLI environment variables in one shared namespace.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 309–382)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into outbound traffic or sandboxes, while catching unsafe naming conflicts. This prevents two secrets or host choices from silently fighting over the same variable or sentinel.

**Data flow**: It receives active manifests, extracts credential slots with injection settings, gathers all declared slot names, and compares sentinels, environment-variable exports, host choices, and metering dimensions. If something would be ambiguous or silently overwritten, it raises an error. Otherwise it returns the valid slots.

**Call relations**: It calls connector_clis to include connector credential exports in the same conflict check. Proxy and sandbox-building code can then use the returned slots knowing these collisions were already refused.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 385–433)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[tuple[ToolDef, ...], dict[str, Exten
```

**Purpose**: Builds the complete tool set available during a turn. It combines built-in tools, extension tools, connector tools, and object-operation tools.

**Data flow**: It receives active manifests plus optional credential, index, and embedding services. It starts with built-in tools, creates an ExtensionContext for each extension that contributes tools or object kinds, attaches tools to that context, binds object kinds, adds core credential objects, and returns both the full tool list and a map from extension tool name to its context.

**Call relations**: It calls context_for to make per-extension contexts, core_object_kinds for core credential objects, object_registry to combine object kinds, and ObjectVerbs to expose object operations as tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `core_object_kinds`  (lines 436–459)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None) -> tuple[BoundKind, ...]
```

**Purpose**: Creates the core object kind for credentials, based on all active extensions’ declared credential slots. This lets credentials appear through the same object interface as other kinds.

**Data flow**: It receives manifests and an optional credential store. It turns every declared credential slot into a DeclaredSlot, builds a CredentialObjects store around them, wraps that in an ObjectKind, and returns it as a BoundKind with no extension context.

**Call relations**: turn_tools calls this when building turn-time object tools. validate_ext_tools calls it during startup validation so object tool names and registrations are checked early.

*Call graph*: called by 2 (turn_tools, validate_ext_tools); 4 external calls (__init__, __init__, __init__, __init__).


##### `skill_registry`  (lines 462–485)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the deploy-wide registry of loadable skills. Skills are reusable instruction or behavior bundles that can come from core, packs, extensions, or generated startup data.

**Data flow**: It starts with core skills, then reads each skill spec path from active manifests and discovers skills on disk. It rejects duplicate skill names. It then adds generated skills, also rejecting duplicates, and returns a SkillRegistry.

**Call relations**: It calls discover_skills to read extension skill files and SkillRegistry to package the final name-to-skill map for later skill loading and rendering.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 488–492)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent the system can make available.

**Data flow**: It receives active manifests, walks through their subagent profile lists, and returns all profiles in manifest order.

**Call relations**: No in-file caller is shown; serving or turn setup code can pass the returned profiles into the subagent registry.


##### `durable_surfaces`  (lines 495–501)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds surface names whose replies are durable, meaning they are delivered through the writeback poller rather than only immediately. A surface is treated this way when it declares a post handler.

**Data flow**: It receives manifests, inspects their surface specs, keeps surfaces with a post handler, and returns their names as a frozen set.

**Call relations**: No in-file caller is shown; admission or writeback code can use this set to decide when to register poller-backed writeback rows.


##### `turn_subagent_grants`  (lines 504–514)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Builds the map of extra tools that extensions grant to subagent profiles they may not own. This lets one extension widen a profile’s abilities without editing the profile itself.

**Data flow**: It receives manifests, walks through subagent tool grant declarations, unions tool names by target profile, and returns a dictionary whose values are frozen sets.

**Call relations**: No in-file caller is shown; the turn loop can merge this map into subagent profiles before intersecting with the actually available tool set.


##### `turn_runtime_skills`  (lines 517–539)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Asks extensions for runtime skills that depend on the current workspace. These are skills produced dynamically rather than read once from disk.

**Data flow**: It receives manifests plus optional credential, index, and embedding services. For each manifest with a runtime skill provider, it requires a credential store, builds that extension’s context, awaits the provider, and appends the returned RuntimeSkill objects. It returns them as a tuple.

**Call relations**: It calls context_for so each provider runs with the same kind of scoped extension context that tools and jobs receive.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 545–565)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Chooses and builds the index backend for the workspace. The index backend is the service that stores and searches indexed text or vectors.

**Data flow**: It receives manifests, an optional configured backend name, and an optional credential store. It uses the configured name or default, searches extension index specs for that name, checks credential requirements, builds an ExtensionContext, and calls the selected factory. It returns an IndexBackend or raises NotRegisteredError if none exists.

**Call relations**: It calls context_for before handing control to the extension’s backend factory. Startup code uses this to turn manifest declarations into the actual indexing service.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 568–588)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Chooses and builds the embedding client for the deploy. An embedding client turns text into numeric vectors used for search and memory.

**Data flow**: It receives manifests, an optional configured name, and an optional credential store. It chooses the configured name or default, searches extension embed specs, checks whether credentials are required, builds the declaring extension’s context, and returns the factory result. If no extension registers the name, it raises NotRegisteredError.

**Call relations**: It calls context_for before invoking the selected extension factory. The resulting EmbedClient is later threaded into index, memory, and extension contexts.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 591–616)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider if an active extension declares it. Memory search is the feature that retrieves relevant stored context for a turn.

**Data flow**: It receives manifests, optional credential/index/embed services, and a provider name. It finds matching memory search specs, returns None if there are none, rejects duplicates, checks credential requirements, builds an ExtensionContext, and wraps the provider in a MemorySearch object.

**Call relations**: It calls context_for to bind the provider to its declaring extension and MemorySearch to package the built provider for callers.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 619–655)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Checks extension tool and object registrations at boot before any user turn runs. This makes name collisions and missing credential setup fail early.

**Data flow**: It receives manifests and an optional credential store. It gathers built-in tools plus extension and connector tools, checks that credential-declaring tool extensions have a credential key, builds an object registry including core and extension object kinds, adds object-operation tools, and constructs a ToolRegistry. If names or registrations are invalid, construction raises.

**Call relations**: It calls core_object_kinds, object_registry, ObjectVerbs, and ToolRegistry. Unlike turn_tools, it validates deploy-level shape without building per-workspace extension contexts.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.fire`  (lines 691–766)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, audience_member_id: UUID | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one turn event and combines their results. Hooks are extension callbacks that can observe, block, modify, or add context at specific points in a turn.

**Data flow**: It receives an event, event payload, optional turn and agent records, and optional speaker/audience IDs. It selects hooks for the event, filters tool-specific hooks, gives each hook the latest payload in a HookContext, enforces a timeout, checks that the hook returned an allowed outcome, and folds outcomes into one HookResolution. A Deny stops the chain; modifications flow into later hooks; injected text is concatenated.

**Call relations**: turn_hooks builds the HookChain that owns this method. During firing, it creates HookContext and HookResolution objects, uses dataclasses.replace to pass updated payloads forward, uses asyncio.timeout to limit hook runtime, and logs swallowed failures for non-gating events.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 769–795)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> HookChain
```

**Purpose**: Builds the hook chain used during a turn. It binds each declared turn-lifecycle hook to the ExtensionContext of the extension that declared it.

**Data flow**: It receives manifests plus optional credential, index, and embedding services. It creates empty groups for supported turn events, requires a credential store for hook-declaring extensions, builds each extension context, skips page_change hooks, and stores the rest as BoundHook objects grouped by event. It returns a HookChain.

**Call relations**: It calls context_for to create scoped extension contexts, BoundHook to bind hook specs to those contexts, and HookChain to package the grouped hooks. HookChain.fire later executes the grouped hooks during the turn.

*Call graph*: 3 external calls (__init__, __init__, context_for).
