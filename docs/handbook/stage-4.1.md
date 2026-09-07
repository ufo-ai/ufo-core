# Core extension loading and manifest contract  `stage-4.1`

This stage is part of startup and shared support for UFO’s extension system. Extensions are add-ons, like plug-in attachments for a tool. Before the rest of the system can use them, UFO must know what is installed, what is allowed, and what each extension offers.

The store file supports the command line side. It can search an extension catalog, pin a chosen extension into a lockfile, and remove that pin later. A pin is a saved decision that says, “this deployment should load this extension.”

The loader file is the main front door at runtime. It discovers installed extensions, checks the active set against the allowed pins, then converts each extension’s declarations into usable system pieces such as tools, hooks, object types, skills, credentials, and backends.

The manifest file defines the contract for those declarations. It is the menu format an extension must use, with checks to catch unsafe or unclear entries. The package file simply makes this folder importable by Python.

## Files in this stage

### Catalog pinning
Command-line catalog operations choose which available extensions are pinned for this deployment.

### `core/src/ufo/host/ext/store.py`

`domain_logic` · `extension management commands`

This file is about making extension choices repeatable. The catalog is a list of extensions that are available to a deployment. The lockfile is the saved list of extensions that should actually be loaded, including exact version and digest information so the system can later confirm it is using the same code. Without this file, a user could not reliably turn catalog entries into pinned, loadable extensions.

The file defines simple shapes for catalog entries and search results, then provides an ExtensionStore that works like a small shop counter. You can ask what is on the shelves, buy one item, or return one item. “Buying” an extension means adding a pin to the lockfile, not downloading it. The extension must already be installed in the Python environment, because the store checks the installed package metadata and computes a digest, which is like a fingerprint of the extension source.

There is one important guardrail: catalog entries marked disabled are treated as bundle-only. They may exist in the catalog for packaging workflows, but normal install refuses them. Another careful detail is that writing the lockfile preserves the lockfile’s existing UFO version marker if the file already exists; otherwise it records the current installed UFO version.

#### Function details

##### `read_catalog`  (lines 48–49)

```
def read_catalog(path: Path) -> Catalog
```

**Purpose**: Reads an extension catalog file from disk and turns it into a checked Catalog object. This gives the rest of the store code a clean, predictable list of available extensions.

**Data flow**: It receives a file path. It reads the file text, parses it as TOML, which is a human-readable configuration format, and validates the result against the Catalog shape. It returns a Catalog containing the extension entries.

**Call relations**: This is the doorway from a catalog file into the extension store. It relies on the file path to supply text and on TOML parsing to turn that text into data before the store can search or install from it.

*Call graph*: 2 external calls (read_text, loads).


##### `ufo_version`  (lines 52–53)

```
def ufo_version() -> str
```

**Purpose**: Returns the installed version of the main ufo package. The store uses this when it needs to create a new lockfile and record which UFO version the lockfile belongs to.

**Data flow**: It takes no direct input. It asks Python package metadata for the version of the package named ufo, then returns that version string.

**Call relations**: ExtensionStore._write calls this only when there is no existing lockfile version to preserve. In that moment, this function supplies the anchor version for the new lockfile.

*Call graph*: called by 1 (_write); 1 external calls (version).


##### `pin_for`  (lines 56–63)

```
def pin_for(name: str) -> ExtensionPin
```

**Purpose**: Builds the exact lockfile pin for an extension that is already installed in the current Python environment. It refuses to create a pin if the extension name is only known in the catalog but is not actually installed.

**Data flow**: It receives an extension name. It looks through the installed extensions discovered by the loader, finds the matching manifest and package entry, computes a digest, and returns an ExtensionPin containing the name, manifest version, and digest. If nothing installed matches the name, it raises an error instead of guessing.

**Call relations**: ExtensionStore.install calls this after confirming the catalog allows the extension. pin_for then hands back the precise pin that install writes into the lockfile, using loader helpers to discover installed extensions and fingerprint their source.

*Call graph*: called by 1 (install); 3 external calls (__init__, discovered, extension_digest).


##### `ExtensionStore.search`  (lines 73–84)

```
def search(self, query: str) -> tuple[StoreListing, ...]
```

**Purpose**: Searches the catalog by name and marks which matching extensions are already pinned in the lockfile. This is what lets a user see both availability and current install state in one result.

**Data flow**: It receives a search query string. It reads the current pins from the lockfile, scans catalog entries whose names contain the query, and creates StoreListing results with each entry’s name, version, disabled flag, and installed status. It returns all matching listings as a tuple.

