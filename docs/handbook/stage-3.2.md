# Core extension discovery infrastructure  `stage-3.2`

This stage is the behind-the-scenes system that lets UFO find and use optional add-ons called extensions. An extension is a separate package that can add new tools, hooks, object types, skills, credentials, or sandbox backends to the core program. The manifest file defines the promise every extension must make: a clear description of what it provides, in a shape the runtime can understand. The loader is the loading dock. During startup or preparation, it looks for installed extensions, checks which ones are allowed, reads their manifests, and converts their declarations into usable parts for the rest of UFO. The store is the small catalog and lockfile manager. It lets commands search available extensions and record which ones are installed or remove them later, like keeping a shopping list and receipt. The sandbox selector connects this discovery work to execution. It takes sandbox backends registered by extensions, combines them with configuration, and chooses which sandbox carriers should stay available to open or resume isolated work areas.

## Files in this stage

### Sandbox backend selection
Selects the extension-provided sandbox backends that a deployment should keep available at runtime.

### `core/src/ufo/harness/sandbox/select.py`

`orchestration` · `startup`

A sandbox backend is the place where isolated workspaces run, such as the built-in local machine backend or a remote provider added by an extension. This file is the deploy’s “backend picker.” Without it, the system could silently choose the wrong sandbox provider, fail to resume old workspaces, or let a remote sandbox run without the required protected network proxy.

The file starts with one always-available backend: `local`. It then reads every installed extension manifest and adds any sandbox carriers those extensions provide. A carrier is the object that actually knows how to create or reconnect to sandboxes for one backend. The code refuses duplicate backend names, because two providers claiming the same name would make configuration ambiguous.

The main result is a `DeployCarriers` record. It contains the default carrier used for new sandboxes, plus a separate map of “resume backends.” Resume backends are older or alternate providers kept alive only so existing sandbox handles can still be reopened. This is like moving to a new storage company while keeping the old keyring around until all old boxes are emptied.

Remote carriers get an extra safety check: they must have a public HTTPS proxy URL configured. That proxy is how in-sandbox network access stays credential-injected, default-denied, and measured, rather than becoming an uncontrolled open path.

#### Function details

##### `select_carriers`  (lines 25–49)

```
def select_carriers(config: Config, manifests: tuple[Manifest, ...]) -> DeployCarriers
```

**Purpose**: Builds the full set of sandbox carriers for this deploy from configuration and extension manifests. It decides which backend is the default for new sandboxes and which extra backends must stay available for resuming existing sandboxes.

**Data flow**: It receives the loaded `Config` and a tuple of extension `Manifest` objects. It starts with the built-in `local` carrier, adds carriers declared by extensions, checks for duplicate or invalid resume backend names, then asks `_built` to create the default carrier and each resume carrier. It returns a `DeployCarriers` object containing the ready-to-use default carrier, its specification, and the resume carrier map.

**Call relations**: This is the public entry point of the file. During deploy setup, higher-level startup code calls it after configuration and extension manifests are available. It delegates the per-backend validation and construction work to `_built`, then packages the results into `DeployCarriers` for the rest of the sandbox harness to use.

*Call graph*: calls 1 internal fn (_built); 2 external calls (__init__, __init__).


##### `_built`  (lines 52–72)

