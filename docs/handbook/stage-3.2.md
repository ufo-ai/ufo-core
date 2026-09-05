# Extension manifests and lockfile enforcement  `stage-3.2`

This stage is part of the behind-the-scenes setup before the system starts running user work. It is the extension gatekeeper. Extensions are add-on packages, and their manifests are “registration cards” that describe what each package provides.

The built-in app manifests register first-party workspace apps such as Chat, Wiki, Issues, Metrics, and Notification. The connector manifests describe links to outside services like Slack, iMessage, Pipedream, and content sources. The agent, skill, and productivity manifests add specialist helpers for research, coding, browsing, documents, objectives, and sites. The platform and operations manifests add support pieces such as scheduled jobs, debugging, Redis transport, reports, monitors, and web features.

The loader is the front door. It scans installed extensions, checks them against the lockfile, and only loads approved code that has not changed. The store supports catalog, install, and remove commands by updating that lockfile. The extension object file then exposes loaded extensions as read-only workspace objects, so people can inspect what is available without accidentally changing the installed system.

## Sub-stages

- [Built-in app extension manifests](stage-3.2.1.md) `stage-3.2.1` — 9 files
- [Connector and external service extension manifests](stage-3.2.2.md) `stage-3.2.2` — 7 files
- [Agent, skill, and productivity extension manifests](stage-3.2.3.md) `stage-3.2.3` — 7 files
- [Platform, scheduling, and operations extension manifests](stage-3.2.4.md) `stage-3.2.4` — 8 files

## Files in this stage

### Extension store and loading
Manages extension catalog operations, verifies approved installed packages, loads extension contributions, and exposes loaded extensions as read-only workspace objects.

### `core/src/ufo/host/ext/store.py`

`domain_logic` · `extension CLI operations, before the loader uses the lockfile`

This file solves a practical problem: UFO needs a safe way to choose extensions from a known catalog and record exactly which ones should be used later. The catalog is like a shop shelf: it lists extension names, versions, and whether an entry is disabled for normal install. The lockfile is like a receipt or packing list: it records the exact pinned extensions that the loader will trust at startup.

The file defines simple shapes for catalog entries and search results, then provides an ExtensionStore object that works with one catalog and one lockfile. Searching checks the catalog and marks which matching entries are already pinned in the lockfile. Installing first proves the extension is in the catalog, refuses entries marked disabled, then checks the current Python environment to make sure that extension package is really installed. It records not just the name and version, but also a digest, which is a fingerprint of the extension source. Removing does the reverse: it deletes a pin from the lockfile, but only if it was actually present.

An important detail is that this store does not download packages. It only pins extensions that are already installed in the environment. That prevents the lockfile from claiming an extension exists when UFO would not be able to load it.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file and turns it into a checked Catalog object. Someone uses this when they need the store’s list of available extensions from a TOML file, which is a simple text format for configuration.

**Data flow**: It takes a file path, reads the file text from disk, parses that text as TOML, and asks the Catalog model to validate the result. The output is a Catalog with a tuple of catalog entries, or an error if the file does not match the expected shape.

**Call relations**: This is the doorway from a catalog file on disk into the store logic. After it produces a Catalog, that catalog can be placed inside an ExtensionStore, where search and install use it to decide what names are allowed.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Finds the installed version of the main UFO package. This matters because the lockfile keeps track of the UFO version it is anchored to.

**Data flow**: It takes no project-specific input. It asks Python’s package metadata for the version of the installed package named "ufo" and returns that version string.

**Call relations**: ExtensionStore._write calls this only when it is creating a lockfile that did not already exist. That way a new lockfile records the current UFO version, while an existing lockfile keeps its original version anchor.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an installed extension. It makes sure the named extension is truly present in the current Python environment before it can be recorded.

**Data flow**: It receives an extension name. It asks the loader’s discovery code for installed extensions, looks up that name, and fails with a clear error if the name is missing. If found, it takes the extension’s declared version and computes a digest, meaning a fingerprint of the extension’s source, then returns an ExtensionPin containing the name, version, and digest.

**Call relations**: ExtensionStore.install calls this after the catalog check passes. This function hands install the trustworthy pin that should be written into the lockfile, and it relies on the loader’s discovery and digest tools so the store and loader agree about what an extension is.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and shows whether each result is already installed according to the lockfile. It is what a user-facing command can use to show available extensions and their current status.