**Call relations**: This is the read-only path through the store. It calls ExtensionStore._pins to learn what is currently installed, then combines that lockfile information with the catalog to produce search results for callers such as extension commands.

*Call graph*: calls 1 internal fn (_pins); 1 external calls (__init__).


##### `ExtensionStore.install`  (lines 86–96)

```
def install(self, name: str) -> ExtensionPin
```

**Purpose**: Pins a catalog extension into the lockfile so the loader can use it later. It also enforces two rules: the extension must be in the catalog, and it must not be marked bundle-only.

**Data flow**: It receives an extension name. It searches the catalog for that exact name, rejects missing or disabled entries, asks pin_for to create a precise pin from the installed package, removes any older pin with the same name, writes the updated pin list to the lockfile, and returns the new pin.

**Call relations**: This is the main install path. It uses pin_for to turn an installed extension into a trustworthy lockfile entry, uses ExtensionStore._pins to keep the other existing pins, and hands the final list to ExtensionStore._write so the change is saved.

*Call graph*: calls 3 internal fn (_pins, _write, pin_for).


##### `ExtensionStore.remove`  (lines 98–102)

```
def remove(self, name: str) -> None
```

**Purpose**: Removes an extension pin from the lockfile. This makes the deployment stop treating that extension as selected, without uninstalling the Python package itself.

**Data flow**: It receives an extension name. It reads the current pins, checks that one of them matches the name, filters that pin out, and writes the remaining pins back to the lockfile. If the name was not pinned, it raises an error.

**Call relations**: This is the uninstall-from-lockfile path. It calls ExtensionStore._pins to inspect the current state and then ExtensionStore._write to save the state after the named pin has been removed.

*Call graph*: calls 2 internal fn (_pins, _write).


##### `ExtensionStore._pins`  (lines 104–105)

```
def _pins(self) -> tuple[ExtensionPin, ...]
```

**Purpose**: Reads the current extension pins from the lockfile, or returns an empty list if no lockfile exists yet. It gives the rest of the store a single simple way to ask, “What is currently selected?”

**Data flow**: It uses the store’s lockfile path. If the file exists, it reads and parses the lockfile and returns its extension pins. If the file does not exist, it returns an empty tuple.

**Call relations**: Search, install, and remove all call this before making decisions. It hides the “file may not exist yet” detail so those higher-level actions can work with a normal list of pins.

*Call graph*: called by 3 (install, remove, search); 1 external calls (read_lockfile).


##### `ExtensionStore._write`  (lines 107–111)

```
def _write(self, pins: tuple[ExtensionPin, ...]) -> None
```

**Purpose**: Writes a new set of extension pins to the lockfile while keeping the lockfile tied to a UFO version. This is the final save step for install and remove.

**Data flow**: It receives the complete tuple of pins that should be in the lockfile. If a lockfile already exists, it reads the existing UFO version marker and keeps it. If not, it asks for the current UFO package version. It then builds a Lockfile object with that version and the supplied pins, and writes it to disk.

**Call relations**: ExtensionStore.install and ExtensionStore.remove call this after they have decided the new pin list. _write then hands the finished Lockfile to the loader’s write helper, making the change durable for later loading.

*Call graph*: calls 1 internal fn (ufo_version); called by 2 (install, remove); 3 external calls (__init__, read_lockfile, write_lockfile).


### Extension loading
The host extension package exposes the loader that discovers pinned extensions, validates the active set, and converts declarations into runtime components.

### `core/src/ufo/host/ext/loader.py`

`orchestration` · `startup, request handling, turn setup, connection callbacks`

Extensions in this project do not register themselves by calling into the host. Instead, they publish small Python entry points, which are like named plugs the host can discover at startup. This file walks those plugs, loads each extension manifest, rejects unsafe or duplicate declarations, and then builds the runtime pieces that a turn, portal page, connection callback, or background job needs.

A key idea here is the lockfile. If a lockfile exists, it is treated like a sealed packing list: only the pinned extensions may load, and their installed source code must match the saved digest. If anything is missing or changed, startup fails rather than running unknown code. Without a lockfile, the system behaves like a development setup and loads everything it discovers.

After discovery, the rest of the file is mostly translation. Manifests say, “this extension contributes these tools, credential slots, hooks, skills, surfaces, object kinds, index backends, and so on.” The loader turns that into concrete registries and chains the runtime can call. It also builds an ExtensionContext, which is the extension’s scoped view of workspace services, credentials, search, blobs, surfaces, and audience. In plain terms, this file is the switchboard: it decides which extensions are present, verifies they are safe to use, and wires each declared feature to the right runtime context.

