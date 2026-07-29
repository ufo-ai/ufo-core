# Core loader and runtime surface declarations  `stage-2.2.1`

This stage is part of startup and shared support. It is where the system discovers optional extensions and learns what user-facing surfaces they add. The main worker is core/src/ufo/ext/loader.py. It acts like a gatekeeper at a building entrance: it finds installed extensions, checks whether each one is allowed, then converts its declared features into things the rest of UFO can use, such as tools, hooks, skills, credentials, and backends.

The manifest files are the extension “identity cards.” The debugger manifest names the debugger extension and says which debugger web surface to mount. The UFO manifest exposes the live UFO shell surface. The web manifest declares the web surface, its routes, and which tools the web side may call.

The __init__.py files for debugger, eval_env, redis_hub, repl, ufo, and web mostly serve as import markers. They tell Python that these folders are usable packages. Some add a short label, but they do not run the extension logic themselves.

## Files in this stage

### Extension loading gateway
The core loader discovers installed extensions, validates whether they may run, and converts their declarations into runtime capabilities.

### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and turn setup`

Extensions are how UFO grows new abilities without changing core code. This file is the loading dock: extensions arrive through Python entry points, which are advertised plug-in hooks installed with a package. The loader asks each entry point for a manifest, meaning a structured declaration of what that extension provides.

The file also enforces trust. If a lockfile exists, only the extensions named there are used, and their source code must still match the recorded digest, a fingerprint of the installed files. If the code changed, startup fails instead of quietly running something different. Without a lockfile, the system acts like a development setup and loads every discovered extension.

After discovery, this file turns manifests into practical runtime pieces: tool definitions for a turn, credential injection rules, object kinds, skill registries, subagent profiles, search and embedding backends, migration folders, and hook chains. A hook is extension code that reacts at specific moments, such as before a tool is used. The HookChain runs those hooks in order and decides whether to deny, modify, or add context.

Without this file, extensions would be scattered guesses instead of a single, checked source of truth. Startup might miss tampering, duplicate names could shadow each other, and the turn loop would not know which extension-owned abilities are safe to call.

#### Function details

##### `lockfile_path`  (lines 139–140)

```
def lockfile_path() -> Path
```

**Purpose**: This chooses where the extension lockfile lives. It lets an operator override the default path with an environment variable, while keeping a simple default for normal runs.

**Data flow**: It reads the process environment for the lockfile path setting. If the setting is present, it wraps that text as a filesystem path; otherwise it uses the default file name. The result is a Path object pointing to the lockfile location.

**Call relations**: When load_manifests needs to decide whether the deployment is pinned or in development mode, it asks this function where to look for the lockfile.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 143–144)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: This reads a lockfile from disk and turns it into a validated Lockfile object. It protects the loader from accepting malformed pinned extension data.

**Data flow**: It receives a filesystem path, reads the file text, and validates that JSON text against the Lockfile shape. It returns a Lockfile containing the pinned UFO version and extension pins.

**Call relations**: load_manifests calls this after it finds a lockfile, so it can compare the installed extensions against the operator-approved list.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 147–148)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: This writes a Lockfile object back to disk as neat JSON. Command-line tooling can use it to record the exact extension set a deployment should run.

**Data flow**: It receives a path and a Lockfile object. It serializes the object to indented JSON, adds a final newline, and writes that text to the target file. Its output is the changed file on disk.

**Call relations**: This is the counterpart to read_lockfile. The loader reads lockfiles during startup, while tools that pin or bundle extensions can call this to create the file the loader later trusts.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 151–161)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: This finds every installed UFO extension advertised in the Python environment. It also rejects duplicate extension names, so later code never has to guess which one wins.

**Data flow**: It asks Python's entry point system for entries in the UFO extension group. For each one, it loads the callable, calls it to get a Manifest, and stores that manifest together with the entry point under the manifest name. It returns a dictionary of discovered extensions.

**Call relations**: load_manifests uses this as the raw installed-extension list before applying the lockfile or pack filtering. migration_locations also uses it so it can find the installed package location for each active extension.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 164–175)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: This finds installed packs, which are bundles of extensions and pack-level additions such as skills. It rejects duplicate pack names for the same reason extension discovery does: ambiguity should stop startup, not surprise users later.