**Data flow**: It receives a query string. It reads the current pins from the lockfile, builds a set of pinned extension names, then walks through the catalog entries whose names contain the query. For each match, it returns a StoreListing with the catalog name, version, disabled flag, and whether it is already pinned.

**Call relations**: This method calls ExtensionStore._pins to learn what the lockfile already contains. It does not change anything; it only combines catalog information with lockfile state to produce search results for callers such as an extension-listing command.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins one extension into the lockfile so UFO can load it later. It protects users from installing unknown catalog names, disabled bundle-only entries, or packages that are not actually installed in the Python environment.

**Data flow**: It receives an extension name. It looks for that name in the catalog, raises an error if it is absent, and raises another error if the catalog marks it disabled. Then it creates a fresh pin with pin_for, reads the existing pins, replaces any older pin for the same name, writes the new pin list to the lockfile, and returns the pin it wrote.

**Call relations**: This is the main write path for adding an extension through the store. It calls pin_for for the verified extension fingerprint, ExtensionStore._pins to preserve the other existing pins, and ExtensionStore._write to save the updated lockfile.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes one extension pin from the lockfile. This tells UFO not to load that extension from this lockfile anymore.

**Data flow**: It receives an extension name. It reads all current pins, checks that at least one pin has that name, and raises an error if not. If the pin exists, it writes back a new pin list with that name left out.

**Call relations**: This is the reverse of ExtensionStore.install. It calls ExtensionStore._pins to inspect the current lockfile and ExtensionStore._write to save the shortened list.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Gets the extension pins currently recorded in the lockfile. It hides the small detail that a missing lockfile simply means “no extensions are pinned.”

**Data flow**: It reads the store’s lockfile path. If the file exists, it loads the lockfile and returns its extensions. If the file does not exist, it returns an empty tuple.

**Call relations**: Search, install, and remove all call this helper whenever they need the current installed state. By centralizing the missing-file behavior here, those higher-level methods can treat the pin list as always available.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete set of extension pins to the lockfile. It also preserves the lockfile’s UFO version anchor when updating an existing file.

**Data flow**: It receives the full tuple of pins that should be in the lockfile after the change. If the lockfile already exists, it reads the existing UFO version from it; otherwise it uses the currently installed UFO version. It then builds a Lockfile object with that version and the new pins, and writes it to disk.