#### Function details

##### `lockfile_path`  (lines 143–144)

```
def lockfile_path() -> Path
```

**Purpose**: Finds the lockfile path the loader should use. Operators can override the default path with an environment variable, which lets different deployments point at different pinned extension sets.

**Data flow**: It reads the UFO_LOCKFILE environment variable. If it is set, that value becomes a Path object; if not, it uses the default file name ufo.lock. The result is the path that later lockfile reads use.

**Call relations**: load_manifests asks this helper where to look before deciding whether the system is in pinned mode or development mode.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 147–148)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads a lockfile from disk and validates it as a Lockfile object. This gives the loader a trusted list of pinned extensions and their expected digests.

**Data flow**: It receives a file path, reads the file text, parses the JSON, and turns it into a validated Lockfile model. The output is a structured lockfile object instead of raw text.

**Call relations**: load_manifests calls this after finding that a lockfile exists, so it can compare installed extensions against the pinned list.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 151–152)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a Lockfile object back to disk as formatted JSON. Tools that pin or bundle extensions can use this to produce the exact file that startup later reads.

**Data flow**: It receives a destination path and a Lockfile object. It converts the model to indented JSON, adds a final newline, and writes that text to the path. The main change is on disk.

**Call relations**: This is the writer-side companion to read_lockfile. The loader itself mainly reads lockfiles, while command-line tooling can use this to create them.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 155–169)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed extension entry point and loads its manifest. It also refuses duplicate extension names and blocks third-party extensions from declaring privileged member-context access.

**Data flow**: It asks Python packaging metadata for all ufo.extension entry points. For each one, it loads and calls the entry point to get a Manifest, checks naming and privilege rules, and stores the manifest with the entry point it came from. The output is a dictionary keyed by manifest name.

**Call relations**: load_manifests uses this as the raw installed-extension inventory. migration_locations also uses it so it can connect active manifests back to their installed package directories.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 172–183)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds every installed pack. A pack is a bundle that selects a coherent group of extensions plus pack-level skills or onboarding content.

**Data flow**: It reads all ufo.pack entry points, loads each zero-argument callable, and expects a Pack object back. It builds a dictionary by pack name and raises an error if two packs claim the same name.

**Call relations**: _pack_manifests calls this when configuration asks to activate one named pack, so the loader can narrow the manifest set to that bundle.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 186–191)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds the import information for the top-level Python package that owns an extension entry point. The loader needs this to locate source files and migration folders.

**Data flow**: It takes an entry point, extracts the first part of its module name, and asks Python’s import system for that package’s ModuleSpec. If the package cannot be found or has no source location, it raises an error.

**Call relations**: extension_digest uses this to find the files to hash. migration_locations uses it to find an extension package’s migrations directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 194–199)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Finds the directory that should contain an extension’s migrations folder. It works for both normal multi-file packages and single-file modules.

**Data flow**: It receives a ModuleSpec. If the spec points to a package directory, it returns that directory; otherwise it returns the parent directory of the module file.

**Call relations**: migration_locations calls this after _entry_spec so it can look beside the extension source for a migrations directory.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 202–218)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the source-code fingerprint for an installed extension. This is what lets the lockfile detect drift or tampering before the system runs extension code.

**Data flow**: It receives an entry point, finds the package or module that owns it, reads all relevant source files, skips machine-generated bytecode caches, and passes the file contents to extension_content_digest. The result is a sha256-prefixed digest string.

**Call relations**: load_manifests calls this for each pinned extension in a lockfile, comparing the current installed code against the digest recorded by the operator.

*Call graph*: calls 2 internal fn (_entry_spec, extension_content_digest); called by 1 (load_manifests); 1 external calls (Path).


##### `extension_content_digest`  (lines 221–227)

```
def extension_content_digest(files: Mapping[str, bytes]) -> str
```

**Purpose**: Hashes a named set of files in a stable way. It includes both file names and file contents so renaming or editing a file changes the digest.

**Data flow**: It receives a mapping from relative file names to bytes. It sorts the names, hashes each name and each file body into one combined SHA-256 hash, and returns the final string with the sha256: prefix.

**Call relations**: extension_digest uses this after it has collected the installed extension files. This helper keeps the digest rule separate from the file-discovery rule.

*Call graph*: called by 1 (extension_digest); 1 external calls (sha256).


