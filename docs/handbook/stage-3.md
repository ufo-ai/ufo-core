# Pack selection and extension discovery  `stage-3`

This stage is part of startup. It decides which bundles of features, called packs, are active for a workspace, then discovers the extensions inside them. A pack is like a preset toolbox: the local assistant pack, hosted assistant pack, chief-of-staff pack, and YC founder pack each name a different collection of services, tools, and skills to load.

The extension store acts like a small app store. It reads the available extension catalog, shows what is pinned, and updates the lockfile, which is the saved record of installed choices. The loader is the gatekeeper. It finds installed extensions, checks that they are allowed, then turns their written declarations into usable system parts such as tools, hooks, skills, object types, and backend providers.

Capability registration is the sign-in desk for those declarations. Extensions announce what they offer before anything runs. Together, the packs choose the desired toolbox, the store records the choice, the loader validates it, and registration builds the capability graph the rest of the workspace can use.

## Sub-stages

- [Capability registration](stage-3.1.md) `stage-3.1` — 27 files

## Files in this stage

### Extension catalog and loading
Core extension machinery discovers installed extensions, checks what is allowed, and updates pinned extension state.

### `core/src/ufo/ext/store.py`

`domain_logic` · `extension CLI commands and lockfile updates`

This file solves a practical problem: UFO needs a safe, repeatable way to choose which extensions should load. The catalog is the menu of possible extensions. The lockfile is the receipt that says exactly which extensions are pinned, including a digest, which is a fingerprint of the installed extension code. Without this file, a command like `ufoctl ext install` would not have one clear place to check the catalog, refuse unavailable or disabled extensions, and record the exact extension version to boot later.

The main idea is simple. `CatalogEntry` describes one catalog item: its name, version, and whether it is disabled. Disabled here means “bundle-only”: it may be pinned by a bundle process, but normal install refuses it. `Catalog` is the whole list. `StoreListing` is what search returns, adding whether the extension is already installed according to the lockfile.

`ExtensionStore` ties one catalog to one lockfile path. Searching filters the catalog and marks entries that are already pinned. Installing first checks the catalog, rejects disabled entries, confirms the Python package is actually installed in the current environment, builds a pin with a digest, and rewrites the lockfile. Removing does the opposite: it checks that the extension is pinned and rewrites the lockfile without it. Think of it like updating a playlist: the catalog is all songs you may choose from, while the lockfile is the exact playlist the player will use.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked `Catalog` object. This gives the rest of the extension store a clean, predictable list of available extensions instead of raw text.

**Data flow**: It takes a file path. It reads the file text, parses that text as TOML, which is a common human-readable configuration format, and validates the result as a `Catalog`. The output is a structured catalog object ready for searching or installing from.

**Call relations**: This is the doorway from a catalog file into the store’s in-memory view of available extensions. It uses the path object to read text and the TOML parser to understand that text; code that wants to create an `ExtensionStore` would use this first so the store has a catalog to work with.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Looks up the installed version of the `ufo` Python package. The store uses this when it needs to create a new lockfile and must record which UFO version the lockfile belongs to.

**Data flow**: It takes no direct input. It asks Python package metadata for the installed `ufo` version and returns that version string.

**Call relations**: This helper is called by `ExtensionStore._write` only when there is no existing lockfile to copy a version from. In that case, `_write` needs a starting UFO version before it can save the new lockfile.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an installed extension. A pin records the extension name, the version from its manifest, and a digest, which is a fingerprint used to notice if the installed code changes.

**Data flow**: It receives an extension name. It asks the loader’s discovery system what extensions are installed in the current Python environment. If the name is missing, it raises an error because the catalog cannot pin something that is not actually installed. If found, it reads the extension’s manifest version, computes a digest for its entry point, and returns an `ExtensionPin`.

**Call relations**: `ExtensionStore.install` calls this after the catalog checks pass. This function hands back the precise pin that `install` will write into the lockfile, so the loader later knows exactly what extension to load.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and reports which matching extensions are already pinned in the lockfile. This is what lets a user see both what is available and what is currently installed.