**Call relations**: Install and remove call this after they have decided what the new pin list should be. This helper hands the final lockfile object to the loader’s write function, keeping disk-writing details out of the user-facing store operations.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/host/ext/loader.py`

`orchestration` · `startup and per-turn setup`

Extensions in this project do not register themselves by calling into the host at runtime. Instead, each installed package exposes an entry point, which is like a named plug socket Python can discover. This loader reads those plug sockets, asks each extension for its manifest, and then builds the live shape of the system from those manifests.

A key job here is safety. If a lockfile exists, it acts like a packing list for production: only the listed extensions are allowed, and each one's source files must match a stored SHA-256 digest, which is a fingerprint of the files. If the code has drifted or been tampered with, startup fails instead of running unknown behavior. Without a lockfile, all discovered extensions are loaded, which is useful for development.

After discovery, the file converts declarations into working registries. It builds the tool list for a turn, object kinds for the portal, hook chains for turn events and connection events, skill registries, credential injection rules, index and embedding backends, migration folders, and more. In everyday terms, this file is the adapter board: extensions bring labeled parts, and the loader plugs those parts into the places the host expects them.

#### Function details

##### `lockfile_path`  (lines 143–144)

```
def lockfile_path() -> Path
```

**Purpose**: Chooses where the extension lockfile lives. It lets an environment variable override the normal `ufo.lock` path, so deployments can point the host at a specific pinned extension list.

**Data flow**: It reads the `UFO_LOCKFILE` environment variable if present. If it is absent, it uses `ufo.lock`. It returns that location as a filesystem path object.

**Call relations**: When `load_manifests` decides whether the system is in pinned mode or development mode, it asks this function where to look for the lockfile.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 147–148)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads and validates the lockfile from disk. This turns the saved JSON text into a structured `Lockfile` object the loader can trust.

**Data flow**: It receives a path, reads the file text at that path, and parses it as lockfile JSON. The result is a `Lockfile` model containing the pinned UFO version and extension pins.

**Call relations**: When `load_manifests` finds a lockfile, it calls this function before checking each pinned extension against its recorded digest.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 151–152)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a lockfile object back to disk in a readable JSON format. This is used by code that creates or updates the pinned extension list.

**Data flow**: It receives a path and a `Lockfile` object. It converts the object to indented JSON, adds a final newline, and writes that text to the given path.

**Call relations**: This is the counterpart to `read_lockfile`. Other tooling can use it to save the exact extension set that this loader later reads.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 155–169)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension in the current Python environment. It also rejects duplicate extension names and prevents third-party extensions from claiming privileged capabilities.

**Data flow**: It asks Python for entry points in the `ufo.extension` group. For each one, it loads the callable, calls it to get a manifest, checks safety rules, and stores the manifest together with the entry point under the manifest's name.

**Call relations**: Both `load_manifests` and `migration_locations` start from this discovery step. Downstream code can assume names are unique because this function has already checked them.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 172–183)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds installed extension packs. A pack is a named bundle that can select several extensions plus its own shared skills or onboarding content.

**Data flow**: It reads Python entry points in the `ufo.pack` group, loads each zero-argument provider, calls it to get a `Pack`, and stores packs by name. If two packs use the same name, it raises an error.

**Call relations**: `_pack_manifests` calls this when configuration selects a pack, so it can narrow the active extension set to exactly that pack's declared contents.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 186–191)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds the import information for the top-level Python package that owns an extension entry point. This is needed to locate the extension's source files on disk.

**Data flow**: It takes an entry point, extracts the first part of its module name, and asks Python import machinery where that package comes from. It returns the package's module specification or raises an error if the source cannot be found.

**Call relations**: `extension_digest` uses it to find files to fingerprint. `migration_locations` uses it to find an extension's possible migrations folder.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 194–199)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the directory that belongs to an extension package. This matters because extension database migrations are expected to live beside that package.

**Data flow**: It receives a module specification. If the extension is a package directory, it returns that directory; if it is a single Python file, it returns the file's parent directory.

**Call relations**: `migration_locations` uses this after `_entry_spec` to look for a `migrations` directory for each active extension.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 202–218)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Creates the source-code fingerprint used to pin an extension. The fingerprint changes if any meaningful source file in the extension package changes.

**Data flow**: It receives an extension entry point, locates the package source, reads all relevant files while skipping bytecode cache files, and passes the file names and bytes to `extension_content_digest`. It returns a string beginning with `sha256:`.

**Call relations**: `load_manifests` calls this for every pinned extension so startup can refuse an installed extension whose code no longer matches the lockfile.

*Call graph*: calls 2 internal fn (_entry_spec, extension_content_digest); called by 1 (load_manifests); 1 external calls (Path).


##### `extension_content_digest`  (lines 221–227)

```
def extension_content_digest(files: Mapping[str, bytes]) -> str
```

**Purpose**: Hashes a set of named files into one stable digest. It includes both file names and file contents so renaming or editing files changes the result.

**Data flow**: It receives a mapping from file name to file bytes. It sorts the names, hashes each name and each file's content, feeds those into one combined SHA-256 hash, and returns the final digest string.

**Call relations**: `extension_digest` gathers files from disk and delegates the actual fingerprint calculation to this function.

*Call graph*: called by 1 (extension_digest); 1 external calls (sha256).


##### `migration_locations`  (lines 230–247)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Lists database migration folders contributed by active extensions. This lets the database upgrader apply extension schema changes alongside core schema changes.

**Data flow**: It discovers installed extensions, loads the active manifests, finds the package directory for each active installed extension, and checks for a `migrations` subdirectory. It returns the existing migration directories as strings.

**Call relations**: It combines `discovered`, `load_manifests`, `_entry_spec`, and `_package_dir`. Code that applies database migrations uses its output to know which extension migration branches to include.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 250–273)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Builds the active manifest list, which is the master list of extensions the host will actually use. It enforces the lockfile when present and supports narrowing to one selected pack.

**Data flow**: It discovers installed extensions, checks whether the lockfile exists, and either activates all discovered extensions or only the pinned ones after digest verification. If a pack name is provided, it passes the active map to `_pack_manifests` and returns that narrower list.

**Call relations**: This is the central discovery result that later derivations read. `migration_locations` calls it, and many other startup paths are expected to use its returned manifests.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 276–309)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Turns a selected pack into the exact manifests it should activate. It ensures every bundled extension is installed and active, then adds a synthetic manifest for the pack's own contributions.

**Data flow**: It receives a pack name and the already active extension manifests. It discovers packs, finds the named pack, collects the manifests for each bundled extension, checks for name collisions, creates one pack-level `Manifest`, and returns the combined tuple.

**Call relations**: `load_manifests` calls this only when configuration asks for a pack. It lets pack-level skills and onboarding travel through the same manifest-reading paths as normal extension features.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 312–321)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These declarations tell the system which environment variable or helper should represent a provider grant.

**Data flow**: It receives manifests, walks through their connectors, and keeps connectors that declare CLI credentials. It returns a dictionary keyed by OAuth provider name.

**Call relations**: `injecting_slots` uses this map while checking that sandbox environment variables do not collide in unsafe ways.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 324–397)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into outbound traffic or sandbox environments, and checks that their names and environment variables will not conflict. This prevents silent authentication mistakes.

**Data flow**: It receives manifests, extracts credential slots with injection rules, gathers connector CLI environment claims, and validates sentinel uniqueness, host metering dimensions, host-choice slot references, and environment variable ownership. It returns the validated credential slots.

**Call relations**: It calls `connector_clis` to include connector-level environment variables in the same conflict check. The egress proxy and sandbox export paths can then use its result without repeating these safety checks.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 400–492)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the full tool set available during one agent turn. It combines built-in tools, extension tools, connector tools, object actions, object verbs, and the extension contexts needed to run them.

**Data flow**: It receives active manifests plus optional services such as credentials, indexing, embeddings, blob storage, audience information, and public URLs. It creates an `ExtensionContext` for each contributing extension, adds normal tools to the callable tool list, turns bound tools into object actions, registers object kinds, adds core object kinds, and returns the final tools, a tool-to-context map, and object verbs.

**Call relations**: This is called when preparing a turn for dispatch. It hands object registration to `object_registry` and `action_registry`, and it uses `core_object_kinds` so core objects appear beside extension objects.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `member_object_registry`  (lines 504–569)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object registry used by the member-facing portal outside an active turn. It lets the portal show rows and controls for core and extension objects.

**Data flow**: It receives manifests and optional services, creates context for extensions that declare objects or bound actions, registers their object kinds and actions, adds core object kinds, and returns a `MemberObjectRegistry` containing kind and action maps.

**Call relations**: It mirrors the validation and registration style of `turn_tools`, but uses workspace-level context rather than a conversation audience. It calls `core_object_kinds`, `object_registry`, and `action_registry`.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `frame_admissible`  (lines 572–586)

```
def frame_admissible(manifests: tuple[Manifest, ...], registry: MemberObjectRegistry) -> frozenset[str]
```

**Purpose**: Computes which tools or object actions an embedded app frame is allowed to call. This is an allow-list for messages coming from framed pages.

**Data flow**: It receives manifests and a member object registry. It gathers built-in and extension tools, combines them with registered actions, and returns the set of callable IDs marked as safe for frame use.

**Call relations**: It delegates the ID calculation to `frame_admissible_ids`. The action lane can use this set when deciding whether a frame-originated request is permitted.

*Call graph*: 1 external calls (frame_admissible_ids).


##### `core_object_kinds`  (lines 589–636)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, *, public_base_url: str | None=None, artifact_token_secret: str='') -> tuple[BoundKind, ...]
```