```
def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Creates one carrier from a registered backend name and checks that it is safe to use. It gives clear errors when the configured backend name is unknown or when a remote backend lacks a secure public proxy URL.

**Data flow**: It receives the registry of available carrier specifications, the current configuration, and the backend name to build. It looks up the matching `CarrierSpec`; if none exists, it raises a `NotRegisteredError`. If the carrier runs off-cluster, meaning outside the local process environment, it reads `[sandbox] proxy_public_url`, parses it as a URL, and requires it to be HTTPS with a real hostname. If all checks pass, it calls the carrier factory and returns the new carrier together with its specification.

**Call relations**: `select_carriers` calls this once for the default sandbox backend and once for each resume backend. `_built` is the file’s safety gate: before any carrier object is handed back to the deploy, it verifies that the name is registered and that remote sandbox traffic can go through the required encrypted proxy path.

*Call graph*: called by 1 (select_carriers); 2 external calls (__init__, urlparse).


### Extension catalog and loading
Manages the extension catalog and lockfile, then loads permitted installed extensions into runtime contributions.

### `core/src/ufo/host/ext/store.py`

`domain_logic` · `extension management commands`

UFO extensions are Python packages that must be both available in the running Python environment and recorded in a lockfile before the loader will use them. This file connects those two worlds: a catalog says what extensions are offered, while the lockfile says what this deployment has chosen to pin and boot with. Think of the catalog like a menu, and the lockfile like the order receipt that the kitchen actually follows.

The file defines simple data shapes for catalog entries and search results. A catalog entry has a name, version, and a disabled flag. Disabled entries are “bundle-only”: they may be pinned by a bundle-building tool, but normal install refuses them.

The main class, ExtensionStore, works over one catalog and one lockfile path. Search reads the current lockfile so it can show whether each matching catalog item is already installed. Install checks that the requested name is in the catalog, rejects disabled entries, verifies that the Python package is actually installed, computes a digest of its source, and writes a pin into the lockfile. Remove does the reverse: it refuses to remove something that is not pinned, then rewrites the lockfile without that extension. The digest matters because it makes the lockfile point to a specific extension build, not just a name.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked Catalog object. This is used when the tool needs to know which extensions are offered by a deployment.

**Data flow**: It receives a file path. It reads the file text, parses that text as TOML, which is a human-friendly configuration format, and validates the result against the expected catalog shape. It returns a Catalog containing the listed extensions.

**Call relations**: This function sits at the boundary between a catalog file and the in-memory store. It relies on the path object to read text and on the TOML parser to turn that text into ordinary data before validation.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Finds the installed version of the UFO package. The lockfile uses this as an anchor when a new lockfile is first written.

**Data flow**: It takes no input. It asks Python’s package metadata system for the version of the installed package named “ufo”. It returns that version as a string.

**Call relations**: ExtensionStore._write calls this only when there is no existing lockfile to copy a UFO version from. In that moment, this function supplies the version label that gets written into the new lockfile.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the lockfile pin for one installed extension. A pin records the extension name, its declared version, and a digest, which is a fingerprint of its source.

**Data flow**: It receives an extension name. It asks the extension loader what extensions are actually discovered in the current Python environment. If the name is missing, it raises an error because the store cannot pin code that is not installed. If found, it reads the extension manifest version, computes a source digest, and returns an ExtensionPin.

**Call relations**: ExtensionStore.install calls this after it has confirmed that the name appears in the catalog and is allowed for normal install. This function then hands install the exact pin that should be written into the lockfile.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and marks which matching extensions are already pinned in the lockfile. This gives users a useful list instead of just raw catalog entries.

**Data flow**: It receives a query string. It reads the current pins from the lockfile, builds a set of pinned names, then scans catalog entries whose names contain the query text. It returns StoreListing objects with the extension name, catalog version, disabled status, and whether it is already installed.

**Call relations**: This is the read-only browsing path of ExtensionStore. It calls ExtensionStore._pins to learn the current lockfile state, then turns catalog entries into StoreListing results for the command layer to show to a user.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins an extension into the lockfile so UFO will load it later. It enforces the catalog rules before changing anything.

**Data flow**: It receives an extension name. It looks for that name in the catalog. If it is not listed, it raises an error. If it is marked disabled, it raises an error explaining that normal install is not allowed. Otherwise it asks pin_for to create the correct pin from the actually installed Python package. It then reads the existing pins, replaces any old pin with the same name, writes the updated lockfile, and returns the new pin.

**Call relations**: This is the main write path for adding an extension. It uses pin_for to turn an installed package into a reliable lockfile entry, uses ExtensionStore._pins to preserve other existing pins, and hands the final pin list to ExtensionStore._write.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. This means the loader will no longer treat that extension as selected for this deployment.

**Data flow**: It receives an extension name. It reads the current pins. If none of them match the name, it raises an error because there is nothing installed to remove. Otherwise it filters that pin out and writes the remaining pins back to the lockfile. It returns nothing.

**Call relations**: This is the uninstall-like path for ExtensionStore. It calls ExtensionStore._pins to inspect the current lockfile, then calls ExtensionStore._write to save the lockfile after the named extension has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the extension pins currently stored in the lockfile. If the lockfile does not exist yet, it treats that as an empty list of pins.

**Data flow**: It uses the store’s lockfile path. If the file exists, it reads and parses the lockfile and returns its extensions. If the file is missing, it returns an empty tuple.

**Call relations**: This helper is shared by search, install, and remove so they all interpret the lockfile in the same way. Search uses it to mark installed results, install uses it to preserve unrelated pins, and remove uses it to decide what can be deleted.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a complete new lockfile using a supplied set of extension pins. It also preserves the existing UFO version anchor when one is already present.

**Data flow**: It receives the full tuple of pins that should appear in the lockfile. If the lockfile already exists, it reads the current UFO version from it. If not, it asks ufo_version for the installed UFO package version. It builds a Lockfile object from that version and the supplied pins, then writes it to disk.

**Call relations**: Install and remove call this after they have decided the exact final pin list. This function is the last step in those flows: it turns their in-memory decision into the lockfile that the extension loader will later boot against.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### `core/src/ufo/host/ext/loader.py`

`orchestration` · `startup and per-turn/request setup`

Extensions are how UFO grows without hard-coding every feature into the core program. This file is the place where those extensions enter the system. An extension does not call a registration function at runtime; instead, it exposes a small declaration called a Manifest through Python entry points, which are package-advertised hooks discovered from the installed environment.

The loader first discovers all installed extensions and packs. If a lockfile exists, it acts like a sealed shipping list: only the pinned extensions may load, and their source code must still match the saved digest. This prevents a deployment from silently running changed or unexpected extension code. Without a lockfile, the system behaves more like a development setup and loads everything it can find.

After that, the file translates active manifests into the pieces used during normal operation. It builds the tool list for a turn, binds each extension tool to an ExtensionContext, collects hooks for turn events and connection events, registers object kinds and actions, chooses indexing and embedding backends, exposes credential injection rules, and gathers skills and subagents. It also performs many early checks, such as duplicate names or missing credential support, so the service fails loudly at startup instead of breaking later in the middle of a user turn.

#### Function details

##### `lockfile_path`  (lines 143–144)

```
def lockfile_path() -> Path
```

**Purpose**: Finds the lockfile path the deployment should use. It lets operators override the default file name with an environment variable.

**Data flow**: It reads the UFO_LOCKFILE environment variable if present. If that variable is absent, it falls back to ufo.lock. It returns the chosen location as a Path object.

**Call relations**: load_manifests calls this before deciding whether the deployment is pinned by a lockfile or running in open development mode.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 147–148)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads a lockfile from disk and turns it into a validated Lockfile object. This protects the rest of the loader from malformed lockfile contents.

**Data flow**: It receives a filesystem path, reads the file text, parses the JSON, and validates it against the expected lockfile shape. The result is a Lockfile containing the pinned UFO version and extension pins.

**Call relations**: load_manifests uses this when a lockfile exists, so it can compare installed extensions against the pinned list.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 151–152)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a Lockfile object back to disk as formatted JSON. This is the counterpart to read_lockfile for tooling that creates or updates pinned extension sets.

**Data flow**: It receives a path and a Lockfile object. It serializes the lockfile with indentation, adds a final newline, and writes that text to the path.

**Call relations**: Nothing else in this file calls it, but command-line tooling such as extension or bundle commands can use it to create the file that load_manifests later reads.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 155–169)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension advertised through the ufo.extension entry point group. It also rejects duplicate extension names and blocks third-party extensions from declaring privileged member-context access.

**Data flow**: It asks Python packaging metadata for extension entry points. For each one, it loads the callable, calls it to get a Manifest, checks safety and uniqueness rules, and returns a dictionary keyed by manifest name with both the Manifest and its EntryPoint.

**Call relations**: load_manifests uses this as the complete installed-extension inventory. migration_locations also uses it to connect active manifests back to their installed package locations.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 172–183)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds every installed UFO pack advertised through the ufo.pack entry point group. A pack is a bundle that names a coherent set of extensions plus pack-level skills or onboarding content.

**Data flow**: It reads pack entry points, loads and calls each one to get a Pack, checks that no two packs share the same name, and returns them in a dictionary by name.

**Call relations**: _pack_manifests calls this when configuration asks to activate one named pack instead of the full active extension set.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 186–191)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Locates the import information for the Python package that owns an extension entry point. This is needed to find the extension source files and migration directory.

**Data flow**: It receives an EntryPoint, takes the top-level module name, asks Python import machinery where that module comes from, and returns its module specification. If the source cannot be found, it raises an error.

**Call relations**: extension_digest uses it to find files to hash. migration_locations uses it to find a package directory where database migrations may live.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 194–199)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the directory that should be treated as an extension package directory. This is where the loader looks for an extension's migrations folder.

**Data flow**: It receives a module specification. If the extension is a package, it returns the package directory. If it is a single Python file, it returns that file's parent directory.

**Call relations**: migration_locations calls this after _entry_spec so it can check whether an active installed extension has a migrations directory.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 202–218)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the source-code digest used to prove an installed extension still matches the lockfile. It is a tamper and drift check for pinned deployments.

**Data flow**: It receives an EntryPoint, finds the owning module or package, reads all relevant source files while ignoring bytecode cache files, and passes their names and bytes to extension_content_digest. It returns a sha256-prefixed digest string.

**Call relations**: load_manifests calls this for each pinned extension in the lockfile and refuses to boot if the computed digest differs from the pinned one.

*Call graph*: calls 2 internal fn (_entry_spec, extension_content_digest); called by 1 (load_manifests); 1 external calls (Path).


##### `extension_content_digest`  (lines 221–227)

```
def extension_content_digest(files: Mapping[str, bytes]) -> str
```

**Purpose**: Hashes the named files that make up an extension package in a stable way. File names and file contents both affect the result.

**Data flow**: It receives a mapping from file names to bytes. It processes the names in sorted order, mixes a hash of each name and a hash of each file body into one sha256 hash, and returns the final digest with the sha256: prefix.

**Call relations**: extension_digest hands the collected extension files to this helper so the digest algorithm is kept in one clear place.

*Call graph*: called by 1 (extension_digest); 1 external calls (sha256).


##### `migration_locations`  (lines 230–247)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Finds database migration folders contributed by active extensions. These folders tell the database upgrader which extension-owned schema changes to apply.

**Data flow**: It discovers installed extensions, loads the active manifest set, optionally narrowed to a pack, then matches each active manifest back to its entry point. For each installed active extension, it looks for a migrations directory and returns all found paths as strings.

**Call relations**: It combines discovered, load_manifests, _entry_spec, and _package_dir. Database migration code can call it to layer extension migrations on top of core migrations.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 250–273)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Chooses the active extension manifests for this run. This is the central gate between what is installed and what the deployment is allowed to use.

**Data flow**: It discovers installed extensions and checks for a lockfile. With no lockfile, it activates every discovered manifest. With a lockfile, it requires each pinned extension to be installed and to match its pinned digest. If a pack name is supplied, it narrows the result through _pack_manifests.

**Call relations**: Many higher-level derivations start from the manifests this function returns. migration_locations also calls it so migrations reflect exactly the same active extension set.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 276–309)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Builds the manifest list for one selected pack. It ensures the pack's bundled extensions are installed and active, then adds a synthetic manifest for the pack's own contributions.

**Data flow**: It receives a pack name and the active manifest dictionary. It discovers installed packs, finds the named pack, collects each bundled extension manifest from the active set, checks for name collision with the pack itself, then creates and appends a Manifest for pack-level skills and prompt content.

**Call relations**: load_manifests calls this only when configuration selects a pack. It uses discovered_packs to know which packs exist.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 312–321)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These declarations tell the system what environment variables or helpers should expose usable grants.

**Data flow**: It receives active manifests, walks through their connectors, keeps connectors that declare a CLI credential, and returns a dictionary keyed by OAuth provider.

**Call relations**: injecting_slots calls this so connector credential exports are checked in the same namespace as normal credential slot exports.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 324–397)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into sandboxed work and validates that their names, sentinels, hosts, and environment variables do not conflict. This avoids silent authentication mistakes.

**Data flow**: It receives manifests and extracts slots with injection rules. It builds a declared-slot set, includes connector CLI environment claims, checks for duplicate sentinels, conflicting metering dimensions for the same host, undeclared host-choice slots, and environment variables trying to carry different values. It returns the validated injectable slots.

**Call relations**: It uses connector_clis as part of its conflict check. Proxy and sandbox setup code can rely on its result instead of repeating the same safety rules.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 400–490)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the complete tool set available during one conversational turn. It includes core tools, extension tools, connector tools, object actions, and object-verb tools.

**Data flow**: It receives active manifests plus services such as credential storage, indexing, embedding, blob storage, audience, and URL settings. It creates an ExtensionContext for each extension that contributes tools or objects, adds plain tools to the callable tool list, registers bound object actions separately, builds object kind and action registries, adds object verb tools, and returns the final tools, a map from tool name to extension context, and object verbs.

**Call relations**: This is called when the turn engine needs to know what the model may call. It delegates core object registration to core_object_kinds and uses object_registry and action_registry to validate and assemble object-related tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `member_object_registry`  (lines 502–567)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object registry used for member-facing reads outside an active turn, such as portal pages. It gives the portal the same object kinds and actions the turn system would validate.

**Data flow**: It receives manifests and optional services. For each extension with objects or bound actions, it creates a workspace-level ExtensionContext, binds object kinds and actions, adds core object kinds, validates them through the registries, and returns a MemberObjectRegistry containing kinds and actions.

**Call relations**: Portal and member-read code can call this instead of turn_tools when there is no conversation audience. It shares core_object_kinds and the same registry validation path used by turn setup.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `frame_admissible`  (lines 570–584)

```
def frame_admissible(manifests: tuple[Manifest, ...], registry: MemberObjectRegistry) -> frozenset[str]
```

**Purpose**: Computes which tools or bound actions an embedded app frame is allowed to post back to this deployment. This is a safety list for frame-originated calls.

**Data flow**: It receives manifests and a member object registry. It gathers builtin tools and declared extension or connector tools, then asks frame_admissible_ids to produce the accepted callable IDs, including canonical IDs for bound actions.

**Call relations**: The action lane can use this result when deciding whether a browser frame is allowed to invoke a given tool or action.

*Call graph*: 1 external calls (frame_admissible_ids).


##### `core_object_kinds`  (lines 587–634)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, *, public_base_url: str | None=None, artifact_token_secret: str='') -> tuple[BoundKind, ...]
```