**Data flow**: It receives a query string. It reads the current pins from the lockfile through `_pins`, makes a set of pinned extension names, then walks through the catalog entries whose names contain the query text. For each match, it returns a `StoreListing` with the catalog details plus an `installed` true-or-false value.

**Call relations**: This is called when the extension store needs to show search results. It depends on `_pins` for the current installed state, then creates listing objects that combine catalog information with lockfile information.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins an extension into the lockfile so UFO will load it later. It protects the user from installing names that are not in the catalog, disabled bundle-only entries, or extensions missing from the current Python environment.

**Data flow**: It receives an extension name. It looks for that name in the catalog. If the name is absent, it raises an error. If the catalog entry is disabled, it raises an error explaining that normal install is not allowed. Otherwise it calls `pin_for` to build the exact pin, removes any older pin with the same name from the current pin list, writes the updated list through `_write`, and returns the new pin.

**Call relations**: This is the main install path for the store. It calls `_pins` to preserve existing lockfile entries, calls `pin_for` to turn the installed package into a trustworthy lockfile pin, and then calls `_write` to save the new lockfile.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. This makes UFO stop treating that extension as installed for future loads.

**Data flow**: It receives an extension name. It reads the current pins from `_pins`. If no pin has that name, it raises an error because there is nothing to remove. Otherwise it filters that pin out and writes the remaining pins back through `_write`. It returns nothing, but the lockfile is changed.

**Call relations**: This is the uninstall path for the store. It relies on `_pins` to know what is currently pinned and hands the shortened list to `_write` so the lockfile reflects the removal.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current list of pinned extensions from the lockfile, if the lockfile exists. If there is no lockfile yet, it treats the store as having no installed extensions.

**Data flow**: It uses the store’s lockfile path. If the file exists, it reads and parses the lockfile and returns its `extensions` tuple. If the file does not exist, it returns an empty tuple.

**Call relations**: `search`, `install`, and `remove` all call this before deciding what to show or change. It is the shared “what is currently pinned?” helper for the store.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete lockfile with a supplied set of extension pins. It keeps the existing UFO version anchor when possible, so editing extensions does not accidentally change that part of the lockfile.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. If a lockfile already exists, it reads that file and reuses its recorded UFO version. If no lockfile exists, it asks `ufo_version` for the installed UFO version. It then builds a `Lockfile` object from that version and the pins, and writes it to disk.