##### `migration_locations`  (lines 230–247)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Returns the database migration folders contributed by the active extensions. Database migrations are versioned scripts that update the database schema.

**Data flow**: It discovers installed extensions, loads the active manifests, connects each active manifest back to its installed entry point, and checks whether that package has a migrations directory. It returns the existing migration directory paths as strings.

**Call relations**: Migration-running code can call this so core database migrations and active extension migrations are applied together. It relies on load_manifests so inactive installed extensions do not add database tables.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 250–273)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Chooses the active extension manifests for this run. It enforces the lockfile when present, or loads all discovered extensions in development mode.

**Data flow**: It first discovers installed extensions and finds the lockfile path. If no lockfile exists, every discovered manifest becomes active. If a lockfile exists, each pinned extension must be installed and its current digest must match the pinned digest. If a pack name is supplied, the active set is narrowed through _pack_manifests.

**Call relations**: This is the central source of truth for active extensions. migration_locations calls it, and most higher-level startup code is expected to feed its result into the registry-building functions in this file.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 276–309)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Narrows the active manifest set to one named pack. It also creates a manifest for the pack’s own skills and onboarding contributions.

**Data flow**: It receives a pack name and the already-active extension map. It discovers installed packs, finds the selected pack, verifies that each bundled extension is active, rejects a name collision between the pack and a bundled extension, then appends a pack-level Manifest. The output is the ordered tuple of manifests for that pack.

**Call relations**: load_manifests delegates here only when configuration selects a pack. The result then flows through the same manifest consumers as ordinary extensions.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 312–321)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These declarations tell the runtime which environment variables or helpers should be exposed for usable grants.

**Data flow**: It receives active manifests, scans every connector, keeps only connectors that declare a CLI credential, and returns a dictionary keyed by OAuth provider name.

**Call relations**: injecting_slots uses this map while checking that sandbox environment variable names do not accidentally collide.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 324–397)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into sandboxed work and validates that their names, sentinels, hosts, and environment variables do not conflict. This prevents silent authentication mistakes.

**Data flow**: It receives manifests, extracts credential slots with injection rules, reads connector CLI exports, and checks several shared namespaces: sentinels, host metering dimensions, host-choice references, and sandbox environment variables. If the declarations are safe, it returns the injectable slots; otherwise it raises a clear runtime error.

**Call relations**: This function is a guardrail for both the egress proxy and the engine that exports credentials into sandboxes. It calls connector_clis so connector credentials and slot credentials are validated together.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 400–492)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the full tool set available during one conversation turn. It combines built-in tools, extension tools, connector tools, object actions, and object verbs, each with the right extension context.

**Data flow**: It receives active manifests plus optional services such as credentials, search indexes, blob storage, audience, and portal-link settings. For each manifest with tools or objects, it builds an ExtensionContext, adds global tools to the callable tool list, adds bound tools as object actions, registers object kinds, then adds core object kinds and object verbs. It returns the tool definitions, a lookup from extension tool name to context, and the object verb registry.

**Call relations**: The turn dispatcher uses this when preparing to answer a member. Internally it hands object kinds and actions to object_registry and action_registry, and it calls core_object_kinds so built-in object views appear beside extension objects.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `member_object_registry`  (lines 504–569)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object registry used by member-facing pages outside a live turn. This lets the portal list objects and available controls using the same extension declarations as turns do.

**Data flow**: It receives manifests and optional credential/search/link services. It builds context for extensions that declare objects or bound actions, collects those object kinds and actions, adds core object kinds, validates them through the object and action registries, and returns a MemberObjectRegistry containing the final maps.

**Call relations**: Portal code can call this when it needs object rows or action metadata without a conversation audience. It mirrors turn_tools closely but produces a registry rather than a turn tool set.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `frame_admissible`  (lines 572–586)

```
def frame_admissible(manifests: tuple[Manifest, ...], registry: MemberObjectRegistry) -> frozenset[str]
```

**Purpose**: Determines which tools or object actions an embedded frame page is allowed to call. This is an access-control list for frame-originated posts.

**Data flow**: It receives active manifests and a member object registry. It gathers built-in and extension tool declarations, combines them with registered actions, and asks frame_admissible_ids to return the allowed callable identifiers.

**Call relations**: Frame request handling can use this result before accepting a posted action. It depends on the registry built by member_object_registry so object-bound actions are checked by their canonical IDs.

*Call graph*: 1 external calls (frame_admissible_ids).