**Data flow**: It asks Python's entry point system for entries in the UFO pack group. Each entry is loaded and called to produce a Pack object, which is stored by pack name. It returns a dictionary of all discovered packs.

**Call relations**: _pack_manifests calls this when the configuration selects a named pack, so the loader can narrow the active manifests to that pack's bundled extensions.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 178–183)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: This finds import information for the package that owns an extension entry point. That information is needed to locate source files and migration folders.

**Data flow**: It receives an entry point, takes the top-level module name, and asks Python's import machinery where that module comes from. If the source cannot be found, it raises an error; otherwise it returns the module specification.

**Call relations**: extension_digest uses this to find the source files to fingerprint. migration_locations uses it to locate an extension package on disk.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 186–191)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: This turns a module specification into the directory that represents the extension package. It works for both multi-file packages and single-file modules.

**Data flow**: It receives import metadata. If the extension is a package directory, it returns that directory. If it is a single file, it returns the file's parent directory.

**Call relations**: migration_locations calls this after _entry_spec so it can check whether the active extension has a migrations folder beside its code.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 194–214)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: This computes the fingerprint used to prove an installed extension still matches the pinned version in the lockfile. It catches edits anywhere in the extension source package, not just the entry file.

**Data flow**: It receives an extension entry point, finds the owning package, gathers source files while skipping machine-made bytecode caches, and hashes both file names and file contents in stable order. It returns a string beginning with the sha256 prefix.

**Call relations**: load_manifests calls this for every pinned extension. If the computed digest differs from the lockfile, startup fails instead of running changed code.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 217–234)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: This lists database migration folders contributed by active extensions. Database migrations are versioned changes to the database schema, such as adding tables.

**Data flow**: It discovers installed extensions, loads the active manifests, optionally narrowed to a pack, then looks beside each active installed extension for a migrations directory. It returns the paths that actually exist.

**Call relations**: Migration-running code can call this to layer extension database changes on top of core database changes. It relies on load_manifests for the approved active set and on discovery helpers to find each package on disk.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 237–262)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: This decides which extension manifests are active for the current run. It is the main doorway from installed extensions into the rest of UFO.

**Data flow**: It starts with all discovered installed extensions. If no lockfile exists, it treats all of them as active. If a lockfile exists, it loads the pins, requires each pinned extension to be installed, checks each digest, and keeps only the verified manifests. If a pack name is provided, it narrows the result through _pack_manifests. It returns the active manifests as a tuple.

**Call relations**: Many higher-level startup paths depend on this result, directly or indirectly. migration_locations calls it before finding extension migration folders, and other callers use its output to build tools, hooks, skills, and backends.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 265–297)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: This converts a selected pack into the exact manifest list UFO should use. A pack acts like a curated bundle, so this makes sure all bundled extensions are both installed and active.

**Data flow**: It receives a pack name and the already-active extension map. It discovers installed packs, finds the named one, collects each bundled extension's manifest, checks that none are missing, then appends a synthetic manifest for the pack's own skills and onboarding steps. It returns the pack-specific manifest tuple.

**Call relations**: load_manifests calls this only when configuration asks for a pack. discovered_packs supplies the available pack declarations, and the synthetic Manifest lets normal manifest-consuming code treat pack-level additions like extension additions.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 300–309)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: This extracts command-line credential declarations from connector extensions. These declarations tell the engine which environment variable should carry a credential marker for a provider.

**Data flow**: It receives active manifests, walks through their connectors, and keeps connectors that declare a CLI credential. It returns a dictionary keyed by provider name, with the CLI credential details as values.

**Call relations**: injecting_slots calls this while checking environment-variable claims, so connector credentials and injected credential slots do not silently fight over the same sandbox variable.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 312–385)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: This collects credential slots that should be injected into tool sandboxes and checks that their names, sentinels, hosts, and environment variables do not conflict. It prevents subtle authentication bugs before any turn runs.

**Data flow**: It receives active manifests and pulls out credential slots that have injection rules. It records declared slot names, connector CLI exports, sentinel ownership, host metering dimensions, and environment-variable claims. If two declarations would make one variable carry two different meanings, or if a host choice points to an undeclared slot, it raises an error. Otherwise it returns the injectable slots.