**Purpose**: Creates the object kinds that core itself exposes, such as credentials, extensions, surfaces, and artifacts. These are built from the active manifests so the system can inspect what is installed and configured.

**Data flow**: It receives manifests plus optional credential and link settings. It builds object stores for declared credential slots, named extensions, registered surfaces, and artifacts, wraps each as a bound core kind, and returns them.

**Call relations**: `turn_tools`, `member_object_registry`, and `validate_ext_tools` call this so core object types are always present in the same registry as extension object types.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 9 external calls (__init__, __init__, __init__, __init__, __init__, named_extensions, artifact_object, registered_surfaces, declared_slots).


##### `skill_registry`  (lines 639–663)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the deploy's loadable skill registry. Skills are reusable instruction or capability packages, and this function combines core skills with extension or pack skills.

**Data flow**: It starts with core skills, reads skill definitions from each manifest's declared paths, rejects duplicate names, then adds any generated skills after checking for collisions. It returns a `SkillRegistry` with all known skills and a record of bundled names.

**Call relations**: Startup code can call this once after manifests are loaded. It relies on `discover_skills` to read skill files from disk and returns the registry used by skill lookup and prompt rendering.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 666–670)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent that can be available during turns.

**Data flow**: It receives manifests and flattens all `subagents` lists in manifest order. It returns the profiles as a tuple.