##### `core_object_kinds`  (lines 589–636)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, *, public_base_url: str | None=None, artifact_token_secret: str='') -> tuple[BoundKind, ...]
```

**Purpose**: Creates the built-in object kinds that depend on the active extension set: credentials, extensions, surfaces, and artifacts. These are core views over extension-related state.

**Data flow**: It receives manifests, an optional credential store, and link-signing settings. It builds ObjectKind definitions for credential slots, installed extensions, registered surfaces, and artifacts, wraps each as a BoundKind with no extension context, and returns them.

**Call relations**: turn_tools, member_object_registry, and validate_ext_tools all call this so core object views are always registered alongside extension-provided object kinds.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 9 external calls (__init__, __init__, __init__, __init__, __init__, named_extensions, artifact_object, registered_surfaces, declared_slots).


##### `skill_registry`  (lines 639–663)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the deploy-wide registry of loadable skills. Skills are reusable instruction or capability bundles that can be loaded by name.

**Data flow**: It starts with core skills, then reads skill specifications from active manifests and discovers skills on disk. It rejects duplicate names, adds optional generated skills, records which names were bundled at boot, and returns a SkillRegistry.

**Call relations**: Startup code can call this after manifests are loaded. The resulting registry is what later skill loading and skill-index rendering can rely on without resolving duplicates.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 666–670)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent the system can make available during turns.

**Data flow**: It receives manifests and flattens each manifest’s subagent profile list in manifest order. The result is a tuple of profiles.

**Call relations**: The serving layer can feed this into a SubagentRegistry. Duplicate-name enforcement happens when that registry is built, so this function simply preserves the extension load order.


##### `durable_surfaces`  (lines 673–679)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds surfaces that support durable writeback. A surface is durable here if it declares a post handler, meaning replies can be delivered later through polling or writeback.

**Data flow**: It receives manifests, scans all declared surfaces, keeps those with a post handler, and returns their names as a frozen set.

**Call relations**: Admission or turn-entry code can use this set to decide when to create writeback tracking for a conversation.


##### `turn_subagent_grants`  (lines 682–692)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects extra tool permissions that extensions grant to subagent profiles they may not own. This lets one extension widen another profile’s available tools without editing that profile.

**Data flow**: It receives manifests, groups each grant by target profile name, unions the tool names for that profile, and returns an immutable mapping from profile to granted tool names.

**Call relations**: The turn loop can fold these grants into each subagent profile before intersecting with the actual live tool set, so missing tools or profiles simply fall away later.


##### `turn_member_skills`  (lines 695–737)

```
async def turn_member_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, agent_name: str) -> tuple[tu
```

**Purpose**: Builds the member-saved skill cards available to one agent during a turn, plus a loader that can materialize a selected skill by name. It filters cards by agent targeting.

**Data flow**: It receives manifests, credentials, optional search services, and the current agent name. For each extension with member skills, it requires a credential store, builds an ExtensionContext, asks the provider for cards, skips cards not meant for this agent, and records the first provider for each skill name. It returns the visible cards and an async materializer function.

**Call relations**: Turn setup can call this when the agent needs member-level skills. Duplicate skill names are logged and ignored rather than breaking the turn.

*Call graph*: 2 external calls (log, context_for).


##### `turn_member_skills.materialize`  (lines 730–735)

```
async def materialize(name: str) -> RuntimeSkill | None
```

**Purpose**: Loads one member skill by name using the provider remembered by turn_member_skills. It returns nothing if no provider claimed that name.

**Data flow**: It receives a skill name, looks it up in the captured providers dictionary, and if found calls that provider’s materialize method with the saved ExtensionContext. The output is a RuntimeSkill or None.

**Call relations**: turn_member_skills returns this nested function to the turn runtime. It is the second half of the card flow: cards advertise possible skills, and materialize loads the chosen one.


##### `member_skill_listing`  (lines 740–766)

```
async def member_skill_listing(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Builds the full portal listing of member-provided skills. Unlike turn_member_skills, it does not filter by agent, because management pages need the whole workspace view.

**Data flow**: It receives manifests, credentials, and optional search services. For each member-skill provider, it requires credentials, builds an ExtensionContext, asks the provider to materialize all skills, keeps the first skill for each name, logs duplicates, and returns the resulting RuntimeSkill objects.

**Call relations**: Portal management pages can call this to show saved skills. It follows the same credential gate and duplicate rule as turn_member_skills.

*Call graph*: 2 external calls (log, context_for).


##### `index_backend`  (lines 772–792)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and constructs the configured search index backend. An index backend is the service that stores and searches indexed workspace content.

**Data flow**: It receives manifests, an optional configured backend name, and a credential store. It uses the configured name or default, searches extension index specs for that name, checks credential requirements, builds an ExtensionContext, and returns the backend from the spec factory. If no extension registers the name, it raises NotRegisteredError.

**Call relations**: Startup configuration can call this once and pass the resulting backend into contexts for tools, jobs, and memory features.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 795–815)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and constructs the configured embedding backend. An embedding backend turns text into numeric vectors used for semantic search.

**Data flow**: It receives manifests, an optional configured backend name, and a credential store. It picks the configured or default name, finds a matching embed spec, checks whether credentials are required, builds an ExtensionContext, and returns the client produced by the spec factory. If none matches, it raises NotRegisteredError.

**Call relations**: Startup code can resolve this before building index and memory services. The selected client is later threaded into extension contexts.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 818–843)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory-search provider from extension declarations. Memory search is the feature that looks up relevant remembered information.