**Call relations**: `install` and `remove` call this after they have decided the new pin list. `_write` is the final step that turns those decisions into a saved lockfile, using `read_lockfile` when preserving an existing version and `write_lockfile` to store the result.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and per-turn setup`

Extensions in UFO do not register themselves by calling into the app. Instead, they publish an entry point, which is like a small public sign saying “call me to get my manifest.” A manifest is the extension’s menu: tools, hooks, skills, credential needs, object types, search backends, and other contributions. This file reads those menus and builds the active system from them.

The important safety feature is the lockfile. If a lockfile exists, only the pinned extensions are allowed, and each one’s source code is checked with a SHA-256 digest, a fingerprint of its files. If the code has changed or the extension is missing, startup fails instead of silently running something unexpected. Without a lockfile, UFO runs in development mode and loads every discovered extension.

After discovery, the file turns manifests into usable runtime pieces. It builds the tool list for a turn, creates extension-specific contexts so tools and hooks know which extension they belong to, collects migration folders for database upgrades, resolves configured index and embedding backends, and builds hook chains that react to turn events. In short, this file is the bridge between “extensions are installed on disk” and “the running UFO system knows exactly what abilities are active.”

#### Function details

##### `lockfile_path`  (lines 133–134)

```
def lockfile_path() -> Path
```

**Purpose**: Finds where the extension lockfile should be read from. This lets deployments override the default lockfile location with an environment variable.

**Data flow**: It reads the UFO_LOCKFILE environment variable if it exists; otherwise it uses the default path ufo.lock. It turns that text into a Path object and returns it.

**Call relations**: When load_manifests decides whether UFO is in pinned mode or development mode, it asks this function where to look for the lockfile.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 137–138)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads a lockfile from disk and validates it as a Lockfile object. This protects the loader from using malformed pinned-extension data.

**Data flow**: It receives a file path, reads the file’s text, parses the JSON, and returns a validated Lockfile. If the file does not match the expected shape, validation fails.

**Call relations**: load_manifests calls this after it has found a lockfile, so it can know which extensions and digests are pinned.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 141–142)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a Lockfile object to disk as neatly formatted JSON. It is the matching writer for the reader used at startup.

**Data flow**: It receives a destination path and a Lockfile object. It converts the lockfile to JSON, adds a trailing newline, and writes that text to the file.

**Call relations**: This function is used by tooling that creates or updates the pinned extension set. The runtime side later reads the same format through read_lockfile.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 145–155)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension in the current Python environment. It rejects duplicate extension names so the rest of the system never has to guess which one wins.

**Data flow**: It asks Python’s package metadata for entry points in the ufo.extension group. For each entry point, it loads and calls it to get a Manifest, then stores the manifest together with the entry point under the manifest’s name.

**Call relations**: load_manifests uses this as the raw installed-extension list. migration_locations also uses it so it can connect active manifests back to their installed package locations.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 158–169)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds installed extension packs. A pack is a named bundle that can activate a coherent group of extensions plus pack-level skills and onboarding steps.

**Data flow**: It reads Python entry points in the ufo.pack group, loads each zero-argument callable, calls it to get a Pack, and stores it by pack name. Duplicate pack names cause an error.

**Call relations**: _pack_manifests calls this when configuration selects a pack, so it can check that the pack exists and read what it bundles.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 172–177)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Locates the importable Python package or module behind an extension entry point. This is needed before the loader can inspect the extension’s files.

**Data flow**: It receives an EntryPoint, takes the top-level module name, and asks Python import machinery for its module specification. It returns that specification, or raises an error if there is no real source location.

**Call relations**: extension_digest uses this to know which files to fingerprint. migration_locations uses it to find an extension’s migrations directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 180–185)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the directory that should contain an extension’s migrations folder. It works for both package-style extensions and single-file extensions.

**Data flow**: It receives a module specification. If the extension is a package, it returns the package directory; if it is a single module, it returns the module file’s parent directory.

**Call relations**: migration_locations calls this after _entry_spec so it can check whether that extension has a migrations directory.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 188–208)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the SHA-256 fingerprint used to pin an extension. This makes changed or tampered extension code visible at startup.

**Data flow**: It receives an extension entry point, finds the source package or file, gathers source files while skipping bytecode cache files, and hashes both file names and file contents in stable order. It returns a string starting with sha256: followed by the digest.

**Call relations**: load_manifests calls this for each pinned extension and compares the result with the digest stored in the lockfile.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 211–228)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Returns the database migration folders contributed by active extensions. These folders tell the database upgrader how extension-owned tables should evolve.

**Data flow**: It discovers installed extensions, loads the active manifests, matches each active manifest back to its entry point, finds its package directory, and checks for a migrations subdirectory. It returns the existing migration paths as strings.

**Call relations**: It depends on load_manifests so inactive extensions do not affect the database. It uses discovered, _entry_spec, and _package_dir to move from manifest names back to disk locations.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 231–256)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Builds the active extension manifest list. This is the central decision point for which extensions are allowed to shape the running system.

**Data flow**: It first discovers installed extensions. If no lockfile exists, it returns all discovered manifests. If a lockfile exists, it reads each pinned extension, verifies that it is installed, recomputes its digest, and keeps only the verified manifests. If a pack name is supplied, it narrows the result through _pack_manifests.

**Call relations**: Many later derivations rely on the manifest tuple this function returns. migration_locations calls it, and other startup code can use its result to build tools, hooks, skills, routes, jobs, and backend choices.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 259–291)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Narrows the active extension set to one named pack. It ensures a selected pack is present and that every extension it names is installed and active.

**Data flow**: It receives a pack name and the already-active manifest map. It discovers packs, finds the requested one, collects the manifests for the pack’s listed extensions, checks for name collisions, then appends a synthetic manifest for the pack’s own skills and onboarding steps.

**Call relations**: load_manifests calls this only when configuration asks for a pack. It hands back the exact manifest list that pack-based startup should use.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 294–303)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential helpers declared by connector extensions. These helpers let the engine expose usable credential grants to external connector commands.

**Data flow**: It receives active manifests, walks through their connectors, and keeps connectors that declare a CLI credential helper. It returns a dictionary keyed by provider name.

**Call relations**: This is a simple derivation from manifests. Other parts of the engine can use its output when preparing environment variables or proxy rules for connector access.


##### `turn_tools`  (lines 306–354)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[tuple[ToolDef, ...], dict[str, Exten
```