**Purpose**: Creates the object kinds owned by UFO core but informed by active extensions. These include credentials, extensions, surfaces, and artifacts.

**Data flow**: It receives manifests, optional credential storage, and public-link settings. It builds ObjectKind objects for credential slots, installed extensions, registered surfaces, and artifacts, wraps them as BoundKind entries with no extension context, and returns them.

**Call relations**: turn_tools, member_object_registry, and validate_ext_tools all call this so core object kinds are always registered consistently beside extension-provided object kinds.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 9 external calls (__init__, __init__, __init__, __init__, __init__, named_extensions, artifact_object, registered_surfaces, declared_slots).


##### `skill_registry`  (lines 637–661)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the deploy-wide registry of loadable skills. It combines core skills, skills contributed by active manifests, and optionally generated skills.

**Data flow**: It starts with core skills by name. For each manifest skill spec, it reads skills from disk and rejects duplicate names. It then adds generated skills, also rejecting duplicates, and returns a SkillRegistry with a record of which names came from bundled sources.

**Call relations**: Boot or setup code can call this once so later skill loading and prompt rendering have a single unambiguous skill catalog.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 664–668)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent the turn system may use.

**Data flow**: It receives manifests, walks through each manifest's subagent profiles in manifest order, and returns them as one tuple.