**Data flow**: It receives manifests, credentials, optional index and embed services, and a provider name. It finds matching memory-search specs, returns None if there are none, rejects multiple providers with the same name, checks credential availability, builds an ExtensionContext, and wraps the built provider in MemorySearch.

**Call relations**: Runtime setup can call this for the memory provider it wants. It uses context_for so the provider runs with the declaring extension’s scoped services.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 846–894)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Checks extension tool and object declarations early, before serving traffic. This makes a bad deployment fail at boot instead of failing later during a user turn.

**Data flow**: It receives manifests and an optional credential store. It collects built-in and extension tools, collects built-in and extension bound actions, checks that credential-declaring tool extensions have credentials available, builds object and action registries, adds object verb tools, and finally constructs a ToolRegistry to catch name collisions. It returns the validated action registry.

**Call relations**: Deployment startup can call this as a safety check. It reuses core_object_kinds and the same registry builders used by turn_tools, but without per-workspace extension contexts.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, __init__, action_registry, object_registry).


##### `turn_workspace_facts`  (lines 897–933)

```
async def turn_workspace_facts(manifests: tuple[Manifest, ...], *, audience: Audience) -> tuple[str, ...]
```

**Purpose**: Reads short fact lines that extensions say are true for the current workspace. These lines can decorate a turn prompt without making the whole turn depend on every fact reader succeeding.

**Data flow**: It receives manifests and the current audience. For each manifest with workspace facts, it builds an ExtensionContext and calls each fact’s holds method. If the fact returns true, its line is added; if it raises an error, the error is warned about and that fact is skipped. The output is the tuple of true fact lines.

**Call relations**: Turn setup can call this while preparing context for the model. Its error handling is deliberately soft so one failing extension fact does not stop the member’s turn.

*Call graph*: 2 external calls (warn, context_for).


##### `turn_hooks`  (lines 936–985)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain for turn-lifecycle events. A hook is extension code that reacts to a named event during a turn, such as before or after some turn phase.

**Data flow**: It receives manifests, credentials, optional index/embed/tailer services, audience, and public base URL. For each manifest with hooks, it requires a credential store, builds an ExtensionContext, keeps only turn events, groups hooks by event, and returns a HookChain.

**Call relations**: The turn loop can call this to get the reactive hooks it should fire during the turn. Connection-recorded hooks are not driven here; those are built by connection_hooks.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 1000–1011)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Runs connection-recorded hooks after a connection has landed. It treats hooks as observe-and-react work: failures are logged but do not undo the recorded connection.

**Data flow**: It receives a ConnectionRecorded payload. For each bound hook, it creates a HookContext containing the extension context and connection payload, runs the hook with a timeout, and logs any exception or timeout instead of raising it onward.

**Call relations**: connection_hooks builds the ConnectionHookChain that owns this method. The connect flow can call fire after committing a connection so extensions can create related records or kick off follow-up work.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 1014–1040)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the chain of hooks that should run when a new connection is recorded. This is the connection-side counterpart to turn_hooks.

**Data flow**: It receives manifests, credentials, and optional index/embed services. It selects hooks whose event is connection_recorded, requires a credential store for hook-declaring extensions, builds an ExtensionContext for each extension, wraps each hook as a BoundHook, and returns a ConnectionHookChain.

**Call relations**: The connect flow uses the returned chain and later calls ConnectionHookChain.fire. The same context style as jobs and other extension code means connect-time work and retry work can share behavior.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### `core/src/ufo/host/ext/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can become an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this area using names like `ufo.host.ext...` instead of treating the folder as just a directory on disk.