**Call relations**: The serving layer can build its subagent registry from this result. Duplicate profile names are left for that registry to reject clearly.


##### `durable_surfaces`  (lines 673–679)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds surfaces whose replies should be delivered through a durable writeback path. A surface is durable when it has a post handler.

**Data flow**: It receives manifests, scans their surface declarations, keeps surface names that define a `post` handler, and returns them as a frozen set.

**Call relations**: Admission or turn setup can use this set to decide when to create writeback tracking rows for conversations entering those surfaces.


##### `turn_subagent_grants`  (lines 682–692)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects extra tools that extensions grant to subagent profiles they do not own. This lets one extension widen another profile's abilities without editing that profile.

**Data flow**: It receives manifests, walks through `subagent_tool_grants`, groups tool names by target profile, and returns a dictionary from profile name to frozen set of granted tools.

**Call relations**: The turn loop can merge these grants into a profile's own tools, then intersect with the actually available live tool set.


##### `turn_member_skills`  (lines 695–737)

```
async def turn_member_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, agent_name: str) -> tuple[tu
```

**Purpose**: Builds the member-specific skill cards available to one agent during a turn, plus a loader that can materialize a selected skill later. It respects per-agent targeting.

**Data flow**: It receives manifests, a credential store, optional index and embedding services, and the current agent name. For each member-skill provider, it creates an extension context, asks for cards, filters cards not meant for this agent, logs duplicate names, and returns the visible cards plus a materializer function.

**Call relations**: This function prepares skill routing for a turn. Its nested `turn_member_skills.materialize` function is returned to the caller and later used to load the selected skill from the provider that claimed it.

*Call graph*: 2 external calls (log, context_for).


##### `turn_member_skills.materialize`  (lines 730–735)

```
async def materialize(name: str) -> RuntimeSkill | None
```

**Purpose**: Loads one member skill by name from the provider chosen earlier. It returns nothing if no provider claimed that skill.

**Data flow**: It receives a skill name. It looks up the saved provider and extension context from `turn_member_skills`; if found, it asks that provider to materialize the runtime skill and returns it.

**Call relations**: This function is created inside `turn_member_skills` and handed back to the turn machinery. It closes over the provider map built during card discovery.


##### `member_skill_listing`  (lines 740–766)

