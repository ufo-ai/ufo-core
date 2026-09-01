# Extension Discovery, Manifest Loading, and App Registration  `stage-4`

This stage is shared setup that happens before the system can use optional apps and extensions. It is like opening a toolbox, checking which tools are allowed, reading each label, and putting the tools in the right drawers. The manifest model defines the label format: how an extension declares tools, web routes, jobs, credentials, hooks, agents, skills, backends, and object types. The loader is the main doorway. It finds installed extensions, filters them, reads their manifests, and registers what they add. The store is the small app-store layer that searches the extension catalog and records installs or removals in the lockfile.

Built-in workspace apps and packaged agents seed new workspaces with known apps such as Chat, Code, Wiki, Issues, and helper agents. Extension-provided hooks, backends, objects, and stores let add-ons react during work and add services such as connectors or Redis-backed storage.

The individual manifest files are registration forms for concrete extensions: Slack, web portal, sites, documents, monitors, scheduled tasks, sources, iMessage, debugger, gbrain, self-improvement, skill creation, and UFO shell. Small __init__ files simply make some extension packages importable.

## Sub-stages

- [Built-In Workspace Apps and Packaged Agents](stage-4.1.md) `stage-4.1` — 24 files
- [Extension-Provided Objects, Hooks, Backends, and Stores](stage-4.2.md) `stage-4.2` — 9 files

## Files in this stage

### Extension discovery gateway
Commands and host startup enter through the store and loader to select installed extensions and turn their declarations into runtime registrations.

### `core/src/ufo/host/ext/store.py`

`domain_logic` · `extension management commands`

UFO extensions are Python packages that can add behavior to the system. This file connects two important records: a catalog, which says which extensions are available, and a lockfile, which says exactly which extensions UFO should load when it starts. The lockfile pins each extension by name, version, and a digest, which is like a fingerprint of the installed package. That fingerprint helps make startup repeatable instead of depending on whatever happens to be nearby.

The file defines simple shapes for catalog data: each catalog entry has a name, version, and an optional disabled flag. Disabled entries are “bundle-only”: they may be included by bundle-building tools, but normal install refuses them.

The main class, ExtensionStore, acts like a clerk at a small library desk. Search looks through the catalog and marks which matching entries are already pinned in the lockfile. Install checks that the extension is listed, not disabled, and actually installed in the current Python environment before writing a pin. Remove checks that the extension is currently pinned, then rewrites the lockfile without it.

A key safety behavior is that the store will not pin something just because it appears in the catalog. The extension must also be discoverable in the running environment, so UFO does not write a lockfile entry it cannot later load.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked Catalog object. This gives the rest of the code a clean, predictable list of available extensions instead of raw text.

**Data flow**: It takes a file path. It reads the text from that path, parses the TOML text into ordinary data, and then validates that data against the expected catalog shape. It returns a Catalog object, or validation/parsing will fail if the file is not shaped correctly.

**Call relations**: This is the doorway from the catalog file into the extension store. It relies on the path object to read the file and on TOML parsing to understand the file format before the store can search or install from it.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Finds the installed version of the UFO package itself. The lockfile uses this as an anchor so the pinned extensions are tied to a particular UFO version.

**Data flow**: It takes no direct input. It asks Python’s package metadata system for the version of the installed package named “ufo” and returns that version string.

**Call relations**: ExtensionStore._write calls this when it needs to create a new lockfile and there is no existing UFO version to preserve. In that moment, this function supplies the version label that will be written beside the extension pins.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the lockfile pin for one installed extension. A pin records the extension’s name, its manifest version, and a digest, which is a fingerprint of its source.

**Data flow**: It takes an extension name. It looks in the currently discovered installed extensions for that name. If the extension is missing, it raises an error rather than creating a bad lockfile entry. If found, it reads the extension’s manifest version, computes a digest for the installed package entry, and returns an ExtensionPin.

**Call relations**: ExtensionStore.install calls this after checking that the catalog allows the extension. This function then bridges from “the catalog says this extension exists” to “this exact installed package can be safely pinned.”

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and marks which matching extensions are already installed in the lockfile. This is what lets a user see both what is available and what is currently selected.

**Data flow**: It takes a query string. It first reads the current pins from the lockfile, then scans catalog entries whose names contain the query text. For each match, it creates a StoreListing showing the catalog name, version, disabled status, and whether the lockfile already pins it. It returns all listings as a tuple.

**Call relations**: This is a read-only operation in the store. It calls ExtensionStore._pins to learn the current lockfile state, then packages catalog matches into StoreListing results for a command or user interface to display.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Adds or replaces a lockfile pin for one extension. It refuses names that are not in the catalog, refuses bundle-only disabled entries, and refuses extensions that are not actually installed in the current environment.

**Data flow**: It takes an extension name. It searches the catalog for that exact name. If the name is missing or disabled, it raises a clear error. Otherwise it asks pin_for to create a trustworthy pin from the installed package, removes any older pin for the same name, writes the updated pin list to the lockfile, and returns the new pin.

**Call relations**: This is the main write path for installing from the store. It uses pin_for to prove and describe the installed extension, ExtensionStore._pins to keep any other existing pins, and ExtensionStore._write to save the new lockfile contents.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. This means UFO will no longer load that extension from this lockfile.

**Data flow**: It takes an extension name. It reads the current pins, checks that one of them has that name, and raises an error if not. If the pin exists, it builds a new pin list without that name and writes the changed list back to the lockfile. It returns nothing.

**Call relations**: This is the uninstall-style path for the store. It reads existing state through ExtensionStore._pins and saves the reduced state through ExtensionStore._write.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current extension pins from the lockfile, if the lockfile exists. If there is no lockfile yet, it treats the installed set as empty.

**Data flow**: It uses the store’s lockfile path. If the file exists, it reads and parses the lockfile and returns its extension pins. If the file does not exist, it returns an empty tuple.

**Call relations**: Search, install, and remove all call this helper before deciding what to show or change. It is the shared way those operations learn the current pinned-extension state.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete lockfile with a given set of extension pins. It preserves the existing UFO version anchor when possible, or uses the currently installed UFO version for a new lockfile.

**Data flow**: It takes the full tuple of pins that should appear in the lockfile. It checks whether the lockfile already exists. If it does, it reads the existing UFO version from it; if not, it gets the current package version from ufo_version. It then builds a Lockfile object with that version and the supplied pins, and writes it to disk.