**Call relations**: The serving layer can use this output to build the SubagentRegistry that participates in turns.


##### `durable_surfaces`  (lines 671–677)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Identifies surfaces whose replies are delivered through durable writeback. A surface is considered durable when it declares a post handler.

**Data flow**: It receives manifests, checks each declared surface, keeps the surface names that have a post handler, and returns them as a frozen set.

**Call relations**: Admission or turn-entry code can use this set to decide when to register writeback rows for conversations on those surfaces.


##### `turn_subagent_grants`  (lines 680–690)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects extra tool permissions that extensions grant to subagent profiles they may not own. This lets one extension widen a subagent's available tools without editing that subagent directly.

**Data flow**: It receives manifests, walks through each subagent tool grant, unions tool names by target profile, and returns a dictionary from profile name to frozen set of tool names.

**Call relations**: The turn loop can fold these grants into a profile's own tool list before intersecting with the actual live tool set.


##### `turn_member_skills`  (lines 693–735)

```
async def turn_member_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, agent_name: str) -> tuple[tu
```

**Purpose**: Builds the member-saved skill cards available to a specific agent during a turn, plus a loader for turning a selected card into a RuntimeSkill.

**Data flow**: It receives manifests, services, and an agent name. For each manifest with a member skill provider, it checks credentials, creates an ExtensionContext, asks the provider for cards, filters out cards not meant for this agent, keeps the first provider for each skill name, logs collisions, and returns the cards plus an async materializer function.