**Purpose**: Builds the full tool list available during a turn. It combines built-in tools, extension tools, connector tools, and object-operation tools.

**Data flow**: It receives active manifests, an optional credential store, and optional index and embedding clients. It starts with built-in tools, creates an ExtensionContext for each extension that contributes tools or object kinds, adds that extension’s tools, records which context belongs to each extension tool, builds object kinds, then adds generated object verb tools. It returns the final tool tuple and a map from extension tool name to context.

**Call relations**: A turn setup path uses this before dispatching tool calls. It calls core_object_kinds to include credential objects, context_for to make extension-scoped contexts, and object_registry/ObjectVerbs to turn object kinds into usable tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `core_object_kinds`  (lines 357–377)

```
def core_object_kinds(manifests: tuple[Manifest, ...]) -> tuple[BoundKind, ...]
```

**Purpose**: Creates core-owned object kinds that depend on active extensions. In practice, it exposes credential slots declared by extensions as credential objects.

**Data flow**: It receives active manifests and reads every declared credential slot. It converts each slot into a DeclaredSlot with the owning extension name, builds a credential ObjectKind around those slots, and returns it as a core-bound kind with no extension context.

**Call relations**: turn_tools calls this when building the per-turn object tool set. validate_ext_tools also calls it during boot checks so credential object tools are included in collision validation.

*Call graph*: called by 2 (turn_tools, validate_ext_tools); 4 external calls (__init__, __init__, __init__, __init__).


##### `skill_registry`  (lines 380–396)

```
def skill_registry(manifests: tuple[Manifest, ...]) -> SkillRegistry
```

**Purpose**: Builds the deploy-wide registry of loadable skills. Skills are reusable instruction or behavior packages that agents can refer to by name.

**Data flow**: It starts with core skills, then reads each skill specification from active manifests. For every discovered skill on disk, it checks that the name is not already taken and adds it to the registry. It returns a SkillRegistry.

**Call relations**: Startup code can call this after load_manifests to produce the skill index used later by skill loading and prompt rendering.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 399–403)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent the system can make available.

**Data flow**: It receives manifests and flattens every manifest’s subagent profiles into one tuple, preserving manifest order.

**Call relations**: Server setup can use this output to build the SubagentRegistry. Duplicate-name enforcement is left to that registry.


##### `durable_surfaces`  (lines 406–412)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds surfaces whose replies should be delivered through durable writeback. A surface is durable here when it declares a post handler.

**Data flow**: It receives manifests, inspects each declared surface, keeps the surface name when the surface has a post callback, and returns the names as a frozen set.

**Call relations**: Admission or conversation setup code can use this set to decide when to create writeback tracking rows for turns.


##### `turn_subagent_grants`  (lines 415–425)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects tool grants that extensions give to subagent profiles they may not own. This lets capabilities widen a subagent’s tool access without editing the profile itself.