**Call relations**: It uses connector_clis to include connector-owned environment variables in the same collision check. Downstream sandbox and proxy setup can then trust the returned slots because this function has already refused ambiguous declarations.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 388–439)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, audience: Audience) -> tuple[tuple[ToolDef
```

**Purpose**: This builds the complete tool set available during a turn. It combines built-in tools, extension tools, connector tools, and object-related tools, while remembering which extension context belongs to each extension tool.

**Data flow**: It receives active manifests, optional credential and search objects, and an audience. It starts with built-in tools, then for each manifest creates an ExtensionContext when the extension contributes tools or objects. It appends declared tools, records their context by tool name, adds extension object kinds, adds core object kinds, and turns the object registry into object verb tools. It returns the final tool list and the extension-context map.

**Call relations**: The turn engine can call this before dispatching tools. It calls core_object_kinds to include core credential objects, context_for to create scoped extension access, and object_registry plus ObjectVerbs to expose object operations as tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `core_object_kinds`  (lines 442–456)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None) -> tuple[BoundKind, ...]
```

**Purpose**: This defines the object kinds that core UFO itself contributes, especially the credential object view. Object kinds are categories of stored things that can be read or acted on through object verbs.

**Data flow**: It receives active manifests and an optional credential store. It gathers declared credential slots from the manifests, builds a credential object store backed by those slots and the live credential store, wraps it as an ObjectKind, and returns it as a BoundKind with no extension owner.

**Call relations**: turn_tools calls this when building runtime object tools. validate_ext_tools calls it during startup validation so core object verbs participate in the same duplicate-name checks as extension object kinds.

*Call graph*: called by 2 (turn_tools, validate_ext_tools); 4 external calls (__init__, __init__, __init__, declared_slots).


##### `skill_registry`  (lines 459–482)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: This builds the deploy's registry of loadable skills. A skill is reusable instruction or capability content that can be loaded by name.

**Data flow**: It starts with core skills, then reads skill specifications from active manifests by discovering skills on disk. It refuses any duplicate skill name. It then adds any generated skills, also checking for name collisions, and returns a SkillRegistry keyed by skill name.

**Call relations**: Startup code can call this once to prepare skill lookup and skill-index rendering. It hands off disk paths to discover_skills and wraps the final map in SkillRegistry.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 485–489)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: This gathers subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent and its default capabilities.

**Data flow**: It receives manifests and flattens every manifest's subagent list in load order. It returns a tuple of profiles without changing them.

**Call relations**: Serving or turn setup code can feed this into the SubagentRegistry. Duplicate-name enforcement is left to that registry, so this function stays focused on collecting the active declarations.


##### `durable_surfaces`  (lines 492–498)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: This identifies conversation surfaces whose replies are delivered through a durable writeback path. In plain terms, these are channels where UFO must remember to post results back later.

**Data flow**: It receives active manifests, scans their surface declarations, and selects surface names that have a post handler. It returns those names as a frozen set.

**Call relations**: Admission or conversation setup code can use this set to decide when a turn needs a writeback row. The function is a direct derivation from manifests, so extension surface declarations drive the behavior.


##### `turn_subagent_grants`  (lines 501–511)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: This collects extra tool grants that extensions give to subagent profiles they may not own. It lets one extension widen a subagent's tool access without hard-coding that relationship inside the profile.

**Data flow**: It receives manifests, walks through every subagent tool grant, and groups tool names by target profile. If several manifests grant tools to the same profile, their tool names are unioned. It returns a dictionary from profile name to a frozen set of tool names.

**Call relations**: The turn loop can combine this map with each profile's own tools and then intersect with the live tool registry. Unknown profiles or missing tools naturally fall away later rather than breaking collection here.


##### `turn_runtime_skills`  (lines 514–534)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This asks active extensions for runtime skills that depend on the current workspace or agent context. These are not just files on disk; they are produced by extension code at runtime.

**Data flow**: It receives manifests, an optional credential store, and optional index/embed clients. For each manifest with a runtime skill provider, it requires a credential store, builds an ExtensionContext with the extension's declared credential slots, awaits the provider, and appends the returned skills. It returns all runtime skills in manifest order.

**Call relations**: Turn setup can call this when it needs skills tied to the live workspace. It uses context_for so the extension provider receives the same scoped access pattern as tools and hooks.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 540–560)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: This selects and builds the workspace's index backend, which is the component used for searching indexed content. If the configuration does not name one, it looks for the default backend.