**Call relations**: Install and remove call this after they have decided the new pin list. This helper is the final save step: it turns the store’s decision into the actual lockfile that the extension loader will later use.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/host/ext/loader.py`

`orchestration` · `startup and per-turn/per-request registry building`

Extensions in UFO do not register themselves by calling into the app. Instead, they publish small Python entry points, and this loader reads those entry points at startup. Think of it like a theater stage manager: each extension hands in a card saying what props, actors, and cues it provides, and this file decides what actually goes on stage.

The first job is safety and consistency. If a lockfile exists, it acts like a pinned shopping list: only the named extensions load, and their installed code must match a stored SHA-256 digest, which is a fingerprint of the files. If the code changed or an extension is missing, boot stops instead of running a surprising mix.

Once the active manifests are known, this file builds many views of them. It creates the tool list for a turn, binds each extension tool to an ExtensionContext, builds object registries for turns and member portal pages, collects credential injection rules, gathers hook chains for turn events and connection events, finds database migration folders, loads skills and subagents, and chooses pluggable index, embedding, and memory providers.

A key theme is “fail loud early.” Duplicate names, missing credential stores, conflicting environment variables, and unknown selected providers are rejected before a user turn silently breaks.

#### Function details

##### `lockfile_path`  (lines 143–144)

```
def lockfile_path() -> Path
```

**Purpose**: Finds the lockfile path the loader should use. Operators can override the default by setting an environment variable.

**Data flow**: It reads the UFO_LOCKFILE environment variable if present, otherwise uses ufo.lock. It turns that text into a Path object and returns it.

**Call relations**: When load_manifests decides whether the deploy is pinned or in development mode, it asks this helper where to look for the lockfile.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 147–148)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads a lockfile from disk and turns it into a validated Lockfile object. This makes sure the pinned extension list has the expected shape before the loader trusts it.

**Data flow**: It receives a file path, reads the file as text, parses the JSON with the Lockfile model, and returns the validated result.

**Call relations**: load_manifests uses this after it finds a lockfile, so the pinned extension names and digests can drive extension loading.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 151–152)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a Lockfile object back to disk as formatted JSON. This is the companion to reading the lockfile, used by tools that pin a deploy’s extension set.

**Data flow**: It receives a path and a Lockfile object, converts the object to indented JSON, adds a final newline, and writes it to the path.

**Call relations**: This function is not part of the boot read path in this file, but it preserves the same file format that read_lockfile later validates.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 155–169)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension in the current Python environment. It also blocks duplicate extension names and blocks third-party extensions from declaring privileged member-context access.

**Data flow**: It reads Python package entry points in the ufo.extension group. Each entry point is loaded and called to get a Manifest, then stored by manifest name together with the entry point used to locate its code.

**Call relations**: load_manifests uses this as the raw installed-extension list. migration_locations also uses it so it can connect active manifests back to their installed package folders.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 172–183)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds installed packs, which are named bundles of extensions and extra pack-level contributions. It rejects duplicate pack names so later code can choose a pack unambiguously.

**Data flow**: It reads Python entry points in the ufo.pack group, loads each one, calls it to get a Pack, and returns a dictionary keyed by pack name.

**Call relations**: _pack_manifests calls this when configuration selects a pack and needs to know which extensions that pack bundles.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 186–191)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Locates the import information for the top-level Python package that owns an extension entry point. This is needed to find source files and migration folders.

**Data flow**: It receives an EntryPoint, extracts the first part of its module name, asks Python import machinery for that package’s module spec, and returns the spec or raises if no source can be found.

**Call relations**: extension_digest uses it to find files to fingerprint. migration_locations uses it to find an extension package directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 194–199)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the directory that represents an extension package. That directory is where the loader looks for a migrations folder.

**Data flow**: It receives a module spec. If the extension is a package, it returns the package directory; if it is a single file module, it returns that file’s parent directory.

**Call relations**: migration_locations calls this after _entry_spec so it can check whether an active extension ships database migrations.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 202–218)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the fingerprint used to prove an installed extension’s code matches the lockfile. It covers the whole package, not just the entry module, so hidden file changes are caught.

**Data flow**: It receives an extension EntryPoint, locates the package source, gathers source files while skipping bytecode cache files, reads their bytes, and passes the named file contents to extension_content_digest. It returns a sha256-prefixed digest string.

**Call relations**: load_manifests calls this for each pinned extension. If the returned digest differs from the lockfile, loading stops.

*Call graph*: calls 2 internal fn (_entry_spec, extension_content_digest); called by 1 (load_manifests); 1 external calls (Path).


##### `extension_content_digest`  (lines 221–227)

```
def extension_content_digest(files: Mapping[str, bytes]) -> str
```

**Purpose**: Builds a stable SHA-256 digest from a set of named files. Including both file names and file contents means renaming or editing files changes the fingerprint.

**Data flow**: It receives a mapping from file name to bytes. It sorts names, hashes each name and each file’s contents into one combined hash, and returns the result as a sha256: string.

**Call relations**: extension_digest prepares the files from an installed package and hands them here for the actual fingerprint calculation.

*Call graph*: called by 1 (extension_digest); 1 external calls (sha256).


##### `migration_locations`  (lines 230–247)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Lists database migration directories contributed by active extensions. These are extra schema-change folders layered on top of UFO’s core migrations.

**Data flow**: It discovers installed extensions, loads the active manifests, finds each active extension’s package directory, checks for a migrations subdirectory, and returns the existing directories as strings.

**Call relations**: The migration runner can call this to know which extension migration branches to include. It relies on load_manifests so inactive installed extensions do not add database tables.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 250–273)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Decides the active extension set for this run. It either loads every discovered extension in development mode or exactly the lockfile-pinned extensions in pinned mode.

**Data flow**: It discovers installed extensions, checks whether the lockfile path exists, and then either collects all manifests or reads pins and verifies each pinned extension’s digest. If a pack is requested, it narrows the result through _pack_manifests.

**Call relations**: This is the main source of truth for active manifests. Many other builders in the system are expected to consume its result so tools, hooks, jobs, routes, and migrations all see the same extension set.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 276–309)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Turns a selected pack name into the exact manifests that pack should activate. It includes the bundled extensions plus a synthetic manifest for the pack’s own skills and onboarding content.

**Data flow**: It receives a pack name and the already-active extension manifests. It finds the installed Pack, verifies every bundled extension is active, checks for name collision with the pack, creates a Manifest for pack-level contributions, and returns the ordered tuple.

**Call relations**: load_manifests calls this only when configuration selects a pack. It lets pack content travel through the same manifest-processing paths as normal extension content.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 312–321)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from extension connectors. These declarations tell the engine which environment variables represent connector grant sentinels.

**Data flow**: It receives manifests, walks through their connectors, keeps connectors that declare a CLI credential, and returns a dictionary keyed by OAuth provider name.

**Call relations**: injecting_slots uses this map while checking for environment-variable conflicts between connector credentials and injected credential slots.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 324–397)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into sandboxed work, and checks that their names, sentinels, host choices, dimensions, and environment variables do not conflict. Without these checks, one secret or host setting could silently overwrite another.

**Data flow**: It receives manifests, extracts credential slots that have injection rules, builds the set of all declared slot names, adds connector CLI environment claims, and then validates each slot’s sentinel, host metering dimension, host-choice references, and exported environment names. It returns the valid injectable slots.

**Call relations**: It calls connector_clis to include connector-exported variables in the same namespace. The egress proxy and sandbox export logic can then use this single checked slot list.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 400–494)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the complete tool and object-action set available during one turn. It combines core tools, extension tools, connector tools, object verbs, and the extension contexts needed to run them safely.

**Data flow**: It receives active manifests plus workspace services such as credential storage, indexing, embedding, blob storage, audience, and URL settings. It creates contexts for extensions that declare tools or objects, adds normal tools to the tool list, registers bound object actions separately, builds object kind and action registries, appends object-verb tools, and returns the tool definitions, tool-to-context map, and ObjectVerbs.

**Call relations**: Turn execution uses this when preparing what the model or dispatcher may call. It delegates core object-kind construction to core_object_kinds and registry validation to the object and action registry builders.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `member_object_registry`  (lines 506–571)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object kinds and actions visible to member-facing pages outside a live turn. This lets the portal show rows and controls using the same extension declarations as turn-time code.

**Data flow**: It receives manifests and optional service handles, creates workspace-level extension contexts for object kinds and bound actions, combines them with core object kinds and built-in actions, validates the registries, and returns a MemberObjectRegistry.

**Call relations**: Portal-style readers call this when they need object metadata and actions without a conversation audience. It uses core_object_kinds so the member portal sees the same core extension, credential, surface, and artifact objects as turns do.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `frame_admissible`  (lines 574–588)

```
def frame_admissible(manifests: tuple[Manifest, ...], registry: MemberObjectRegistry) -> frozenset[str]
```

**Purpose**: Computes which tools and object actions an embedded frame page is allowed to post back to this deploy. This is an allow-list for frame-originated calls.

**Data flow**: It receives manifests and a member object registry, gathers built-in and extension-declared tools, asks frame_admissible_ids to filter them by their frame presentation settings, and returns the allowed identifiers as a frozen set.

**Call relations**: The frame action lane can use this result when a browser frame posts a requested call. It depends on the registry’s action map so bound object actions are checked in their canonical form.

*Call graph*: 1 external calls (frame_admissible_ids).


##### `core_object_kinds`  (lines 591–638)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, *, public_base_url: str | None=None, artifact_token_secret: str='') -> tuple[BoundKind, ...]
```

**Purpose**: Creates the core object kinds that describe credentials, installed extensions, surfaces, and artifacts. These are not owned by one extension, but they are derived from the active extension set.

**Data flow**: It receives manifests, optional credential storage, and public artifact-link settings. It builds ObjectKind definitions backed by stores for declared credential slots, named extensions, registered surfaces, and artifacts, then wraps each as a BoundKind with no extension context.

**Call relations**: turn_tools, member_object_registry, and validate_ext_tools call this so their registries all include the same core object views alongside extension-owned object kinds.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 9 external calls (__init__, __init__, __init__, __init__, __init__, named_extensions, artifact_object, registered_surfaces, declared_slots).


##### `skill_registry`  (lines 641–665)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the deploy’s loadable skill registry. It starts with core skills, adds skills declared by active manifests, and can also include generated skills created at boot.

**Data flow**: It receives manifests and optional generated RuntimeSkill objects. It discovers skills from each declared skill path, rejects duplicate names, adds generated skills after bundled ones, and returns a SkillRegistry with the bundled-name set recorded.

**Call relations**: Skill loading and prompt rendering can use this registry without doing duplicate checks themselves. The function performs synchronous disk discovery once during setup.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 668–672)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent that can be made available during serving.

**Data flow**: It receives manifests, walks through each manifest’s subagents, and returns them in manifest order.

**Call relations**: The serving layer can pass this result into a SubagentRegistry. Duplicate-name validation is left to that registry so boot fails clearly if two profiles collide.


##### `durable_surfaces`  (lines 675–681)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Identifies surfaces whose replies are durable, meaning they use a post handler and need writeback polling. A surface is a place or channel where a conversation can happen.

**Data flow**: It receives manifests, scans their surface specs, keeps surface names that declare a post handler, and returns the names as a frozen set.

**Call relations**: Admission or turn setup can use this set to decide when to register writeback rows for conversations entering those surfaces.


##### `turn_subagent_grants`  (lines 684–694)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects extra tool permissions that extensions grant to subagent profiles they may not own. This lets one extension widen another profile’s available tools without editing that profile.

**Data flow**: It receives manifests, groups grant entries by target profile name, unions all granted tool names per profile, and returns a dictionary of frozen sets.

**Call relations**: The turn loop can fold this map into each profile’s own tool list before intersecting with actually available tools. Unknown profiles or missing tools can then fall away safely later.