**Data flow**: It receives manifests, walks through each subagent tool grant, unions tool names by target profile, and returns a dictionary from profile name to frozen set of granted tool names.

**Call relations**: The turn loop can combine this map with a profile’s own tool names, then intersect with the live tool set so missing tools or unknown profiles do not break startup.


##### `turn_runtime_skills`  (lines 428–450)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Asks extensions for runtime-generated skills for the current workspace. These are skills that cannot be fully known from static files at startup.

**Data flow**: It receives manifests, a credential store, and optional index and embedding clients. For each manifest with a runtime skill provider, it requires a credential store, builds that extension’s context, awaits the provider, and appends the returned RuntimeSkill objects. It returns all runtime skills as a tuple.

**Call relations**: Per-turn setup can call this to augment the turn’s skill registry. It uses context_for so each provider runs with the same scoped extension handle used by tools and hooks.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 456–476)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Chooses and builds the workspace’s index backend. An index backend stores and searches indexed text or vectors for memory and retrieval features.

**Data flow**: It receives active manifests, an optional configured backend name, and an optional credential store. It uses the configured name or default, searches extension index specs for that name, checks credential requirements, builds an extension context, and returns the backend from the spec factory. If none registers the name, it raises NotRegisteredError.

**Call relations**: Startup or workspace setup calls this after manifests are loaded. It hands the chosen backend onward to contexts, memory tools, and other code that needs indexing.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 479–499)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Chooses and builds the deploy’s embedding client. An embedding client turns text into numeric vectors used for semantic search.

**Data flow**: It receives active manifests, an optional configured name, and an optional credential store. It uses the configured name or default, finds a matching embed spec, checks whether credentials are required, creates an extension context, and returns the client from the spec factory. If no extension registers the selected name, it raises NotRegisteredError.

**Call relations**: Startup resolves this once and then passes the resulting client into index, memory, and extension contexts that need embedding support.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 502–527)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider from active extensions. This gives the system a pluggable way to search memory.

**Data flow**: It receives manifests, a credential store, optional index and embedding clients, and a provider name. It finds matching memory search provider specs. No match returns None; more than one match raises an error; exactly one match is built with that extension’s context and wrapped as a MemorySearch object.

**Call relations**: Code that wants memory search calls this after backend resolution. It relies on context_for so the provider gets the right extension scope and optional index/embed helpers.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 530–566)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Checks extension tool and object registrations at boot before any turn runs. This makes misconfigured deployments fail early instead of failing later during a user request.

**Data flow**: It receives manifests and an optional credential store. It gathers built-in tools plus extension and connector tools, checks that credential-requiring tool extensions have credential support, builds the object registry including core credential objects, turns object verbs into tools, and constructs a ToolRegistry to trigger duplicate-name and registration checks.

**Call relations**: Startup validation calls this as a safety pass. It uses core_object_kinds and object_registry in the same way turn_tools later does, but without building per-workspace extension contexts.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.fire`  (lines 602–677)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, audience_member_id: UUID | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one turn event and combines their effects. A hook is extension code that reacts to moments such as before a tool call, after a tool call, or when a user submits a prompt.

**Data flow**: It receives an event, its payload, optional turn and agent records, and speaker/audience member IDs. It runs matching hooks in order with a HookContext, enforcing a timeout. Hooks may deny an action, modify tool input, modify tool output, or inject extra context depending on the event. It returns a HookResolution describing the final denial, modified input/output, and injected text.

**Call relations**: The turn engine calls this at specific lifecycle points. For gating events such as pre_tool_use and user_prompt_submit, a hook error fails closed as a denial. For non-gating events, errors are logged and skipped so observation hooks do not break the turn.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 680–706)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> HookChain
```

**Purpose**: Builds the hook chain used during a turn. It binds each declared hook to the ExtensionContext of the extension that owns it.