**Call relations**: Turn setup can call this to show or route member-level skills. The nested materialize function closes over the provider map built here.

*Call graph*: 2 external calls (log, context_for).


##### `turn_member_skills.materialize`  (lines 728–733)

```
async def materialize(name: str) -> RuntimeSkill | None
```

**Purpose**: Loads one member skill by name using the provider selected by turn_member_skills. It returns nothing if no provider claimed that name.

**Data flow**: It receives a skill name. It looks up the name in the provider map captured from the outer function. If found, it calls that provider's materialize method with its ExtensionContext and returns the RuntimeSkill; otherwise it returns None.

**Call relations**: turn_member_skills returns this function to the turn system, so the turn can delay loading the full skill until a particular card is actually needed.


##### `member_skill_listing`  (lines 738–764)

```
async def member_skill_listing(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Builds a full listing of member-provided skills for management views. Unlike turn_member_skills, it is not filtered to one agent.

**Data flow**: It receives manifests and optional services. For each member skill provider, it checks credentials, creates an ExtensionContext, asks the provider to materialize all skills, keeps the first skill for each name, logs duplicates, and returns the resulting RuntimeSkill objects.

**Call relations**: Portal or administration pages can call this to display the workspace's whole member skill set.

*Call graph*: 2 external calls (log, context_for).


##### `index_backend`  (lines 770–790)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and constructs the indexing backend for the workspace. An indexing backend is the component that stores and searches indexed text or vectors.

**Data flow**: It receives manifests, an optional configured backend name, and credential storage. It chooses the configured name or default, searches manifest index specs for that name, checks credential availability if needed, creates an ExtensionContext, calls the backend factory, and returns the IndexBackend. If none is registered, it raises NotRegisteredError.

**Call relations**: Startup wiring can call this once and pass the resulting backend into contexts used by tools, memory, and jobs.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 793–813)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and constructs the embedding client for the deployment. An embedding client turns text into numeric vectors used for search and memory.

**Data flow**: It receives manifests, an optional configured name, and credential storage. It chooses the configured name or default, searches manifest embed specs, checks credential availability, creates an ExtensionContext, calls the chosen factory, and returns the EmbedClient. If no extension registers the selected name, it raises NotRegisteredError.

**Call relations**: Startup wiring can call this and then pass the embed client to indexing, memory tools, and extension contexts.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 816–841)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider from extension declarations. It returns None when no provider with that name is installed.

**Data flow**: It receives manifests, credential storage, optional index and embed services, and a provider name. It finds matching memory search specs, rejects duplicates, checks credentials for the owning extension, builds an ExtensionContext, wraps the provider implementation in MemorySearch, and returns it.

**Call relations**: Memory-related setup can call this to connect the configured search feature to the extension that provides it.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 844–892)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Checks extension tools and object actions at boot without creating per-workspace contexts. This catches name collisions, invalid registrations, and missing credential setup before the service starts serving turns.

**Data flow**: It receives manifests and credential storage. It gathers builtin tools and actions, adds extension tools or bound actions, checks whether credential-declaring tool extensions have a credential key, builds an object registry including core and extension kinds, validates actions, builds the complete tool registry, and returns the validated action registry.

**Call relations**: Deployment startup can call this as a health gate. It reuses core_object_kinds and the same object/action registry machinery that turn_tools uses later with real contexts.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, __init__, action_registry, object_registry).


##### `turn_workspace_facts`  (lines 895–931)

```
async def turn_workspace_facts(manifests: tuple[Manifest, ...], *, audience: Audience) -> tuple[str, ...]
```

**Purpose**: Asks extensions for short factual lines about the current workspace to include in a turn prompt. A failed fact read is logged and skipped rather than breaking the turn.

**Data flow**: It receives manifests and an audience. For each manifest with workspace fact declarations, it creates an ExtensionContext with that extension's surfaces and credentials, asks each fact whether it holds, appends the fact's line when true, warns on exceptions, and returns all gathered lines.

**Call relations**: Turn prompt building can call this to decorate the model's context with extension-owned workspace facts.

*Call graph*: 2 external calls (warn, context_for).


##### `turn_hooks`  (lines 934–983)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain for turn lifecycle events. Hooks are extension callbacks that react to events during a turn.

**Data flow**: It receives manifests, services, a tailer, audience, and URL settings. For each manifest with hooks, it requires credential storage, creates an ExtensionContext, keeps only turn lifecycle hook events, binds each hook to the context, groups them by event, and returns a HookChain.

**Call relations**: The turn loop uses the returned HookChain to fire extension hooks at the right moments. Connection-recorded hooks are handled separately by connection_hooks.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 998–1009)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Runs all connection-recorded hooks after a connection has landed. It is observe-only: a failed or slow hook is logged and swallowed so the connection itself remains recorded.

**Data flow**: It receives a ConnectionRecorded payload. For each bound hook, it creates a HookContext containing the extension context and connection payload, runs the handler with a timeout, and logs any exception or timeout without re-raising it.

**Call relations**: connection_hooks builds the ConnectionHookChain. The connect flow calls fire after recording a connection so extensions can create follow-up resources such as feeds or account state.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 1012–1038)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the hook chain used by the connection flow. It gathers extension hooks interested in the connection_recorded event.

**Data flow**: It receives manifests, credential storage, and optional index and embed services. For each manifest, it selects hooks whose event is connection_recorded, checks credential storage is available, creates an ExtensionContext, binds the hooks, and returns a ConnectionHookChain.

**Call relations**: The connect flow uses the returned chain and later calls ConnectionHookChain.fire when a connection is successfully recorded.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### Manifest contract
Defines the manifest data structures that extensions use to declare tools, hooks, object types, skills, credentials, and backends.

### `core/src/ufo/runtime/ext/manifest.py`

`data_model` · `startup and cross-cutting extension registration`

An extension in this project does not directly plug itself into every part of the system. Instead, it returns a Manifest: a frozen bundle of declarations saying, for example, “I add these tools,” “I need these credentials,” “I provide this search backend,” or “I want this hook to run before a tool is used.” A pack is similar, but it groups extensions and shared pack-level additions into one product setup.

This file is like the customs form for add-ons. It lists every kind of thing an extension is allowed to bring into the runtime, and it gives each thing a clear shape. Most classes here are frozen dataclasses, meaning they are simple value objects that should not change after creation. That matters because the loader can safely read them at startup and derive registrations from them without extensions mutating the rules later.

Some declarations also protect the system from ambiguous or unsafe setup. For example, conversation slot IDs must be valid and globally unique, only one open connector namespace may exist, and agent or subagent tool allowlists cannot name the low-level dispatcher tool by mistake. Without this file, extensions would not have a shared, predictable language for declaring capabilities, and many errors would only appear much later during a user turn or background job.

#### Function details

##### `PreToolUse.__post_init__`  (lines 409–411)

```
def __post_init__(self) -> None
```

**Purpose**: This fills in the semantic tool call name when a pre-tool-use event did not provide one explicitly. It makes sure hooks can match the call consistently, even when the caller only supplied the raw tool name.

**Data flow**: It starts with a PreToolUse event containing a tool name, tool input, and possibly an empty call field. If call is empty, it copies tool_name into call. The event object then has a stable call identity for later filtering.

**Call relations**: This runs automatically when a PreToolUse payload is created. Later, hook matching uses the call value to decide which pre-tool-use hooks should see or block that tool call.


##### `PostToolUse.__post_init__`  (lines 427–429)

```
def __post_init__(self) -> None
```

**Purpose**: This gives a successful tool-use event a default call identity when none was supplied. It keeps post-tool hooks from having to guess whether to look at call or tool_name.

**Data flow**: It receives the newly created PostToolUse object. If the call field is blank, it sets call to the tool_name. The object leaves initialization with a usable semantic name for the completed tool call.

**Call relations**: This runs as part of constructing a PostToolUse payload. The hook system later uses the filled-in call value when deciding which hooks can inspect or modify the tool result.


##### `PostToolUseFailure.__post_init__`  (lines 447–449)

```
def __post_init__(self) -> None
```

**Purpose**: This gives a failed tool-use event a default call identity when the creator did not provide one. It keeps failure hooks aligned with the same naming rules used before and after successful tool calls.

**Data flow**: It starts with a PostToolUseFailure object that includes the raw tool name, input, error output, and possibly no call value. If call is empty, it writes the tool name into call. The final event can be matched by hooks in a predictable way.

**Call relations**: This runs when a failed tool-call payload is created. The hook runner can then route the failure notification to hooks that asked for that specific tool or action.


##### `HookSpec.__post_init__`  (lines 594–596)

```
def __post_init__(self) -> None
```

**Purpose**: This rejects an unsafe hook configuration: only user prompt hooks are allowed to be marked best-effort. That rule matters because tool-gating hooks must not fail open if something goes wrong.

**Data flow**: It reads the hook’s event name and best_effort flag. If best_effort is true for anything other than user_prompt_submit, it raises a ValueError. Otherwise, the hook declaration is accepted unchanged.

**Call relations**: This check runs when an extension creates a HookSpec. The extension loader later consumes only declarations that passed this local validation, so the runtime does not have to deal with invalid best-effort tool hooks.


##### `AgentProvision.__post_init__`  (lines 628–654)

```
def __post_init__(self) -> None
```

**Purpose**: This validates an extension-shipped agent before it can be installed into a workspace. It catches names, icons, prompts, purposes, and setup tool allowlists that would create a broken or misleading agent.

**Data flow**: It reads the proposed agent name, tool allowlist, icon, prompt, purpose, and setup requirements. It raises a ValueError if the name is not a safe object name, the allowlist names the wrong dispatcher tool, the icon is invalid, the prompt or purpose is missing, or an agent that must set itself up lacks the setup tools it needs. If everything is valid, the declaration remains unchanged.

**Call relations**: This runs as soon as an AgentProvision is constructed by an extension or pack. Later activation code can create or update the workspace’s agent rows knowing these basic promises have already been checked.


##### `SubagentProfile.__post_init__`  (lines 705–727)

```
def __post_init__(self) -> None
```

**Purpose**: This validates a subagent profile, which is a reusable child-agent recipe. It prevents unsafe tool allowlists and makes sure the special concise handoff mode matches the output schema exactly.

**Data flow**: It reads the profile’s tool names, output model fields, and concise_parent_handoff flag. If the low-level object action dispatcher appears in the allowlist, it raises an error. It then checks whether the output model has the exact one-field concise result shape; the concise flag and that schema must agree. A valid profile is left unchanged.

**Call relations**: This runs when an extension declares a SubagentProfile. The subagent registry later collects these profiles and can trust that their tool list and parent-handoff contract are internally consistent.


##### `SubagentToolGrant.__post_init__`  (lines 746–751)

```
def __post_init__(self) -> None
```

**Purpose**: This validates a grant that exposes tools to someone else’s subagent profile. It stops the declaration from granting the low-level dispatcher tool instead of the intended canonical action names.

**Data flow**: It reads the grant’s target profile and tool_names tuple. If the special object action dispatcher tool is present, it raises a ValueError explaining that grants must name canonical action IDs instead. Otherwise, the grant is accepted as written.

**Call relations**: This runs when an extension creates a SubagentToolGrant. The loader later unions valid grants into matching subagent profiles, while missing profiles or tools are allowed to be ignored gracefully.


##### `conversation_slot_declarations`  (lines 849–878)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: This gathers all conversation slot providers from active manifests and validates them as one shared namespace. It prevents two extensions from claiming the same slot or registering a slot the portal cannot display or read.

**Data flow**: It takes a tuple of manifests. For each manifest, it inspects each conversation slot provider, checking that the ID format is valid, the label is present and short enough, the icon is allowed, the read and summarize callbacks are callable, the payload type is supported, and the ID has not already been used. It returns a tuple of pairs, each containing the owning manifest and its provider, or raises RuntimeError on invalid declarations.

**Call relations**: Startup code calls this after manifests are loaded and before conversation slots are offered to the rest of the runtime. The returned manifest-provider pairs preserve ownership, so later code knows which extension each slot belongs to.


##### `open_connector_namespace`  (lines 881–893)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This finds the one optional catch-all connector namespace declared by active extensions. It fails if more than one extension tries to provide that catch-all, because the system would not know which one owns an unknown connector slug.

**Data flow**: It receives the active manifests and scans their connector_resolver fields. If none are present, it returns None. If exactly one is present, it returns that resolver. If it finds a second one, it raises RuntimeError.

**Call relations**: Boot-time registration code uses this when setting up connector discovery, connection flows, and connector routing. The single returned namespace becomes the fallback for connector slugs not explicitly registered by a ConnectorProvider.


##### `declared_slots`  (lines 916–929)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: This converts extension credential declarations into the shared credential-slot records used by the rest of the system. It is the bridge between a manifest’s private CredentialSlot objects and the core credential views.

**Data flow**: It takes all active manifests, walks through each manifest’s credentials, and creates a DeclaredSlot for each one. Each output record carries the slot name, description, owning extension name, optional injection host, and optional merge function. It returns all of those DeclaredSlot records as one tuple.

**Call relations**: Credential-related parts of the runtime call this after manifests are chosen. It calls DeclaredSlot.__init__ to build the normalized records that power things like the credential object kind and the portal credentials panel.

*Call graph*: 1 external calls (__init__).