##### `turn_member_skills`  (lines 697–739)

```
async def turn_member_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, agent_name: str) -> tuple[tu
```

**Purpose**: Builds the member-saved skill cards available to one agent’s turns, plus a loader that can materialize a selected card into a runtime skill. It filters out cards targeted to other agents.

**Data flow**: It receives manifests, credential storage, optional index and embed services, and an agent name. For each member-skill provider, it builds an extension context, fetches cards, skips cards not meant for the agent, logs and ignores duplicate names after the first, and returns the visible cards plus a materializer function.

**Call relations**: Turn setup uses this when it wants member-defined or provider-backed skills. It creates the nested turn_member_skills.materialize function so later code can load only the chosen skill by name.

*Call graph*: 2 external calls (log, context_for).


##### `turn_member_skills.materialize`  (lines 732–737)

```
async def materialize(name: str) -> RuntimeSkill | None
```

**Purpose**: Loads one member skill by name using the provider that originally advertised it. If no provider claimed the name, it returns nothing.

**Data flow**: It receives a skill name, looks it up in the captured providers dictionary from turn_member_skills, and if found calls that provider’s materialize method with its saved extension context. It returns a RuntimeSkill or None.

**Call relations**: turn_member_skills returns this function to the turn layer. It closes over the provider map built from cards, so routing from card name to provider stays consistent.


##### `member_skill_listing`  (lines 742–768)

```
async def member_skill_listing(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Materializes all member skills for management pages. Unlike turn_member_skills, it does not filter by agent targeting, because the portal listing should show the workspace’s whole set.

**Data flow**: It receives manifests, credential storage, and optional index and embed services. For each member-skill provider, it builds an extension context, asks for all materialized skills, keeps the first skill for each name, logs duplicates, and returns the listed skills.

**Call relations**: Portal or management code can call this for a full saved-skill listing. It applies the same credential requirement and duplicate behavior as turn_member_skills.

*Call graph*: 2 external calls (log, context_for).


##### `index_backend`  (lines 774–794)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and constructs the workspace’s index backend. An index backend stores and searches indexed content, and extensions can provide named implementations.

**Data flow**: It receives manifests, a configured backend name or None, and credential storage. It uses default when no name is configured, finds a matching manifest index spec, checks credentials are available if needed, creates an extension context, calls the spec factory, and returns the IndexBackend.

**Call relations**: Startup or workspace setup calls this to wire indexing. If no extension registers the selected backend, it raises NotRegisteredError so bad configuration fails immediately.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 797–817)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and constructs the deploy’s embedding client. An embedding client turns text into numeric vectors used by search and memory features.

**Data flow**: It receives manifests, a configured backend name or None, and credential storage. It defaults the name when absent, finds the matching embed spec, checks credential availability, builds an extension context, calls the factory, and returns the EmbedClient.

**Call relations**: Boot wiring calls this before passing the embed client into indexing, memory, tools, and jobs. A missing selected backend raises NotRegisteredError rather than failing later.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 820–845)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider from extension declarations. Memory search is the feature that lets UFO retrieve relevant stored memories.

**Data flow**: It receives manifests, credential storage, optional index and embed clients, and a provider name. It gathers matching providers, returns None if none exist, raises if more than one extension registers the same name, checks credentials, builds a context, constructs the provider, and wraps it in MemorySearch.

**Call relations**: Runtime memory features call this to obtain the selected provider. The duplicate check ensures one provider name cannot mean two different implementations.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 848–896)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Checks extension tool, object kind, and bound-action declarations at deploy boot without building per-workspace contexts. Its job is to catch collisions and missing credential setup before any user turn starts.

**Data flow**: It receives manifests and optional credential storage, combines built-in tools and actions with extension-declared tools and actions, verifies credential requirements, builds a context-free object registry including core object kinds, validates action registration, creates object-verb tools, checks the final ToolRegistry, and returns the validated action registry.

**Call relations**: Deployment startup can call this as a health gate. It uses core_object_kinds and the same registry builders as turn_tools, but with no workspace-specific context.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, __init__, action_registry, object_registry).


##### `turn_workspace_facts`  (lines 899–939)

```
async def turn_workspace_facts(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, *, audience: Audience) -> tuple[str, ...]
```

**Purpose**: Reads short factual lines that extensions say are true for the current workspace. These lines can decorate a prompt without making one failing extension break the whole turn.

**Data flow**: It receives manifests, credential storage, and an audience. For each declared workspace fact, it builds the extension’s context, calls the fact’s holds method, appends the fact line when true, and logs a warning while skipping that fact if reading fails.

**Call relations**: Turn preparation can call this to gather prompt facts. The function deliberately swallows individual fact-read errors so the member’s turn can continue.

*Call graph*: 2 external calls (warn, context_for).


##### `turn_hooks`  (lines 942–991)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain for turn lifecycle events. Hooks are extension callbacks that react to moments during a turn, such as before or after processing.

**Data flow**: It receives manifests, credential storage, optional index/embed/tailer services, audience, and a public base URL. It groups declared hooks by supported turn event, builds each extension’s context, binds hook specs to that context, and returns a HookChain.

**Call relations**: The turn loop uses the returned HookChain when events occur. Connection-recorded hooks are excluded here because connection_hooks builds the separate connection-time chain.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 1006–1017)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Runs all connection_recorded hooks after a connection has landed. These hooks are observe-only: a failure is logged and swallowed so the recorded connection itself remains valid.

**Data flow**: It receives a ConnectionRecorded payload. For each bound hook, it calls the hook handler with a HookContext containing the extension context and connection payload, enforces a timeout, and logs any exception or timeout without returning a value.

**Call relations**: The connect flow calls this on the ConnectionHookChain built by connection_hooks. It hands each extension the same kind of scoped context used by jobs, so retry jobs can recreate work if a hook fails.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 1020–1046)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the hook chain that reacts when a new connection is recorded. This lets extensions create follow-up resources, such as feeds for a newly connected account.

**Data flow**: It receives manifests, credential storage, and optional index/embed services. It selects only hooks whose event is connection_recorded, checks credential storage is available, builds each declaring extension’s context, binds the hooks, and returns a ConnectionHookChain.

**Call relations**: The connect request path uses the returned chain and later calls ConnectionHookChain.fire. This is separate from turn_hooks because it runs during connection handling, not during a model turn.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### Content and communication manifests
These manifests declare user-facing extensions for debugging, document work, knowledge access, and messaging surfaces.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

Think of this file as the debugger extension’s sign-in sheet and menu card. When the larger UFO system discovers the extension, it needs a simple answer to three questions: what is this extension called, what version is it, and what features does it provide? This file answers those questions by building a Manifest, which is the standard package of information the host expects from an extension.

The extension is named "debugger" and versioned as "0.1.0". It exposes a `report_problem` tool, imported here as `REPORT_PROBLEM_TOOL_DEF`, which lets a workspace problem be sent to operators. It also exposes a debugger surface, meaning a user-facing route or small web area, described by `SurfaceSpec`. That surface uses `ROUTES` for its pages or endpoints and is named with `SURFACE_DEBUG`.

A key safety detail is the `identify=resolve_operator_workspace` setting. In plain terms, the debugger surface is not just mounted everywhere blindly. The host must be able to identify the correct operator workspace before this surface is available. Without this file, the debugger extension would have no official manifest, so the host would not know to install its problem-reporting tool or its debug surface.

#### Function details

##### `manifest`  (lines 16–24)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the host system’s structured description of the debugger extension. Someone would use this when loading extensions so the host can discover the extension’s name, version, tools, and surfaces.

**Data flow**: It starts with the fixed constants `NAME` and `VERSION`, plus imported definitions for the reporting tool, debugger routes, debugger surface name, and workspace identifier. It packages those into a `SurfaceSpec` for the debugger surface, then puts that surface and the reporting tool into a `Manifest`. The result is a complete manifest object that the host can read to enable this extension.

**Call relations**: During extension loading, the host calls `manifest` to ask this file what the debugger extension contributes. Inside, it creates a `SurfaceSpec` to describe the debugger web surface, then creates a `Manifest` to bundle that surface together with the reporting tool and extension metadata.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup`

This file is the registration card for the documents extension. It does not create documents itself. Instead, it lists the pieces this extension contributes so the larger UFO system can find them and load them when needed.

The main contribution is a group of skills stored in the local `skills/` folder. A skill is a packaged workflow with instructions and supporting files. Here, the skills cover things like Word documents, PowerPoint presentations, Excel spreadsheets, PDFs, document review, visual themes, shared design foundations, and prose drafting. Some of these skills depend on shared style foundations, so a document can still follow a consistent house style even when the user gives little design direction.

The file also registers a `writing` subagent profile, imported as `WRITING_PROFILE`. A subagent is a smaller, focused worker the main agent can call on for a specific job. In this case, it is meant for drafting and editing prose.

The `manifest()` function packages all of this into a `Manifest` object: the extension name, version, subagent, and skill paths. Think of it like a table of contents for a toolbox. The tools live elsewhere, but this file tells the system what tools exist and where to look for them.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the system-readable summary of what the documents extension provides. Someone would use it when UFO is discovering extensions and needs to know this pack's name, version, skills, and subagents.