**Data flow**: It receives manifests, an optional configured backend name, and an optional credential store. It searches extension index specs for the selected name. When found, it checks whether credentials are required, builds an ExtensionContext, calls the backend factory, and returns the IndexBackend. If no extension registers the name, it raises NotRegisteredError.

**Call relations**: Startup or workspace setup uses this to turn configuration into a real index implementation. It hands the selected extension a scoped context through context_for, and uses NotRegisteredError so callers can distinguish missing registration from other failures.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 563–583)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: This selects and builds the deployment's embedding client. An embedding client converts text into numeric vectors used for semantic search, meaning search by meaning rather than exact words.

**Data flow**: It receives manifests, an optional configured name, and an optional credential store. It searches extension embedding specs for the chosen name or the default. For a match, it checks credential availability, creates an ExtensionContext, calls the spec's factory, and returns an EmbedClient. If none match, it raises NotRegisteredError.

**Call relations**: Startup code can call this once and pass the result to indexing, memory tools, and derivation jobs. Like index_backend, it uses context_for to bind the chosen extension to its allowed credentials.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 586–611)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: This builds a named memory search provider from active extensions. Memory search is the feature that lets UFO look up relevant past or stored information.

**Data flow**: It receives manifests, credential and search clients, and a provider name. It finds specs with that name. If none exist, it returns None. If more than one exists, it raises an error because the name is ambiguous. For the single match, it checks credentials, builds an ExtensionContext, calls the provider builder, wraps the result as MemorySearch, and returns it.

**Call relations**: Code that wants optional memory search can call this and handle None when no provider exists. It uses context_for so the provider runs with the declaring extension's scoped access.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 614–650)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: This performs startup safety checks for extension tools and object kinds. It makes misconfigured extensions fail at boot instead of failing later during a user turn.

**Data flow**: It receives active manifests and an optional credential store. It builds a list of built-in plus extension-declared tools, checks that tool-declaring extensions with credential slots have a credential key available, builds an object registry from core and extension object kinds, adds object verb tools, and finally constructs a ToolRegistry. If names collide or registration gates fail, construction raises an error.

**Call relations**: Deployment startup can call this before serving traffic. It calls core_object_kinds, object_registry, ObjectVerbs, and ToolRegistry to exercise the same registration path that turn_tools later uses per workspace.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.__post_init__`  (lines 687–690)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that every hook in a HookChain is meant for the same audience as the chain itself. An audience is the target visibility or sharing scope for extension behavior.

**Data flow**: After a HookChain is created, it flattens all bound hooks from all event groups and compares each hook context's audience with the chain audience. If any differ, it raises a ValueError; otherwise the chain is left unchanged.

**Call relations**: This runs automatically after HookChain construction. turn_hooks builds the chain, and this method guards against accidentally mixing hooks from different audience scopes before HookChain.fire ever runs.


##### `HookChain.fire`  (lines 692–767)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: This runs all hooks for one event and folds their answers into one decision. Hooks can deny an action, modify tool input or output, or inject extra context depending on the event.

**Data flow**: It receives an event, the event payload, optional turn and agent records, and an optional speaker member ID. It selects hooks for that event, skips tool-specific hooks that do not match the current tool, builds a HookContext for each remaining hook, and runs it with a timeout. It checks whether the returned outcome is allowed for that event. Gating events fail closed: an error becomes a denial. Non-gating hook errors are logged and ignored. It returns a HookResolution containing any denial, final modified input or output, and joined injected text.

**Call relations**: The turn engine calls this at specific moments such as before tool use or after tool use. turn_hooks prepares the HookChain, while this method does the live execution and uses HookContext, timeout protection, and logging to keep extension reactions controlled.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 770–801)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, audience: Audience) -> HookChain
```

**Purpose**: This builds the HookChain used during a turn. It binds each extension-declared turn hook to that extension's scoped context and groups hooks by event.