```
async def member_skill_listing(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Builds the full member-skill listing for the management portal. Unlike turn-specific skill cards, it does not filter by agent.

**Data flow**: It receives manifests, a credential store, and optional index and embedding services. For each member-skill provider, it creates context, asks the provider to materialize all skills, logs duplicate names, keeps the first claim, and returns the listed runtime skills.

**Call relations**: The portal can call this for a workspace-wide management view. It uses the same context-building and credential requirements as `turn_member_skills`.

*Call graph*: 2 external calls (log, context_for).


##### `index_backend`  (lines 772–792)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and builds the workspace's indexing backend. An indexing backend stores and searches indexed text or vectors for memory and retrieval features.

**Data flow**: It receives manifests, an optional configured backend name, and a credential store. It chooses the configured name or `default`, finds the matching manifest index spec, checks credential requirements, builds an extension context, and returns the backend. If none is registered, it raises `NotRegisteredError`.

**Call relations**: Startup uses this to turn manifest declarations into the concrete index service. It calls `context_for` before invoking the selected extension's factory.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 795–815)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and builds the deploy's embedding client. An embedding client turns text into numerical vectors used for semantic search.

**Data flow**: It receives manifests, an optional configured backend name, and a credential store. It chooses the configured name or `default`, finds the matching embed spec, checks credential requirements, creates context, and returns the client. If no extension registers the selected name, it raises `NotRegisteredError`.

**Call relations**: Startup resolves this once and passes the result into indexing, memory, and extension contexts that need embeddings.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 818–843)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory-search provider if an extension declares it. This gives the runtime a pluggable way to search stored memory.

**Data flow**: It receives manifests, credential and optional index or embedding services, and a provider name. It finds matching providers, rejects duplicates, checks credentials, creates the declaring extension's context, builds the provider, wraps it in `MemorySearch`, and returns it; if none exist, it returns `None`.

**Call relations**: The runtime can call this after backend setup. It uses `context_for` so the provider runs with the same scoped access as other extension code.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 846–894)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Checks extension tool and object declarations at boot without needing a workspace-specific context. This makes bad deployments fail early instead of failing later during a user turn.

**Data flow**: It receives manifests and an optional credential store. It gathers built-in tools and actions, adds extension tools and bound actions, checks credential-key requirements, builds object and action registries including core object kinds, validates the complete tool registry, and returns the validated action registry.

**Call relations**: This is a startup safety pass. It calls `core_object_kinds`, `object_registry`, `action_registry`, `ObjectVerbs`, and `ToolRegistry` to exercise the same registration gates used later by turn setup.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, __init__, action_registry, object_registry).


##### `turn_workspace_facts`  (lines 897–933)

```
async def turn_workspace_facts(manifests: tuple[Manifest, ...], *, audience: Audience) -> tuple[str, ...]
```

**Purpose**: Asks extensions for short factual lines about the current workspace to include in a turn. If one fact reader fails, the rest of the turn can continue.

**Data flow**: It receives manifests and the current audience. For each declared workspace fact, it builds the extension's context, calls the fact's `holds` check, logs a warning if it fails, and includes the fact's line only when the check says it is true.

**Call relations**: Turn prompt preparation can call this to decorate the prompt. It uses `context_for` for scoped extension access and `warn` to report but swallow individual read failures.

*Call graph*: 2 external calls (warn, context_for).


##### `turn_hooks`  (lines 936–985)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain for turn lifecycle events. Hooks are extension callbacks that react to moments in a turn, such as before or after work happens.

**Data flow**: It receives manifests, credential and optional service objects, a turn tailer, audience, and public URL. It groups declared hooks by supported turn event, creates one extension context per hook-owning manifest, binds each hook to that context, and returns a `HookChain`.

**Call relations**: The turn loop uses the returned `HookChain` when events occur. This function creates `BoundHook` objects and hands the grouped hooks to `HookChain`.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 1000–1011)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Runs connection-recorded hooks after a connection has landed. These hooks are observe-only: failures are logged and swallowed so the connection itself remains recorded.

**Data flow**: It receives a `ConnectionRecorded` payload. For each bound hook, it runs the handler with a timeout and passes a `HookContext` containing the extension context and connection payload. If the handler errors or times out, it logs the failure and continues.

**Call relations**: Instances are built by `connection_hooks`. The connect flow calls this method after committing a connection so extensions can create follow-up resources.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 1014–1040)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the hook chain used when a new external connection is recorded. This lets extensions react to successful account or provider connections.

**Data flow**: It receives manifests, a credential store, and optional index and embedding services. It finds hooks whose event is `connection_recorded`, checks credential requirements, creates extension contexts, wraps hooks as `BoundHook` objects, and returns a `ConnectionHookChain`.

**Call relations**: The connect flow uses the returned chain and later calls `ConnectionHookChain.fire`. This function mirrors turn hook binding but only for the connection-recorded event.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### `core/src/ufo/host/ext/extension_kind.py`

`domain_logic` · `startup and object request handling`

This file turns the deploy's active extension manifests into an object kind called `extension`. A manifest is an extension's declaration: its name, version, tools, object types, credential slot names, chat surfaces, jobs, hooks, sources, and subagent profiles. Think of it like a public directory of installed plug-ins: you can look up what each plug-in offers, but you cannot change the installation from the directory itself.

The file first defines how extension names become object names. Because workspace object names must be lowercase and hyphenated, an extension such as `scheduled_tasks` is shown as `scheduled-tasks`. It also checks for name clashes at startup, so two extensions cannot accidentally appear as the same object.

The main class, `ExtensionObjects`, provides the read-only behavior. Its `list` method gives a compact table of extensions, including version and counts of tools and credential slots. Its `get` method returns the full declaration for one extension. Its `status` method shows what the extension asks from the deploy, such as sandbox internet access or required seams from other extensions. Any attempt to create, update, or delete an extension object is rejected, because installation is controlled by the deploy lockfile and command-line tooling, not by chat or object mutation.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: Builds the object-name lookup table for the active extension manifests. It turns each extension's own name into the safe workspace object name that users will type, and fails early if two extensions would end up with the same name.

**Data flow**: It receives a tuple of extension manifests. For each manifest, it lowercases the name, replaces non-letter-or-number runs with hyphens, trims extra hyphens, checks that the result is a valid object name, and stores the manifest under that rendered name. It returns a dictionary from rendered object name to manifest, or raises an error if two manifests collide.

**Call relations**: This is used when the host is preparing the active extension set for exposure as objects. It relies on regular-expression replacement to normalize names and on the shared object-name validator so extension objects obey the same naming rules as the rest of the system.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a paged list of all loaded extensions. Each row gives a quick summary: the extension's version, how many tools it adds, and how many credential slots it declares.

**Data flow**: It reads the stored mapping of extension object names to manifests. For each manifest, it builds an `ExtensionSpec`, then turns that into a lightweight row with a name, summary text, and sortable fields. It passes those rows and the caller's list query into the paging helper, which returns the page the caller asked for.

**Call relations**: This is called when someone lists objects of kind `extension`. It asks `ExtensionObjects._spec` to convert each manifest into the public declaration format, wraps those declarations into object rows, and hands them to `object_page` so normal filtering, ordering, and paging behavior can be applied.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: Returns the full public declaration for one loaded extension. It is used when someone wants to inspect exactly what one extension contributes.

**Data flow**: It receives an object name and looks it up in the extension mapping. If there is no matching manifest, it returns `None`. If it finds one, it converts the manifest into an `ExtensionSpec` and wraps it in an object detail response with no creation or update timestamps, because these are declarations loaded from manifests rather than stored database rows.

**Call relations**: This is called when someone reads a single `extension` object. It delegates the manifest-to-spec conversion to `ExtensionObjects._spec`, then packages the result as an `ObjectDetail` for the object system.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Shows the deploy-level needs declared by one extension. This is separate from the extension's spec: the spec says what the extension contributes, while status says what it asks the deploy to provide.

**Data flow**: It receives an object name and checks the extension mapping. If the extension is missing, it returns `None`. If found, it returns a small dictionary containing whether the extension wants sandbox internet access and the list of required seams, meaning integration points that another extension must provide.

**Call relations**: This is called when the object system asks for the status of an `extension` object. It reads directly from the manifest and does not call other helpers because the status fields are already simple deploy-facing values.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update an extension object. This protects the rule that extensions are installed and removed only through the deploy lockfile, not by editing workspace objects.

**Data flow**: It receives the requested new spec, any old spec, the object name, and generation-check information, but does not use them to change anything. Instead, it immediately raises a `VerbNotSupported` error with a message explaining that extension installation is a deploy action.

**Call relations**: This is invoked when the object system tries to apply a create or update operation to an `extension` object. Rather than handing off to storage or changing a manifest, it stops the flow by raising the shared unsupported-verb error.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete an extension object. Removing an extension must happen through the deploy's extension tooling, not through normal object deletion.

**Data flow**: It receives the object name and generation-check information, but makes no changes. It raises a `VerbNotSupported` error that explains the correct place to install or remove extensions.

**Call relations**: This is invoked when the object system tries to delete an `extension` object. It deliberately ends the request with an unsupported-operation error instead of modifying the active extension set.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: Converts an internal extension manifest into the public `ExtensionSpec` shown to users. It exposes names of contributed things, but not secret values or runtime-only internals.

**Data flow**: It receives one manifest. It reads the manifest's name, version, tools, connector tools, object kinds, credential slot names, surfaces, jobs, hook events, source backends, and subagent profile names. It gathers those into an `ExtensionSpec` and returns it.

**Call relations**: This is the shared translation step used by both `ExtensionObjects.list` and `ExtensionObjects.get`. Listing uses it to build summary rows, while getting one extension uses it to return the full declaration.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).