**Data flow**: It starts with constants in this file: the extension name, version, the folder containing skills, and the list of skill folder names. For each skill name, it creates a `SkillSpec`, which points to that skill's folder. It then puts those skill specifications together with the writing subagent profile into a `Manifest` object and returns that object to the caller.

**Call relations**: During extension loading, the wider system calls `manifest` to ask, 'What do you contribute?' This function answers by creating `SkillSpec` entries for each document skill and passing them into `Manifest.__init__`, along with the imported writing profile, so the loader can later make those skills and the subagent available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/gbrain/ufo_ext_gbrain/manifest.py`

`config` · `startup`

This file is like the extension’s business card. When the main system loads the gbrain extension, it asks this file: “What do you provide?” The answer is a manifest, which is a structured declaration of the extension’s name, version, supported object type, content sources, and credentials.

The gbrain extension can turn Markdown files into pages from two places. One source backend reads from a GitHub repository. The other reads from a local folder, useful when serving or testing content from disk. The file also declares a credential slot named for a GitHub token. That token is only needed when the repository is private; public repositories can be read without it.

The most important idea is that this file does not itself read files or talk to GitHub. Instead, it wires the extension into the host system by saying which source classes should be built when a source of each kind is requested. Without this file, the rest of the system would not know that gbrain sources exist, how to create them, or what credential to ask for when private GitHub access is needed.

#### Function details

##### `manifest`  (lines 16–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the declaration that describes the gbrain extension to the UFO system. Someone uses this so the host application can discover the extension’s object type, source backends, and optional GitHub credential.

**Data flow**: It starts from fixed module-level values such as the extension name, version, Git backend name, folder backend name, object kind, and GitHub token slot. It packages these into a Manifest object, including two SourceProvider entries: one that builds a GitHub-based gbrain source using supplied credentials, and one that builds a folder-based gbrain source without credentials. The result is a complete Manifest returned to the caller; it does not change files, network state, or global data.

**Call relations**: During extension loading, the host system calls this function to learn what the extension offers. Inside, it creates SourceProvider objects so the sync system later knows how to make either a GitHub source or a folder source, creates a CredentialSlot so the system knows about the optional GitHub token, and wraps everything in a Manifest for the host to register.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/imessage/ufo_ext_imessage/manifest.py`

`config` · `startup / extension discovery`

Think of this file as the extension’s front-desk registration form. When the larger UFO system loads extensions, it needs to know what this iMessage add-on is called, what version it is, what actions it offers, and how messages should flow in and out. Without this file, the system would not know that an iMessage surface exists, how to listen for iMessages, how to post replies, or how a workspace member can connect their phone.

The file imports the pieces that do the actual work: the iMessage surface, the connect tool, and the cloud provider setup. Its single job is to assemble those pieces into a `Manifest`, which is a structured description the host system can read.

The manifest exposes one tool: “Connect iMessage.” This tool is marked as side-effecting because it changes real-world state by starting or completing a phone connection. It is also marked untrusted, meaning the system should treat requests carefully rather than assuming they are harmless. The manifest also declares one surface, named for iMessage, and gives the host the functions to call when it needs to listen, post, attach files, or speak through that surface. Finally, it lists two required deployment keys, so the runtime knows which environment secrets are needed to talk to the Spectrum provider.

#### Function details

##### `manifest`  (lines 21–55)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of the iMessage extension for the host system. Someone uses this when the system needs to discover what this extension can do and what settings it requires.

**Data flow**: It starts with no caller-provided input. It creates an iMessage surface and an iMessage connection tool, both using the Spectrum project provider. It then packages their names, callbacks, input model, display label, safety flags, and required deployment secrets into a `Manifest` object, which is returned to the host system.

**Call relations**: When the extension is loaded, this function is the place where the separate parts are joined together. It creates `ImessageSurface` so the system can receive and send iMessage traffic, creates `ImessageConnect` so members can prove control of a phone number, wraps that tool with `ToolDef`, describes its object binding with `ObjectBinding`, gives it a user-facing label through `ActionPresentation`, describes the surface through `SurfaceSpec`, and finally hands all of that to `Manifest` as the extension’s complete registration record.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Automation and creation manifests
These manifests register recurring work, scheduled tasks, self-improvement jobs, site creation features, and user-created skills.

### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `extension startup and scheduled job dispatch`

A monitor is like a standing watch: it repeatedly runs a shell probe in a conversation’s sandbox at a set interval, and tracks whether the results are changing or failing over time. This file does not contain the probing logic itself. Instead, it declares the pieces that make that feature visible to the wider system.

When the extension is loaded, `manifest()` builds a `Manifest`, which is a package label for the system: it gives the extension a name and version, registers the monitor tool that users or agents can call, registers the monitor object kind that can be stored durably, and registers a scheduled job.

That scheduled job is named `monitor_runner` and is set to run every minute. The schedule string is a cron-like pattern, meaning a compact clock rule. The job does not blindly scan every workspace. Its `candidates` value comes from `due_monitor_workspaces()`, which points the job dispatcher only toward workspaces that actually have monitors ready to check. That keeps idle workspaces cheap.

When the clock fires, the job calls `_probe()`. `_probe()` creates a `MonitorRunner` with the current extension context and asks it to run. In other words, this file is the wiring diagram; the runner is the worker that does the real inspection.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job’s small entry function. It starts the monitor runner so any monitors that are due can be probed.

**Data flow**: It receives an `ExtensionContext`, which is the system-provided bundle of services and current workspace information for an extension. It uses that context to create a `MonitorRunner`, then calls its `run()` method. Nothing is returned; the effect is that due monitor checks are performed by the runner.

**Call relations**: The job declared in `manifest()` uses `_probe` as its handler. When the scheduler decides a workspace has due monitor work, it calls `_probe`, and `_probe` immediately hands control to `MonitorRunner` because that class contains the actual monitor-checking behavior.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the monitors extension to the UFO system. It says what the extension is called, what tool and object type it contributes, and what recurring job should be scheduled.

**Data flow**: It reads the constants in this file, uses `due_monitor_workspaces()` to describe which workspaces should be considered for monitor runs, wraps the scheduled runner in a `JobSpec`, and returns a complete `Manifest`. The returned manifest is the system’s map for installing and activating this extension.