**Data flow**: It receives manifests, credential and search clients, and an audience. For each manifest with hooks, it requires a credential store, creates an ExtensionContext, ignores page_change hooks because those are driven elsewhere, and appends the remaining hooks to their event group as BoundHook objects. It returns a HookChain for the requested audience.

**Call relations**: Turn setup calls this before the turn loop begins firing hook events. It creates the BoundHook objects that HookChain.fire later executes, using context_for so hook handlers receive the same extension-scoped access as other extension runtime code.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### Debugger and evaluation packages
These files declare importable debugger and evaluation-environment extension packages and expose the debugger web surface manifest.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python, a folder can contain an `__init__.py` file to say, “treat this folder as an importable package.” That is the main job here. It lets other parts of the project refer to code inside `extensions/debugger/ufo_ext_debugger` using normal Python import paths. Think of it like a label on a drawer: the label does not contain the tools, but it tells the system that the drawer exists and can be opened by name.

Because the file is empty, it does not run setup code, create objects, register plugins, or change program state when imported. That is important: importing this package should be lightweight and have no side effects. The actual debugger extension behavior must live in other files under this package. Without this file, some Python environments or packaging tools may not recognize the directory as a package, which could make imports fail or make the extension harder to discover.


### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

A manifest is a small declaration that lets the main UFO system discover an extension and know how to plug it in. Without this file, the debugger extension would have code, routes, and a surface name, but the host would not have a clear, standard way to find them or attach them to the running system.

This file names the extension as "debugger" and gives it version "0.1.0". Its main job is the `manifest` function, which creates a `Manifest` object. That object says: this extension offers one surface, called `SURFACE_DEBUG`; the requests for that surface should use the route table named `ROUTES`; and before the surface is used, the system should identify the correct operator workspace by calling `resolve_operator_workspace`.

In plain terms, this is like a sign-in sheet for the extension. It does not implement the debugger itself. Instead, it points the host to the debugger’s front door and explains how the host should decide where that door belongs.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the standard description the host system uses to discover and mount the extension. Someone would use this when loading extensions so the debugger surface becomes available in the right place.

**Data flow**: It starts with fixed information in this file: the extension name, version, debugger surface name, debugger routes, and the workspace-identification function. It wraps the surface details in a `SurfaceSpec`, then wraps that in a `Manifest`. The result is a complete manifest object that the host can read to know what this extension provides.

**Call relations**: When the extension system asks this file for its manifest, this function creates the needed objects. It calls `SurfaceSpec.__init__(ext)` to describe the debugger surface, then calls `Manifest.__init__(ext)` to package that surface together with the extension’s name and version.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `import time`

This file is the front door for the `ufo_ext_eval_env` package. It does not define any code of its own, but its docstring explains the package’s job: to provide predictable, fake versions of mailbox and calendar services for evaluation. In plain terms, this package lets the system practice or be tested against email and calendar behavior without contacting real services like Gmail, Outlook, or a live calendar server. That matters because real services can change, fail, rate-limit requests, or contain private data. A deterministic environment is like a staged practice room: every run can start with the same mailbox and calendar state, so results are easier to compare and debug. Without this package boundary, Python would not treat this folder as an importable package in the usual way, and readers would have less immediate context about why these fake connectors exist.


### Hub and shell package entries
These package entry files make Redis hub, REPL, and UFO shell extension code importable, with the UFO shell manifest declaring its live surface.

### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

In Python, a folder usually needs an `__init__.py` file to be treated as an importable package. This file is that marker for the Redis Hub extension package. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the project and can be opened with normal import statements. Because the file is empty, it does not define settings, start connections, register features, or change program behavior at import time. Its value is structural: without it, depending on the Python version and packaging setup, code that tries to import `ufo_ext_redis_hub` or modules inside it might fail or behave differently.


### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a directory can be treated as an importable package when it has an `__init__.py` file. That is the job of this file: it lets code refer to the REPL extension package by name, such as importing modules from `extensions/repl/ufo_ext_repl`.

There is no setup logic, no configuration, and no functions here. Think of it like a label on a folder: the label does not contain the documents, but it tells Python, “this folder is part of the program and can be opened through the import system.”

Without this file, depending on the Python version and packaging setup, imports for this extension might fail or behave differently. Its presence makes the package boundary explicit and helps tools, installers, and readers understand that this directory is meant to be used as Python code.


### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like putting a label on a box: the label does not contain the tools, but it lets the rest of the system find and open the box correctly. Because this file has no code, it does not configure anything, create objects, or run side effects when the package is imported. Its value is structural: it makes imports such as modules under `extensions/ufo/ufo_ext_ufo` work in the expected Python package layout. If it were missing in environments that still rely on explicit package markers, code that tries to import this extension package could fail or behave differently.


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

This is the extension’s calling card. When the larger system looks for installed extensions, it needs a small, standard description of what each extension offers. This file provides that description for the `ufo` extension.

The main idea is simple: if this extension is installed, it should be mounted and made available. It does not define extra user-facing configuration choices, and it does not ask for stored credential slots. Instead, incoming users are identified through the extension’s own workspace resolver, and bearer tokens are checked against an environment secret named `UFO_TOKEN_SECRET`.

The file imports the route definitions and workspace-identification function from the extension’s surface code. It then packages them into a `SurfaceSpec`, which is a description of one exposed interaction point, or “surface.” Here, that surface is the terminal-style live connection used by the `ufo` shell client. Finally, it wraps that surface in a `Manifest`, which is the object the host system can read during extension discovery.

Without this file, the core application would not know that the `ufo` extension exists, what version it is, or which route should be mounted for clients to use.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the `ufo` extension. The host uses this to learn the extension’s name, version, route surface, and workspace-identification hook.

**Data flow**: It starts with the file’s constants, `NAME` and `VERSION`, and the imported surface details: the surface name, route list, and workspace resolver. It places those into a `SurfaceSpec`, then places that surface spec into a `Manifest`. The result is a complete extension description returned to whoever is loading extensions.

**Call relations**: During extension discovery, the host calls `manifest` to ask this file what the extension provides. Inside that call, it creates a `SurfaceSpec` for the live `ufo` route, then creates a `Manifest` that carries that surface back to the host so the route can be mounted.

*Call graph*: 2 external calls (__init__, __init__).


### Web extension surface
The web extension package marker and manifest declare the web surface, routes, and permitted web-side tools.

### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import/package discovery`

This file does not contain any executable code, but it still has a practical job. In Python, an `__init__.py` file tells Python that a folder should be treated as a package, meaning its contents can be imported using dotted names like `ufo_ext_web.something`. You can think of it like a label on a drawer: the drawer may hold many tools, and the label lets the rest of the workshop find them reliably. Without this file, some Python tools or older import setups might not recognize this directory as a package, which could make imports fail or make packaging less predictable. Because it is empty, it does not set up defaults, expose shortcuts, or run startup code. Its importance is mostly structural: it gives the web extension a clear package boundary.


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup / extension discovery`

This file is like the web extension’s registration card. When the core system discovers this extension, it needs a compact answer to questions such as: What is this extension called? What version is it? What actions can it perform? What web routes should be mounted? This file provides that answer through a manifest, which is a structured description of an extension.

The web extension is treated as a “surface,” meaning a place where users interact with the system. In this case, the surface is the web interface. The manifest points the core system to the web routes that should become available, and to the function that identifies which workspace a web request belongs to. It also declares the access tools used by the web extension, which are the allowed operations for maintaining the web audience.

One important detail is what is not here: there are no credential slots and no configuration switch. The comment explains that the user’s own session cookie carries their token, so the extension does not need a separate bot secret. Also, if this extension is installed, it is mounted automatically.

#### Function details

##### `manifest`  (lines 16–22)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the object the core system reads to know how this extension should be plugged in. Someone would use this when loading extensions so the web interface can be mounted correctly.

**Data flow**: It starts with constants imported from nearby web-extension modules: the extension name, allowed web access tools, route definitions, the web surface name, and the workspace-identifying function. It packages those into a Manifest object, including one SurfaceSpec that describes the web surface. The result is a ready-to-use manifest object for the core system.

**Call relations**: During extension loading, the system calls this function to ask the web extension to describe itself. The function creates a SurfaceSpec for the web interface, then places that inside a Manifest so the core system can mount the routes and know how to identify incoming web requests.

*Call graph*: 2 external calls (__init__, __init__).