The folder name `ext` suggests this package is meant to hold extension-related code for the `ufo.host` part of the project, but this file does not define any extensions, settings, functions, or classes itself. Its job is closer to putting a label on a drawer: it tells Python, “this drawer belongs to the package system, and other files may live inside it.”

Without this file, imports may fail in environments or tooling that expect traditional Python packages. Keeping it present helps make package discovery predictable.


### Manifest contract
Runtime manifest models define and validate the capabilities that extensions and packs can provide.

### `core/src/ufo/runtime/ext/manifest.py`

`data_model` · `startup and cross-cutting runtime declarations`

This file is the contract between the core application and its extensions. An extension does not directly reach into the runtime and register things one by one. Instead, it returns a frozen Manifest object that says, in one place, what it contributes. A pack does the same at a product-bundle level: it names which extensions belong together and may add its own skills, onboarding steps, and prompt text.

Most of the file is made of small immutable data classes. Each one describes one kind of contribution: a background job, an HTTP route, a credential slot, a connector, a search backend, a browser provider, a lifecycle hook, a prebuilt agent, a subagent profile, and so on. Think of these classes like labeled forms. Extensions fill out the forms; the loader reads them later and wires the system together.

The file also protects important boundaries. For example, credential declarations say whether a secret may be injected into outbound network traffic or only read inside the server. Hook declarations say which lifecycle event they listen to and what kind of outcome they may return. Agent and subagent declarations reject unsafe tool allowlists and incomplete shipped agents. Conversation slots and open connector namespaces are checked as global namespaces so two extensions cannot accidentally claim the same catch-all space.

Without this file, extensions would have no shared language for declaring what they provide, and boot-time checks would move into scattered runtime code or fail much later during a user turn.

#### Function details

##### `JobFault.__init__`  (lines 118–120)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates a named failure for a background job when the job code can safely explain what went wrong. This lets the job system record a clear, bounded reason without exposing raw provider errors or secret-bearing text.

**Data flow**: It receives a human-written reason string. It stores that reason both as the normal exception message and on a dedicated reason field. The result is an exception object that can be raised by job code and later inspected by the job runner.

**Call relations**: A background job handler raises this when it wants the scheduler to record a meaningful failure reason. The wider job failure path can then treat this differently from an unexpected exception, because the text was deliberately authored by the handler.


##### `PreToolUse.__post_init__`  (lines 437–439)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the semantic tool-call name before a tool runs, if the caller did not provide one. This gives hooks a consistent name to match against.

**Data flow**: It reads the newly created PreToolUse object. If the call field is empty, it copies tool_name into call; otherwise it leaves the supplied call value alone. The object remains frozen to normal callers, but this setup step safely completes it during creation.

**Call relations**: This runs automatically after a PreToolUse event object is created. Later, pre-tool hooks use the call value to decide whether their rule applies to this specific tool or object action.


##### `PostToolUse.__post_init__`  (lines 455–457)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the semantic tool-call name after a successful tool run, if it was not explicitly set. This keeps post-tool hooks from having to guess which name to use.

**Data flow**: It looks at the PostToolUse object just after construction. If call is blank, it sets call to the basic tool_name; if call already names a more specific action, it keeps that value. The completed event then carries both the tool result and the name hooks should match.

**Call relations**: This runs automatically when a successful tool-use event is created. The hook system later reads the normalized call field when deciding which post-tool hooks should see or modify the output.


##### `PostToolUseFailure.__post_init__`  (lines 475–477)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the semantic tool-call name for a tool that ran but failed. This lets failure hooks identify the failed operation in the same way as success hooks.

**Data flow**: It receives the newly built failure event object. If no specific call name was supplied, it copies tool_name into call. The event then carries the failed tool input, the error output, and a stable call name.

**Call relations**: This runs automatically after a PostToolUseFailure event is created. Failure-observation hooks rely on the call field to match the failed tool or object action they care about.


##### `HookSpec.__post_init__`  (lines 622–624)

```
def __post_init__(self) -> None
```

**Purpose**: Rejects hook declarations that mark the wrong event as “best effort.” In this system, only user-prompt hooks are allowed to fail softly.

**Data flow**: It reads the HookSpec fields after construction. If best_effort is true but the event is not user_prompt_submit, it raises a ValueError. Otherwise the hook declaration is accepted unchanged.