**Data flow**: It receives manifests, a credential store, and optional index and embedding clients. For each manifest with hooks, it requires a credential store, creates an extension context, skips page_change hooks because those are run elsewhere, groups the remaining hooks by event, and returns a HookChain.

**Call relations**: Per-turn setup calls this before the turn loop starts firing events. It creates BoundHook objects that HookChain.fire later executes in manifest order.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### Assistant packs
Built-in assistant pack definitions select local or hosted extension bundles for assistant-style workspaces.

### `packs/assistant_dev/ufo_pack_assistant.py`

`config` · `config load`

A “pack” is like a preset recipe. Instead of asking a user or operator to enable dozens of separate features one by one, this file gives that whole collection a single name: “assistant”. When the system is configured to use this pack, it activates the listed extensions together.

The pack includes memory, search and research tools, web browsing inside a sandbox, code execution, document generation, scheduled tasks, connectors to outside services, model providers, debugging tools, and other assistant-facing capabilities. Importantly, this version is meant to run on the project’s own local storage and indexing pieces, rather than relying on a hosted managed setup. That is why it is described as different from an “assistant_hosted” pack.

The file itself is intentionally simple. It does not implement the tools, teach onboarding text, or define new skills. Each extension brings its own behavior and user-facing pieces. This file only names the pack, gives it a version, lists the extensions that belong to it, and provides a small function that turns those constants into a Pack object the rest of the system can load.

#### Function details

##### `pack`  (lines 46–47)

```
def pack() -> Pack
```

**Purpose**: This function builds the actual Pack object for the “assistant” bundle. The system uses it when it needs a concrete description of this pack’s name, version, and included extensions.

**Data flow**: It reads the file’s fixed values: the pack name, its version, and the tuple of extension names. It passes those values into the Pack constructor. The result is a Pack object that represents this assistant preset and can be used by the configuration system.

**Call relations**: When the pack loader imports this file, it calls this function to obtain the pack definition. The function then hands the name, version, and extension list to Pack.__init__, which turns those plain values into the standard pack object used by the rest of the system.

*Call graph*: 1 external calls (__init__).


### `packs/assistant_hosted/ufo_pack_assistant_hosted.py`

`config` · `config load`

This file is a small manifest for a pack, which is a named bundle of features the system can activate from configuration. In everyday terms, it is like a menu preset: choosing the “assistant_hosted” preset tells the system to bring together memory, research, browser use, document tools, scheduled tasks, coding help, connectors, Slack support, and more.

The important point is that this pack uses hosted or managed infrastructure rather than only local pieces. For example, it includes Turbopuffer for the memory search index, Redis for live shared state, E2B for sandboxed code execution, and sandboxed Chrome for browser control. The assistant still uses OpenAI embeddings for turning text into searchable numeric representations, but the storage and lookup layer is provided by Turbopuffer.

The file does not implement those capabilities itself. Each named extension owns its own behavior and setup. This file simply names the pack, gives it a version, and lists the extensions that should be included. Without this file, a user could not select the hosted assistant as one coherent package; they would need to know and configure all of these pieces separately.

#### Function details

##### `pack`  (lines 55–56)

```
def pack() -> Pack
```

**Purpose**: This function creates the pack description that the rest of the system can read. It packages the pack name, version, and extension list into a `Pack` object, which is the standard form used by the manifest system.

**Data flow**: It starts with the constants in this file: the pack name, its version, and the list of extension names. It passes those values into `Pack`, which turns them into a structured object. The result is returned to the caller so the system can load the hosted assistant bundle.

**Call relations**: When the pack system asks this file what it provides, `pack` is the entry it uses. Inside, it hands the collected name, version, and extensions to `Pack.__init__`, so the broader configuration machinery receives one clean object instead of separate loose values.

*Call graph*: 1 external calls (__init__).


### Workflow packs
Domain-specific pack manifests assemble extensions and skills for manager and startup-founder workflows.

### `packs/chief_of_staff/ufo_pack_chief_of_staff.py`

`config` · `startup/config load`