**Call relations**: The extension loader calls `manifest()` when it is discovering available extensions. Inside, it builds a `JobSpec` for the minute-by-minute monitor runner, calls `due_monitor_workspaces()` so the scheduler can skip workspaces with no due monitors, and places everything into a `Manifest` that the rest of the system can consume.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension load and recurring scheduled jobs`

This file is the doorway through which the scheduled-tasks extension introduces itself to the larger system. Without it, the system would not know that this extension can pause conversations until later, run scheduled tasks, or load the task-scheduling instructions used by the agent.

The file defines a manifest, which is like a checklist handed to the host at startup. The checklist says: this extension is named "scheduled_tasks"; it provides a pause-and-wait tool; it defines a scheduled-task object; it needs the memory search extension; and it has one skill folder called "task-scheduling".

It also registers two clock-based background jobs. One job looks for scheduled tasks that are due. The other looks for paused conversations that are ready to resume. They use the same once-per-minute schedule, but they are separate jobs on purpose. If one kind of work gets stuck, it should not block the other. Each job also asks only for workspaces that actually have due work, so the dispatcher does not waste time visiting empty workspaces.

The two small async functions, `_run` and `_resume`, are the job entry points. They create the right runner object and tell it to do its work.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job function used when the system wants to process scheduled tasks that are due. It starts a `ScheduledTaskRunner`, which is the part that does the actual task-running work.

**Data flow**: It receives an `ExtensionContext`, which is the host-provided bundle of services and workspace information the extension needs. It gives that context to `ScheduledTaskRunner`, then waits for the runner to finish. It returns nothing directly; the useful effect is that due scheduled tasks may be found and invoked.

**Call relations**: The manifest registers `_run` as the handler for the scheduled-task runner job. When the clock fires for that job, the host calls `_run`; `_run` then creates `ScheduledTaskRunner` and hands control to it.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job function used when the system wants to resume conversations that were paused until a later time. It starts a `PauseRunner`, which performs the resume work.

**Data flow**: It receives an `ExtensionContext` from the host. It passes that context into `PauseRunner`, then waits while the runner processes any pauses that are due. It returns nothing directly; its effect is to wake up conversations that should continue now.

**Call relations**: The manifest registers `_resume` as the handler for the pause-runner job. When that recurring job fires, the host calls `_resume`; `_resume` creates `PauseRunner` and lets it carry out the resume process.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension’s manifest: the complete description of what this extension contributes to the system. The host uses it to wire the extension into tools, background jobs, skills, and conversation storage.

**Data flow**: It starts from constants in this file, such as the extension name, version, schedule, and skill folder. It also asks `due_task_workspaces()` and `due_pause_workspaces()` for candidate workspace selectors, so each background job only runs where there is due work. It packages all of that into `JobSpec`, `SkillSpec`, and finally a `Manifest`, which is returned to the host.

**Call relations**: The host calls `manifest` while loading the extension. Inside, it creates two `JobSpec` entries: one points to `_run` for due scheduled tasks, and one points to `_resume` for due pauses. It also creates `SkillSpec` entries for the skill folders and includes the tool, object type, dependency, and conversation slot the rest of the extension needs.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `extension load and scheduled evaluation`

This file is the extension’s front door. When the larger system loads extensions, it asks this file for a manifest, which is like a small registration card: the extension’s name, version, and the timed job it wants the scheduler to run. Here, the job is called an evaluation cron. A cron is a clock-based scheduled task, meaning it runs because time passed, not because a user made a request or some data changed.

The scheduled job points to `_tick`. When `_tick` runs, it first checks that a model is available. In plain terms, the self-improvement process needs access to an AI model; without it, it cannot propose changes, replay old conversations, or judge whether a candidate is better. If model access is missing, it stops with a clear error instead of failing later in a confusing way.

If the model is present, `_tick` wraps it in `ModelAccessLeg`, then builds the three main pieces of the improvement loop: a proposer that suggests prompt candidates, an evaluator that replays and judges them, and an `ImproveCron` object that runs the full cycle. The manifest marks the job as needing the deploy model, meaning it should use the main production-style model rather than a cheaper background model, because replaying archived transcripts may require the same context capacity used in deployment.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the function the scheduler runs when the self-improvement job fires. It performs one full self-improvement evaluation pass, but only if AI model access has been wired into the extension context.

**Data flow**: It receives an `ExtensionContext`, which contains shared services for the extension, including optional model access. It checks whether `ctx.model` exists; if not, it raises an error explaining that self-improvement cannot run. If model access is present, it wraps that model in `ModelAccessLeg`, gives the wrapper to a `PromptProposer` and to `CandidateEvaluation`, then creates an `ImproveCron` with those parts and awaits its run. The result is not returned as a value; the effect is that the improvement cycle is carried out.

**Call relations**: The scheduler reaches this function through the job declared by `manifest`. Inside the tick, it constructs `ModelAccessLeg` so all model calls go through the intended access layer. It then builds `PromptProposer` for suggesting candidate changes and `CandidateEvaluation` for replaying and judging them, hands both to `ImproveCron`, and lets `ImproveCron` drive the actual workflow.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension declaration that the host system reads during startup. It says the extension is named `self_improvement`, gives its version, and registers one scheduled evaluation job.

**Data flow**: It takes no inputs. It creates a `JobSpec` describing the scheduled job: its name, clock schedule, handler function, eligible workspaces from `trajectory_workspaces()`, and the fact that it needs the deploy model. It then wraps that job inside a `Manifest` and returns it to the host system.

**Call relations**: The extension loader calls this function when discovering the extension. The function calls `trajectory_workspaces()` to say which workspaces the job can run against, creates a `JobSpec` that points at `_tick`, and returns a `Manifest` so the scheduler knows what to run and when.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup / extension load`

Think of this file as the extension’s packing list and instruction label. The code here does not build websites itself. Instead, it describes all the pieces the main system should plug in when the “sites” extension is loaded.

It names the extension, reads a prompt section from disk, points to the website-building skill folder, and gathers together many imported building blocks. These include tools an agent can use to create and edit hosted sites, delegation tools that let a main agent hand work to a website-building subagent, and an object kind that lets chat recognize a hosted site as something users can interact with.

It also registers a “surface,” meaning a user-facing area where a hosted site can be shown, like putting a finished page into a frame. The manifest adds prompt text so the main agent knows how to use these abilities correctly.

A particularly important part is the hook setup. A hook is code that runs at a certain moment, here before tool use, to enforce rules such as using the right creation route, staying in the correct builder phase, limiting repair reads, and requiring quality checks before deployment. Finally, it schedules a background job that releases a main agent’s held homepage once ownership should move on. Without this file, the system would not know that the sites extension exists or how to wire its many parts together.

#### Function details

##### `manifest`  (lines 64–123)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the complete declaration for the sites extension. The host system calls it to learn which tools, prompts, skills, hooks, surfaces, object types, conversation slots, and scheduled jobs this extension wants to add.

**Data flow**: It starts with constants and imported pieces: the extension name and version, prompt text read from the sites prompt file, tool definitions, subagent profiles, surface and object definitions, hook functions, and job settings. It packages those into manifest-related objects, including prompt, skill, hook, and job entries. The result is a single Manifest object that the larger system can load and use to activate the extension.

**Call relations**: During extension loading, the host asks this function for the sites extension’s manifest. Inside that assembly step, it creates the prompt section, skill entry, hook entries, and cleanup job specification, and it asks the main-homepage helper for the workspaces that are candidates for release. The finished manifest is then handed back to the host so the host can expose the website tools, register the subagents and surface, enforce the pre-tool rules, and schedule the background cleanup work.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, unreleased_main_homepage_workspaces).


### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`orchestration` · `extension startup, object requests, tool calls, runtime skill loading, scheduled indexing`

A “skill” here is a small bundle of text files, led by a required SKILL.md file, that teaches an agent how to do something. This file makes those member-authored skills behave like first-class workspace objects: they can be created, inspected, updated, deleted, searched, and loaded in later conversations. Without this file, saved workspace skills would not be exposed to the object system, would not appear in member views, would not be searchable, and would not be made available as runtime skills.

The file has three main jobs. First, it defines the shape of a saved skill: files may be supplied directly as text, copied from a workspace path, or kept unchanged by referring to their stored SHA-256 digest, which is a fingerprint of the file contents. Second, SkillObjects connects those skill specs to persistent storage through UserSkillStore, while enforcing safety rules: paths must stay inside the skill folder, files must be UTF-8 text, there are limits on file count and total size, and edits must use the expected generation so two writers do not silently overwrite each other. Third, it registers search and indexing. Search ranks skill descriptions by keyword, while the scheduled indexing job embeds stale skill cards so they can be found through the system’s index.

At the bottom, manifest() announces all of this to the host application: the object kind, the search tool, the built-in authoring skill, runtime skill hooks, and the background indexing job.

#### Function details

##### `_require_ext`  (lines 119–122)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the code has an ExtensionContext, which is the workspace-aware handle it needs to read storage, run transactions, and access services. If the context is missing, it stops immediately with a clear error instead of failing later in a confusing way.

**Data flow**: It receives either an ExtensionContext or nothing. If a real context comes in, it returns it unchanged; if None comes in, it raises an error saying the skill kind was called without its needed context.

**Call relations**: Most SkillObjects methods call this first because they all depend on workspace-specific services. It acts like checking that you have the right key before trying to open any of the storage doors.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 125–132)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This checks that every file path in a skill stays inside that skill’s own folder. It prevents a saved skill from naming files outside its allowed area, such as by using path tricks like ../.

**Data flow**: It receives the skill name and the proposed UserSkillSpec. It computes the skill’s allowed root folder, checks each file key against that root, and either returns nothing if all paths are safe or raises a ValueError for the first unsafe path.

**Call relations**: SkillObjects.apply calls this before saving. It relies on skill_root to know where the skill should live and contained_relative to prove each path remains inside that boundary.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_root).


##### `_text`  (lines 135–141)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This verifies that a skill file is readable text, specifically UTF-8 text. Skills are not allowed to bundle arbitrary binary files.

**Data flow**: It receives a file path and that file’s raw bytes. It tries to decode the bytes as text; if decoding works, it returns the decoded string, and if not, it raises a ValueError explaining that the file is not text.

**Call relations**: SkillObjects._resolve calls this after it has gathered each file’s bytes, whether those bytes came from inline text, existing storage, or a workspace file reference. It is the final text-only check before saving.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of saved workspace skills for object-listing requests inside a tool context. It shows compact rows rather than full file contents.

**Data flow**: It receives a ToolContext and a listing query. It checks for the extension context, asks _rows for all saved skill rows, applies the requested paging and filtering through object_page, and returns an ObjectPage.

**Call relations**: The object system calls this when someone lists objects of kind skill. It delegates context validation to _require_ext and row construction to SkillObjects._rows.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 151–162)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the same saved-skill listing for the member portal, outside a live agent turn. Skills belong to the workspace as a whole, so the member_id and admin flag do not narrow the set here.

**Data flow**: It receives an optional extension context, member identity information, an admin flag, and a listing query. It validates the context, builds the workspace’s skill rows, turns them into a page, and returns that page.

**Call relations**: The member-facing object view calls this when a signed-in member browses saved skills. Like SkillObjects.list, it uses _rows and object_page, but it is shaped for portal access rather than tool access.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 164–165)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches one saved skill’s detail for object_get-style access. It returns file fingerprints and sizes, not the actual file bodies, so large or sensitive content is not echoed into ordinary object detail.

**Data flow**: It receives a ToolContext and a skill name. It validates the extension context, asks _skill for the stored detail, and returns either that ObjectDetail or None if the skill does not exist.

**Call relations**: The object system calls this when a caller asks for one skill by name. It is a thin public wrapper around SkillObjects._skill.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 167–185)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This builds the member-portal view of one saved skill, combining the row summary with the detailed spec. Like get, it exposes digests and sizes instead of raw file contents.