**Call relations**: This validation runs when an extension creates a HookSpec. It protects the later hook runner: tool gates must not fail open, while user-prompt context injection may be allowed to drop if the hook has a problem.


##### `AgentProvision.__post_init__`  (lines 656–682)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a shipped workspace agent is valid before an extension can declare it. It prevents confusing names, invalid icons, empty instructions, missing purpose text, and unsafe tool allowlists.

**Data flow**: It reads the proposed agent name, tool list, icon, prompt, purpose, and setup requirements. It raises a ValueError if the name is not a safe object name, if the allowlist names the low-level object-action dispatcher instead of real action IDs, if the icon is not an approved mark, if the prompt or purpose is blank, or if an agent that must perform setup lacks the tools needed to do that setup. If all checks pass, the provision object is ready for activation.

**Call relations**: This runs automatically when an extension constructs an AgentProvision. The activation code can then create or update workspace agent rows knowing that the declaration already passed the basic safety and usability checks.


##### `SubagentProfile.__post_init__`  (lines 733–755)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a subagent profile’s tool list and output contract are consistent. This matters because a subagent’s result is handed back to a parent agent, so the handoff shape must match what the profile claims.

**Data flow**: It reads the profile’s allowed tool names, output model, and concise-handoff flag. It rejects any allowlist that names the generic object-action dispatcher instead of canonical action IDs. It then inspects the output model to see whether it has the special single-field concise-result shape, and raises an error if that shape and the concise_parent_handoff flag disagree. If nothing is wrong, the profile is accepted unchanged.

**Call relations**: This runs when a SubagentProfile is created by an extension. Later, the subagent registry and spawn flow can trust that profiles marked for concise parent handoff really use the expected result schema, and that tool allowlists name dispatchable actions rather than the internal dispatcher.


##### `SubagentToolGrant.__post_init__`  (lines 774–779)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a grant adding tools to another subagent profile names real canonical tools or actions, not the internal object-action dispatcher. This keeps cross-extension tool sharing precise.

**Data flow**: It reads the grant’s tool_names. If the tuple includes the generic object-action tool, it raises a ValueError explaining that grants must name canonical action IDs instead. Otherwise the grant remains valid as declared.

**Call relations**: This validation runs when an extension creates a SubagentToolGrant. The loader can later merge valid grants into target subagent profiles without accidentally exposing the broad dispatcher tool.


##### `conversation_slot_declarations`  (lines 877–906)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: Collects all typed conversation-slot providers from active manifests and checks that they form one clean global namespace. A conversation slot is a named piece of structured conversation context that extensions can provide to the portal and runtime.

**Data flow**: It receives the active manifests. For each declared conversation-slot provider, it checks that the id has a safe format, the label is present and short enough, the icon is one the portal knows, the read and summarize callbacks are callable, and the payload type is supported. It also tracks owners so two extensions cannot use the same slot id. It returns a tuple of manifest-and-provider pairs, or raises a RuntimeError if any declaration is invalid or duplicated.

**Call relations**: The loader or startup assembly calls this when turning manifests into runtime declarations. Its output preserves both the provider and the manifest that owns it, so later code can read slots through the right extension context.


##### `open_connector_namespace`  (lines 909–921)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: Finds the one catch-all connector namespace, if any, among all active manifests. This prevents ambiguity when a connector provider slug was not explicitly registered.

**Data flow**: It receives the active manifests and scans their connector_resolver field. If none is present, it returns None. If exactly one is present, it returns that resolver. If more than one extension declares a resolver, it raises a RuntimeError because the system would not know which one owns an unregistered connector slug.

**Call relations**: Startup code uses this while assembling connector support. Later connect flows, connector lookup, and transfer-host decisions can route unknown connector slugs through this single resolver instead of choosing between competing catch-all brokers.


##### `declared_slots`  (lines 944–957)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: Turns extension credential declarations into the simpler credential-slot records used by the credentials object kind and the portal’s credentials screen. This is the shared view of all BYOK, or “bring your own key,” slots declared by active extensions.

**Data flow**: It receives the active manifests. For each manifest and each CredentialSlot inside it, it creates a DeclaredSlot with the slot name, description, owning extension name, optional injection host, and optional merge function. It returns all of those DeclaredSlot objects as one tuple.

**Call relations**: This helper is called by code that needs a unified list of declared credentials rather than each extension’s raw manifest. While building that list, it hands each slot’s information to DeclaredSlot.__init__, which creates the credential declaration object consumed by the rest of the credential and portal paths.

*Call graph*: 1 external calls (__init__).