This file is the pack’s manifest, meaning it is like the label and packing list on a box. It does not run the chief-of-staff workflows itself. Instead, it tells the larger UFO system what to install and connect so those workflows can exist.

The pack is built around a manager using one Slack-based front door to review meetings, notes, people files, org data, daily logs, reminders, and follow-up items. To make that possible, the file lists the extensions the pack depends on. These include connectors for outside services, memory and search tools, a knowledge graph for linking facts together, Slack access, scheduled tasks, page alerts, todo tracking, skill creation, and self-improvement features.

It also names four skills stored in a nearby skills folder: setup, sync, prep, and triage. In plain terms, setup helps the user grant access through conversation, sync reviews incoming information and proposes updates, prep builds meeting briefs, and triage captures the user’s judgment so future runs improve.

Without this file, the system would not know that these pieces belong together. The skills might exist on disk, but they would not be presented as one installable, loadable pack with the right dependencies.

#### Function details

##### `pack`  (lines 42–48)

```
def pack() -> Pack
```

**Purpose**: This function builds and returns the pack definition that the UFO system can load. It packages the pack name, version, required extensions, and skill folders into one object.

**Data flow**: It reads the constants defined in this file: the pack name, version, extension names, skills directory, and skill names. It turns each skill name into a SkillSpec pointing at that skill’s folder, then places everything into a Pack object. The result is a complete description of the chief-of-staff pack, ready for the surrounding system to register or install.

**Call relations**: When the larger pack-loading system asks this file what it provides, this function is the answer. It creates SkillSpec objects for each named skill, then hands those skill specs into Pack so the rest of the system can load the chief-of-staff workflows with the extensions they require.

*Call graph*: 2 external calls (__init__, __init__).


### `packs/yc/ufo_pack_yc/manifest.py`

`config` · `startup`

This file is like the label and packing list on a toolbox. It does not perform founder tasks itself. Instead, it describes what belongs in the YC pack so the rest of the system can load it correctly.

The file gives the pack a name, “yc,” and a version, “0.1.0.” It then lists the extensions the pack depends on, such as command-line support, memory, document handling, scheduled tasks, todos, embeddings, and a knowledge graph. In plain terms, these are the supporting tools the pack expects to have available.

It also points to a local skills folder and names two skills: “founder-operations” and “company-diligence.” A skill is a packaged unit of behavior or instructions that the system can use for a particular kind of work. The `pack()` function turns this simple manifest information into a `Pack` object that the UFO framework can understand.

Without this file, the system would not know that this YC pack exists, which extensions it needs, or where to find its skills. The pack’s contents might be present on disk, but there would be no standard way for the system to discover and load them.

#### Function details

##### `pack`  (lines 24–30)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the formal description of the YC pack. The system uses this description to know the pack’s name, version, required extensions, and included skills.

**Data flow**: It starts with constants defined in this file: the pack name, version, extension names, the skills folder path, and the skill names. It turns each skill name into a `SkillSpec`, which points to that skill’s folder, then gathers everything into a `Pack` object. The result is a ready-to-use pack description; it does not change files or external state.

**Call relations**: When the UFO framework wants to discover this pack, it calls `pack`. Inside that call, the function creates `SkillSpec` objects for the named skill folders and then hands those, along with the pack metadata and extension list, to `Pack` so the broader system can load the pack as one coherent bundle.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The combined settings that tell the service which features, adapters, limits, and deployment options to use.
- `reg-extension-pack-lock` — The saved choice of active packs and installed extensions for a workspace.
- `reg-extension-state-store` — The per-workspace key-value storage area where extensions keep their own persistent settings and data.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-tool-catalog` — The shared list of tools the agent may call, including their names, inputs, safety labels, and handlers.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-background-job-registry` — The registered set of built-in and extension background workflows that the scheduler can run.
- `reg-extension-catalog-cache` — The discovered extension/pack catalog and update metadata used to show available extensions and resolve pinned installs before loading capabilities.