**Data flow**: It receives an optional extension context, a skill name, member information, and an admin flag. It validates the context, looks for the matching row, fetches the detail, and returns a MemberObject containing both; if either the row or detail is missing, it returns None.

**Call relations**: The member portal calls this when a user opens a specific saved skill. It uses _rows to get the display row, _skill to get the stored detail, and wraps both in MemberObject.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 187–195)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This converts stored skills into small display rows. Each row includes the skill name, a shortened description, and whether it is pinned.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore for the workspace’s listing, trims each description to the summary limit, builds ObjectRow values, and returns them as a tuple.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail all use this helper whenever they need the lightweight list view of saved skills.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 197–212)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the detailed saved-skill object without exposing the raw file contents. It replaces each stored file body with a FileRef containing a SHA-256 digest and byte size.

**Data flow**: It receives an ExtensionContext and a skill name. It asks UserSkillStore for the stored record; if none exists, it returns None. Otherwise it hashes each file’s bytes, builds a UserSkillSpec with FileRef entries, and returns an ObjectDetail with timestamps and generation information.

**Call relations**: SkillObjects.get and SkillObjects.member_detail call this when they need the safe detailed view of one skill. The returned digests can later be passed back to apply to keep files unchanged.

*Call graph*: called by 2 (get, member_detail); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 214–231)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This gives a quick factual status summary for one saved skill, such as its description, file count, total byte size, and pinned state. It also protects readers from using stale data by checking the expected generation.

**Data flow**: It receives a ToolContext, a skill name, and an expected generation. It loads the stored record; if missing, it returns None. If the generation does not match, it raises an error; otherwise it returns a small dictionary of status fields.

**Call relations**: The object system can call this after reading or applying an object to confirm what exists. It uses _require_ext and UserSkillStore directly rather than going through the full detail-building path.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects.apply`  (lines 233–256)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This creates or updates a workspace skill from a submitted spec. It enforces limits, resolves file references into actual bytes, and saves the final bundle only if it is valid.

**Data flow**: It receives a ToolContext, skill name, new spec, optional old spec, and expected generation. It checks the extension context, rejects too many files, verifies paths stay inside the skill, resolves inline/from/digest file values into bytes, checks total size, and saves the skill through UserSkillStore with the pinned flag and generation guard.

**Call relations**: The manifest object system calls this when a user applies a skill object. It calls _contained_keys for path safety, _resolve to gather file contents, and UserSkillStore.save to persist the result.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 258–269)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved workspace skill, while preventing accidental deletion if someone else changed it first. The generation check works like asking, “Am I deleting the same version I looked at?”

**Data flow**: It receives a ToolContext, skill name, and expected generation. It loads the current record; if the skill exists but its generation differs, it raises an error. Otherwise it asks UserSkillStore to delete the skill.

**Call relations**: The object system calls this for delete requests. It uses _require_ext for workspace access and UserSkillStore for both the pre-delete check and the actual deletion.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 271–319)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns the three allowed file value forms into actual file bytes ready to save: inline text, a copied workspace file, or an unchanged stored file referred to by digest. It is the bridge between a convenient manifest format and the final stored bundle.

**Data flow**: It receives a ToolContext, skill name, and UserSkillSpec. It loads existing stored files so FileRef entries can be verified by SHA-256 digest, reads any FileFrom paths from the workspace through the sandbox, decodes the sandbox’s base64 output back into bytes, converts inline strings to bytes, checks every file is UTF-8 text, and returns a dictionary from skill-relative path to bytes.

**Call relations**: SkillObjects.apply calls this before saving. It uses UserSkillStore for existing file content, the sandbox to safely read workspace files, _text for text validation, and digest checks so a caller cannot claim to keep a file that does not match storage.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `skill_search`  (lines 371–388)

```
async def skill_search(ctx: ToolContext, args: SkillSearchInput) -> ToolResult
```

**Purpose**: This searches all loadable skills, both built-in and workspace-saved, by matching the user’s keywords against each skill’s routing card. It returns only short name-and-description lines, not full skill bodies.

**Data flow**: It receives a ToolContext and SkillSearchInput containing a query and limit. It gets all skill cards from the context, scores each one with lexical_score, sorts by best score, keeps positive matches up to the limit, and returns a ToolResult with either matching lines or a no-match message showing how many skills were searched.

**Call relations**: SKILL_SEARCH_TOOL uses this as its handler when a caller invokes the skill_search action. It hands back skill names that can then be used with the normal skill-loading flow.

*Call graph*: 3 external calls (__init__, __init__, lexical_score).


##### `_member_cards`  (lines 405–406)

```
async def _member_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]
```

**Purpose**: This returns the routing cards for member-authored workspace skills. A routing card is the small name-and-description record used to decide which skills might be useful.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore for the workspace’s saved skill cards and returns them as a tuple.

**Call relations**: manifest() passes this function into MemberSkillsSpec so the runtime can include workspace skills when building the set of available skill cards.

*Call graph*: 1 external calls (__init__).


##### `_materialize_skill`  (lines 409–410)

```
async def _materialize_skill(ctx: ExtensionContext, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one saved workspace skill into its runtime form. “Runtime” means the form the agent can actually use during a turn.

**Data flow**: It receives an ExtensionContext and a skill name. It asks UserSkillStore to materialize that skill and returns either the RuntimeSkill or None if it cannot be found.

**Call relations**: manifest() registers this with MemberSkillsSpec. The runtime calls it when it needs to load a specific member-authored skill by name.

*Call graph*: 1 external calls (__init__).


##### `_materialize_all_skills`  (lines 413–414)

```
async def _materialize_all_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads all saved workspace skills into runtime form. It is used when the agent needs the whole workspace skill set rather than one named skill.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore to materialize every saved skill for that workspace and returns them as a tuple.

**Call relations**: manifest() gives this to MemberSkillsSpec so the runtime can load the complete member skill collection when an agent is configured to use workspace skills.

*Call graph*: 1 external calls (__init__).


##### `index_skills`  (lines 417–466)

```
async def index_skills(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job keeps saved skill descriptions searchable in the system’s index. It finds skill cards whose stored digest differs from their indexed digest, embeds them, and marks them indexed only if the same version is still current.

**Data flow**: It receives an ExtensionContext. It checks that both the index backend and embedding client are available, queries the database for stale skill cards in the workspace, then tries to index each one. It counts successful settlements, logs per-skill failures, and if every attempted row failed it raises the last failure so the job does not look falsely healthy.

**Call relations**: manifest() registers this as the skill_index background job. For each stale row it calls _index_card, using a TextChunker plus the index and embedding services to refresh search data.

*Call graph*: calls 2 internal fn (transaction, _index_card); 3 external calls (__init__, or_, select).


##### `_index_card`  (lines 469–506)

```
async def _index_card(ctx: ExtensionContext, index: IndexBackend, embed: EmbedClient, chunker: TextChunker, row: sa.Row) -> None
```

**Purpose**: This indexes one skill’s searchable card and then carefully records that the current version was indexed. It avoids marking a skill as indexed if it changed while indexing was happening.

**Data flow**: It receives the extension context, index backend, embedding client, text chunker, and one database row. It sends a short text made from the skill name and description to chunk_embed_upsert, then tries to update the database row’s indexed_digest only if the digest still matches. If the row disappeared during indexing, it deletes that skill’s index scope to clean up.

**Call relations**: index_skills calls this for each stale skill row. It hands text to the indexing pipeline and uses database transactions to decide whether the index result still matches the latest saved skill.

*Call graph*: calls 2 internal fn (transaction, delete); called by 1 (index_skills); 4 external calls (__init__, select, update, chunk_embed_upsert).


##### `_skills_awaiting_index`  (lines 509–519)

```
def _skills_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with saved skills needing indexing. It looks for skills that have never been indexed or whose indexed digest no longer matches their current digest.

**Data flow**: It takes no runtime input. It returns a SQLAlchemy Select object that selects distinct workspace IDs from the user_skill table where indexing is missing or stale.

**Call relations**: manifest() passes this query builder to owner_candidates for the scheduled indexing job. That lets the job runner know which workspaces should receive an index_skills run.

*Call graph*: 2 external calls (or_, select).


##### `manifest`  (lines 522–542)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s registration function. It tells the host application what this extension is called, which tools and object kinds it provides, which skills it ships, how to load member skills, and which background job to run.

**Data flow**: It takes no input. It constructs and returns a Manifest containing the extension name and version, the skill_search tool, the skill object kind, the built-in create-skill authoring skill, member-skill loading callbacks, and the scheduled skill_index job with its workspace candidate query.

**Call relations**: The host application calls this when loading the extension. Everything else in the file becomes reachable through the Manifest it returns.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, owner_candidates).


### Platform integration manifests
These manifests connect UFO to external platforms, source providers, the shell client, and the web portal.

### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup and workspace connection checks`

Think of this file as the Slack extension’s registration form. The core app does not automatically know how to receive Slack messages, respond to Slack buttons, or finish Slack OAuth setup. This manifest explains all of that in one place.

It names the extension, lists the private Slack secrets a workspace may need, and declares a Slack “surface,” meaning a place where users can talk to the agent. That surface has routes for incoming Slack events, interactive actions like buttons, and the OAuth callback used when installing the app. It also points the core system to Slack-specific functions for posting messages, attaching context, speaking back to users, and identifying which workspace a request belongs to.

The file also wires in Slack tools and hooks. Hooks are callbacks the core system runs at important moments, such as before a tool is used or when a user prompt starts. Here they are used to attribute connector sends, follow Slack thread progress during a turn, and settle setup buttons after a connection is recorded.

A key detail is the workspace fact. It only says “Slack is installed” when there is both an installation record and Slack can actually reach this deployment. That avoids telling the agent Slack is ready while setup is only half-finished.

#### Function details

##### `_slack_answers`  (lines 73–79)

```
async def _slack_answers(ext: ExtensionContext) -> bool
```

**Purpose**: This checks whether Slack is not only registered for a workspace, but actually working. It prevents the system from treating a half-finished Slack install as live.

**Data flow**: It receives an extension context, which gives access to workspace installation records. First it looks for a Slack installation record. If none exists, it returns false. If one does exist, it asks `install_is_live` to confirm that Slack can really reach this deployment, then returns that answer.

**Call relations**: The workspace fact declared by `manifest` uses this function when the core system wants to know whether it should state that Slack is installed for a workspace. Inside that check, it hands off to `ufo_ext_slack.surface.install_is_live(ext)` for the final live-connection test.

*Call graph*: 1 external calls (install_is_live).


##### `manifest`  (lines 82–127)

```
def manifest() -> Manifest
```

**Purpose**: This builds and returns the Slack extension manifest, which is the core system’s map for using Slack. It declares what Slack needs, where Slack requests arrive, what tools and hooks are available, and how setup is represented.

**Data flow**: It takes no input. It gathers constants and imported Slack functions, wraps them into manifest objects such as credential slots, surface routes, hook specs, a setup skill, and a workspace fact, then returns one complete `Manifest` object. It does not run Slack itself; it describes how the rest of the system should connect to Slack.

**Call relations**: The core extension loader calls this when registering the Slack extension. During construction it creates `CredentialSlot`, `SurfaceRoute`, `SurfaceSpec`, `HookSpec`, `SkillSpec`, `WorkspaceFact`, and finally `Manifest` objects so the core system can later route Slack web requests, call Slack send functions, run Slack hooks, expose Slack tools, and check `_slack_answers` when deciding whether Slack is live for a workspace.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension loading`

Think of this file as the extension’s registration form. The rest of the system does not automatically know that this extension can sync pages from connected services, react to page changes, or retry unfinished setup work. This file spells that out in one place.

It declares the extension name and version, then builds a Manifest, which is the object the host reads when loading the extension. The manifest says there are object kinds for sources, pages, and source triggers. It also registers two event hooks: one that reacts when a page changes, and one that reacts when a connection has just been recorded. That second hook helps a newly connected account start syncing its usual streams without the user needing another manual step.

The file also defines a scheduled retry job, so if source creation did not complete cleanly the first time, the system can try again later for the relevant workspaces. For each connector in the connector registry, it creates a source provider backend. In plain terms, each registered service gets a small factory that can produce the backend used by the sync runner. Finally, it declares credential slots for bring-your-own-key API keys and registers a direct authentication proxy that reads those credentials.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 41–42)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a stored connector class into a working source backend. The credentials argument is accepted because source factories share a common shape, but this factory does not use it directly.

**Data flow**: It starts with a ConnectorSourceFactory that already contains a connector class. When called, it creates a fresh connector object from that class, wraps it in a ConnectorBackend, and returns that backend for the sync system to use.

**Call relations**: The manifest creates one ConnectorSourceFactory for each registered connector and gives it to a SourceProvider. Later, when the host needs a backend for that provider, it calls this factory, which hands back a ConnectorBackend ready to run that connector.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 45–82)

```
def manifest() -> Manifest
```

**Purpose**: This builds the complete manifest for the sources extension. The host system uses it to discover everything this extension contributes: source types, page-related objects, event hooks, scheduled jobs, credential slots, and the direct auth proxy.

**Data flow**: It reads the module constants and the CONNECTORS registry. From those, it creates object declarations, hook declarations, one retry job, one source provider per connector, one credential slot per connector, and a direct authentication proxy specification. All of those pieces are bundled into and returned as a Manifest.

**Call relations**: The extension loader calls this function when the extension is being registered. Inside, it calls constructors for Manifest, HookSpec, JobSpec, SourceProvider, CredentialSlot, AuthProxySpec, and ConnectorSourceFactory, and asks connection_workspaces for the set of workspaces where the retry job should look for work. The finished Manifest is then what lets the wider system wire this extension into syncing, credential lookup, event handling, and scheduled retry behavior.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup`

This is the extension’s front-door registration file. When the larger system discovers the UFO extension, it needs a small, standard description of what the extension offers. This file provides that description as a manifest, which is like a shipping label: it says what the package is called, what version it is, and where its usable interface can be found.

The extension exposes one “surface,” meaning one named way for the core system to talk to it. That surface is wired to the UFO routes, which are the connection paths used by the terminal-style UFO client. It also points to `resolve_workspace`, the function used to identify which workspace a request belongs to.

A notable detail is what this manifest does not include. It does not declare credential storage or a user-facing configuration switch. The comment explains that access is checked using a bearer token secret from the environment, `UFO_TOKEN_SECRET`, rather than a stored workspace credential. In plain terms: if this extension is installed, its route is mounted, and the route itself is responsible for admitting and streaming the connection.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest object for the UFO extension. The host uses this to learn the extension’s name, version, and the surface it should mount.

**Data flow**: It starts with the module constants `NAME` and `VERSION`, plus the imported surface name, route list, and workspace-identifying function. It packages those into a `SurfaceSpec`, then places that surface specification inside a `Manifest`. The result is a complete description object that the extension loader can read.

**Call relations**: This function is called when the host is loading or inspecting the extension. Inside, it creates a `SurfaceSpec` to describe the UFO-facing route, then creates a `Manifest` to wrap that surface together with the extension name and version. The returned manifest is what lets the rest of the system mount the UFO surface correctly.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup and scheduled background work`

Think of this file as the web extension’s registration form. The core system does not guess what the web portal can do; this manifest states it clearly. It gives the extension a name and version, says that it connects to member accounts, and lists the tools the web surface is allowed to use.

It also describes the browser-facing surface itself: the portal has routes, a way to identify the current workspace, and it claims the main home page so the bare web host opens the portal. The file lists feature flags too. A feature flag is an on/off switch controlled outside the code, often per environment. These flags decide whether portal areas such as Code, Issues, Memory, Skills, or admin screens appear.

Finally, it registers two repeating background jobs. One gives untitled conversations readable names by summarizing their opening exchange. This matters across all surfaces, not just the web portal, because the conversation rail may show chats from Slack, CLI, or the portal together. The other seeds homepage content for agent workspaces that have not been seeded yet. Without this file, the web extension would not be mounted properly, its routes and flags would not be known, and its scheduled portal upkeep would not run.

#### Function details

##### `manifest`  (lines 64–90)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s complete manifest, which is the object the core system reads to know how to install and run this extension. Someone would use it during extension loading so the portal can announce its routes, tools, flags, and jobs in one place.

**Data flow**: It starts with constants and imported portal pieces: the extension name, version, routes, feature flags, workspace resolver, tool permissions, conversation slots, and job handlers. It packages those into a Manifest object. The result is a single description of the web extension, including its browser surface, scheduled jobs, visible feature switches, and permission to read member context.

**Call relations**: When the core system asks this extension what it provides, this function creates the answer. As part of that answer, it creates a web SurfaceSpec so the portal can be mounted, creates JobSpec entries so recurring work can be scheduled, and asks the jobs helper functions for the sets of workspaces that need title summaries or homepage seeding.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).


### Manifest schema and packages
The runtime manifest model defines the extension declaration contract, while package markers make selected extension modules importable.

### `core/src/ufo/runtime/ext/manifest.py`

`data_model` · `startup and cross-cutting extension loading`

This file is mostly a set of frozen data shapes. “Frozen” means that once an extension declares something, the runtime treats it as a fixed promise rather than a live object to mutate. The main idea is simple: an extension should not call into core systems to register itself piece by piece. Instead, it returns a Manifest, like handing over a completed application form. The runtime then reads that form at startup and wires the extension into the rest of the product.

The declarations cover many parts of the system. Some say what tools an agent may use. Some define background jobs, HTTP routes, credential slots, connector providers, sandbox backends, search providers, browser providers, feature flags, prompt text, onboarding steps, shipped agents, subagents, and skills. There is also a Pack declaration, which bundles a coherent set of extensions and pack-level additions.

A few small functions validate global rules that cannot be checked inside one declaration alone. For example, conversation slot IDs must be unique across all active extensions, and there can be only one open connector namespace because it is the catch-all owner for otherwise unknown connector names. Without this file, extensions would have no stable, shared vocabulary for telling the runtime what they provide, and boot-time checks would miss conflicts that would later turn into confusing runtime failures.

#### Function details

##### `PreToolUse.__post_init__`  (lines 427–429)

```
def __post_init__(self) -> None
```

**Purpose**: This fills in the semantic tool-call name for a “before tool runs” hook event when the caller did not provide one. It makes ordinary tools default to using their tool name as their identity.

**Data flow**: A PreToolUse object is created with a tool name, validated tool input, and possibly an empty call field. After creation, this method checks whether call is blank. If it is blank, the object is still frozen to normal code, but this initializer sets call to the tool name so later hook matching has a stable value to compare.

**Call relations**: This runs automatically when a PreToolUse event object is constructed. Later hook filtering can use the call value without having to remember the fallback rule itself.


##### `PostToolUse.__post_init__`  (lines 445–447)

```
def __post_init__(self) -> None
```

**Purpose**: This gives a successful “after tool ran” hook event a default semantic call name. If no special call identity was supplied, the tool’s own name becomes that identity.

**Data flow**: A PostToolUse object starts with the tool name, the tool input, the output text, and maybe no call value. This method checks the call field right after construction. If it is empty, it writes the tool name into call, so the finished event carries the same kind of identity as the pre-tool event.

**Call relations**: This is triggered by dataclass construction. It supports the hook system by making post-tool hook matching consistent with pre-tool hook matching.


##### `PostToolUseFailure.__post_init__`  (lines 465–467)

```
def __post_init__(self) -> None
```

**Purpose**: This gives a failed tool-call event a default call identity. It keeps failure hooks from needing special logic when a tool is identified simply by its name.

**Data flow**: A PostToolUseFailure object is created with the failed tool name, input, error output, and optionally a call field. The initializer checks whether call was left empty. If so, it stores the tool name there, leaving the event ready for later matching by hooks.

**Call relations**: This runs automatically after a PostToolUseFailure object is made. It lets the same hook selection idea work for successful and failed tool calls.


##### `HookSpec.__post_init__`  (lines 612–614)

```
def __post_init__(self) -> None
```

**Purpose**: This enforces a safety rule for best-effort hooks. Only hooks on user prompt submission may be marked best effort, because other hook types are part of stricter tool or lifecycle control.

**Data flow**: A HookSpec is created with an event name, a handler function, optional tool filters, and a best_effort flag. This method checks the flag and event. If best_effort is true for anything except user_prompt_submit, construction fails with a ValueError; otherwise the hook declaration is accepted unchanged.

**Call relations**: This validation happens when an extension declares a hook. It protects the later hook runner from ambiguous policy, so it can assume best-effort behavior only applies to user-prompt context injection.


##### `AgentProvision.__post_init__`  (lines 646–672)

```
def __post_init__(self) -> None
```

**Purpose**: This validates an agent that an extension wants to create for a workspace. It catches bad names, invalid icons, missing instructions, missing purpose text, and unsafe tool allowlists before the agent is installed.

**Data flow**: An AgentProvision comes in with a name, agent spec, optional tool allowlist, setup requirements, icon, and a flag saying whether it replaces the main agent. The method checks that the name looks like an object name, that the allowlist does not contain the generic object-action dispatcher, that the icon is one the portal can draw, and that the agent has both a prompt and a purpose. If the provision asks the agent to perform its own setup while using a restricted tool list, it also checks that the needed setup tools are included. On success, nothing is returned; on failure, construction raises a ValueError.

**Call relations**: This runs when extension manifests are built. It prevents invalid shipped agents from reaching the activation flow, where they would otherwise create broken workspace agent rows.


##### `SubagentProfile.__post_init__`  (lines 723–745)

```
def __post_init__(self) -> None
```

**Purpose**: This validates a subagent profile, especially its tool allowlist and its promise about concise handoff back to the parent agent. It prevents a profile from advertising one result style while using a different output schema.

**Data flow**: A SubagentProfile is created with a name, prompt, allowed tools, input and output data models, model settings, and behavior flags. The method first rejects the generic object-action dispatcher in the tool list, because subagents must be granted specific action IDs instead. It then inspects the output model fields to see whether it exactly matches the shared concise result contract. If the concise flag and the schema disagree, it raises a ValueError; otherwise the profile is accepted.

**Call relations**: This runs as each profile declaration is constructed. The subagent registry and spawn flow can then trust that concise parent handoff profiles really return the expected simple result shape.


##### `SubagentToolGrant.__post_init__`  (lines 764–769)

```
def __post_init__(self) -> None
```

**Purpose**: This validates a declaration that gives extra tools to someone else’s subagent profile. It blocks granting the generic object-action dispatcher, because grants must name exact actions or tools.

**Data flow**: A SubagentToolGrant is created with a target profile name and a tuple of tool names. The method checks whether the forbidden object-action dispatcher name appears in that tuple. If it does, construction fails with a ValueError; otherwise the grant remains unchanged.

**Call relations**: This runs when an extension declares cross-profile tool grants. It keeps later grant merging simple and safe, because the loader only has to union already-valid tool names into matching subagent profiles.


##### `conversation_slot_declarations`  (lines 867–896)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: This collects all conversation slot providers from active manifests and checks that they form one clean global namespace. A conversation slot is a typed piece of conversation-side data that extensions can display or summarize, so duplicate or malformed slot IDs would confuse the portal and runtime.

**Data flow**: The function receives a tuple of manifests. It walks through each manifest’s conversation slot providers, checking each provider’s ID format, label length and non-blank text, icon choice, required callback functions, and supported payload type. It also records which extension owns each ID, so it can reject duplicates. If every provider is valid, it returns a tuple pairing each manifest with each provider; if not, it raises RuntimeError with a clear startup failure.

**Call relations**: This is used during extension loading, when the runtime has all active manifests together. It turns scattered extension declarations into one validated list that downstream conversation-slot code can rely on.


##### `open_connector_namespace`  (lines 899–911)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This finds the single catch-all connector namespace declared by active extensions, if there is one. It fails if more than one extension claims that role, because the system would not know who owns an unknown connector slug.

**Data flow**: The function receives all active manifests and scans their connector_resolver field. If none has one, it returns None. If exactly one has one, it returns that resolver. If a second resolver appears, it raises RuntimeError instead of letting later connector lookup behave unpredictably.

**Call relations**: This runs when the runtime is assembling connector support from manifests. The connect flow and connector registry can then ask one agreed resolver about unregistered connector names, or know that no open namespace exists.


##### `declared_slots`  (lines 934–948)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: This turns extension credential declarations into the simpler slot records used by shared credential views and object projections. In plain terms, it makes one combined list of all secrets the active extensions say they may need.

**Data flow**: The function receives all active manifests. For every credential slot in every manifest, it creates a DeclaredSlot with the slot name, description, owning extension name, whether a member may fill it, any injection host, and any merge function. It returns all of those DeclaredSlot objects as a tuple, without changing the manifests.

**Call relations**: This is a shared assembly point for credential-related features. It calls DeclaredSlot.__init__ to produce the normalized records that other parts of the system, such as credential object handling and the credentials panel, can read without understanding the full Manifest structure.

*Call graph*: 1 external calls (__init__).


### `extensions/gbrain/ufo_ext_gbrain/__init__.py`

`other` · `import time`

In Python, a folder usually needs an `__init__.py` file to be treated as an importable package. This file is that marker for the `ufo_ext_gbrain` extension package. It is empty, so it does not run setup code, expose helper functions, or define package-level shortcuts. Its value is structural: without it, some Python tooling or import styles might not recognize this directory as a package, especially in older or stricter environments. Think of it like a label on a drawer: the label does not contain the contents, but it tells the system that the drawer is meant to be opened as part of the project.


### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `package import and extension discovery`

This file contains no code, but it still has a useful job. In Python projects, an `__init__.py` file tells Python that a folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may hold other useful files, and the label lets the system find them by name. Without this file, some Python environments or packaging tools might not recognize `extensions/repl/ufo_ext_repl` as a proper package, which could stop the REPL extension from being discovered or imported correctly. Because it is empty, it does not run setup code, define shortcuts, or change behavior at import time. Its purpose is simply to make the package structure explicit and reliable.


### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Without this file, depending on the Python version and packaging setup, other code might not reliably import modules from `extensions/sources/ufo_ext_sources` using normal package-style paths. Because it contains no code, it does not run setup logic, expose shortcuts, or change any state. Its main value is structural: it helps the project organize source-extension code under a clear namespace.

## 📊 State Registers Touched

- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-presentation-slots` — The shared conversation display slots for showing artifacts, sources, tasks, sites, automations, image previews, and other side-panel content.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-redis-service-pool` — The shared Redis client/connection and stream/cache coordination state used by live turn streaming, workers, and Redis-backed extension stores.
- `reg-workspace-object-store` — The current persisted workspace object records, such as tasks, monitors, todos, reports, prompts, and site metadata, read and mutated through object APIs, tools, jobs, and slots.
- `reg-extension-catalog-cache` — The extension app-store/catalog metadata and update availability state used when discovering, installing, removing, or bundling extensions.
