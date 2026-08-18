# Runtime configuration, extension discovery, and capability registration  `stage-4`

This stage happens mostly at startup. It decides what the running system is allowed to use, then builds a shared catalog of those abilities. The main configuration file, ufo.toml, is checked by config.py so missing or unsafe settings stop the app early. The extension loader then finds approved extensions, verifies them, and registers what they offer. extension_kind.py makes those loaded extensions visible as read-only objects, so users can inspect them without changing the deploy.

The registered pieces form several shelves. One shelf lists model, embedding, search, memory, sandbox, and Redis backends. Another loads agent skills, helper subagents, and workflow profiles. User-facing manifests add browser, coding, documents, objectives, sites, web, shell, and debugger surfaces. Connector manifests plug in services such as Slack, Gmail, Stripe, Composio, Pipedream, and API-key based providers. Background manifests schedule monitors, delayed tasks, and self-improvement checks. Finally, package marker files make extension folders importable. Together, these parts turn installed code into a clear menu of safe, usable capabilities.

## Sub-stages

- [Model, embedding, search, and sandbox backend registration](stage-4.1.md) `stage-4.1` — 11 files
- [Agent skills, subagents, and workflow profiles](stage-4.2.md) `stage-4.2` — 12 files
- [User-facing extension capability manifests](stage-4.3.md) `stage-4.3` — 8 files
- [Connector, source, and integration provider manifests](stage-4.4.md) `stage-4.4` — 7 files
- [Scheduled and background-work extension manifests](stage-4.5.md) `stage-4.5` — 3 files
- [Extension package import markers](stage-4.6.md) `stage-4.6` — 18 files

## Files in this stage

### Deploy configuration
Defines and validates the main runtime deployment configuration before extension discovery proceeds.

### `core/src/ufo/config.py`

`config` · `config load and startup`

This file is the rulebook for configuration. A deploy of UFO needs many outside resources: a database, blob storage for files and transcripts, model names, sandbox settings, ports, extension choices, and more. Instead of letting each part of the system read raw text and hope it is correct, this file turns the TOML config file into typed Python objects with built-in checks.

The main idea is simple: the config file is like a completed form, and these classes are the form validator. Each section has its own model, such as `DatabaseConfig`, `BlobConfig`, `ModelsConfig`, and `SandboxConfig`. If someone writes an unknown field, leaves out a required value, chooses a placeholder model, or gives an unsafe public ingress URL, validation fails immediately. That is important because bad config could otherwise show up much later as broken database jobs, inaccessible sandboxes, failed browser links, or security-sensitive routing mistakes.

Some settings are derived automatically. For example, if the DBOS system database URL is not given, it is built from the main database URL. Other settings are deliberately not guessed, such as missing blob storage details. At the bottom, `load_config` finds `ufo.toml` or a path named by the `UFO_CONFIG` environment variable, reads it, parses it, and returns one complete `Config` object for the rest of the program to use.

#### Function details

##### `DatabaseConfig._derive_system_url`  (lines 39–52)

```
def _derive_system_url(self) -> 'DatabaseConfig'
```

**Purpose**: This validation step fills in the DBOS system database URL when the user has not written one explicitly. DBOS is the system store used alongside the application database, so this gives it a predictable sibling database by default.

**Data flow**: It starts with the database settings, especially `url` and possibly `system_url`. If `system_url` is already present, it leaves everything alone. If it is missing, it splits the main database URL near the final database name, creates a related name ending in `_dbos`, adjusts the driver name for SQLite or PostgreSQL where needed, stores that derived URL back on the config object, and returns the updated object.

**Call relations**: This runs automatically while Pydantic, the validation library, is building a `DatabaseConfig`. It is part of the larger `load_config` flow: after the TOML file is parsed, the config object is validated, and this step makes sure later database code has both the application database URL and the DBOS system-store URL available.


##### `BlobConfig._backend_complete`  (lines 74–79)

```
def _backend_complete(self) -> 'BlobConfig'
```

**Purpose**: This validation step makes sure the chosen blob storage backend has the information it needs. A filesystem backend needs a local root folder, while an S3 backend needs a bucket name.

**Data flow**: It receives the blob storage settings after basic parsing. If `backend` is `filesystem`, it checks that `root` was provided. If `backend` is `s3`, it checks that `bucket` was provided. When a required value is missing, it raises an error; otherwise it returns the same config object unchanged.

**Call relations**: This runs automatically when the `blob` section of the main config is validated. It protects later storage code from starting with an unusable setup, so missing storage details are caught during config loading rather than when the system first tries to save a transcript or artifact.


##### `ModelsConfig._models_concrete`  (lines 102–113)

```
def _models_concrete(self) -> 'ModelsConfig'
```

**Purpose**: This validation step ensures every deploy-level model setting names a real model, not the special placeholder `auto` and not an empty string. That matters because these are the final model choices the running system depends on.

**Data flow**: It reads the three model fields: `auto_model`, `ambient_reply_model`, and `background_jobs_model`. For each one, it checks whether the value is empty or equal to the project’s `AUTO_MODEL` placeholder. If any field is not concrete, it raises a clear error. If all are valid, it returns the model config unchanged.

**Call relations**: This is called automatically during config validation. It sits between the raw TOML file and the parts of the system that actually call language models, making sure those later calls do not receive an unresolved placeholder.


##### `SandboxConfig._ingress_base_is_addressable`  (lines 197–221)

```
def _ingress_base_is_addressable(self) -> 'SandboxConfig'
```

**Purpose**: This validation step checks that the public sandbox ingress URL is safe and usable. It allows only an HTTPS base address with a host, because sandbox sites put their own label in front of that host and rely on secure browser cookies.

**Data flow**: It starts with the sandbox settings. If `ingress_public_url` is not set, it returns the config unchanged. If it is set, it breaks the URL into parts using `urlsplit`, then checks that the scheme is `https`, that a hostname exists, and that there is no path, query string, fragment, username, or password. A bad URL raises an error; a good URL passes through unchanged.

**Call relations**: This runs during sandbox config validation. It uses `urllib.parse.urlsplit` to inspect the URL, and it prevents later ingress and link-generation code from disagreeing about what the base address means or silently producing links that cannot carry the required secure session cookie.

*Call graph*: 1 external calls (urlsplit).


##### `config_path`  (lines 347–348)

```
def config_path() -> Path
```

**Purpose**: This function decides where the main configuration file should be read from. It uses the `UFO_CONFIG` environment variable if present, otherwise it falls back to `ufo.toml` in the current working directory.

**Data flow**: It reads the process environment for `UFO_CONFIG`. If that value exists, it turns it into a `Path` object. If it does not exist, it turns the default `ufo.toml` path into a `Path`. The output is the path that config loading should try to open.

**Call relations**: This is called by `load_config` when the caller did not provide a path directly. It is the small decision point that lets operators either use the conventional `ufo.toml` file or point the process at a different config file.

*Call graph*: called by 1 (load_config); 1 external calls (Path).


##### `load_config`  (lines 351–357)

```
def load_config(path: Path | None=None) -> Config
```

**Purpose**: This function reads the deploy configuration file and turns it into a validated `Config` object. It is the main doorway from plain text config into the structured settings used by the application.

**Data flow**: It receives an optional path. If no path is provided, it asks `config_path` where to look. It checks that the file exists, reads its text, parses the TOML text into ordinary data with `tomllib.loads`, and then asks the `Config` model to validate and convert that data into nested config objects. The result is a complete, checked configuration object; if the file is missing or invalid, an exception is raised instead.

**Call relations**: This is the function other startup code calls when it needs configuration. It hands off path selection to `config_path`, TOML parsing to `tomllib.loads`, and detailed field validation to the config model classes in this same file.

*Call graph*: calls 1 internal fn (config_path); 1 external calls (loads).


### Extension loading and inspection
Discovers approved extensions, registers their runtime capabilities, and exposes loaded extension manifests as read-only workspace objects.

### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and per-turn/request setup`

An extension is an add-on package that tells UFO what it can contribute: tools, credential slots, hooks, skills, database migrations, search backends, and more. This file makes those add-ons safe and predictable. It discovers extensions through Python entry points, which are advertised callable names installed with a package. If a lockfile exists, it acts like a guest list: only the extensions named there may load, and each package’s source code must match the saved hash. Without that check, a deployed system could silently run a different extension than the operator approved.

After discovery, the file builds practical runtime objects from the extension manifests. For a turn, it combines built-in tools with extension tools, creates the right ExtensionContext for each extension, and builds hook chains that can react before or after important events. It also gathers credential injection rules, object registries, subagent profiles, runtime skills, memory search providers, and selected index or embedding backends.

The important pattern is “fail early.” Duplicate names, missing credential support, unsafe environment variable collisions, missing pinned extensions, or unknown configured backends raise errors at startup instead of causing confusing failures later during a user request.

#### Function details

##### `lockfile_path`  (lines 156–157)

```
def lockfile_path() -> Path
```

**Purpose**: Chooses where the extension lockfile should be read from. It lets an environment variable override the default file name, so deployments can point UFO at a specific pinned extension list.

**Data flow**: It reads the UFO_LOCKFILE environment variable if it is set. If not, it uses the default path ufo.lock. It returns that choice as a filesystem path object.

**Call relations**: When load_manifests needs to know whether the deployment is pinned or in development mode, it asks lockfile_path for the file location before deciding what extensions are active.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 160–161)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads and validates the lockfile that lists approved extensions. This turns plain JSON text on disk into a structured Lockfile object the loader can trust.

**Data flow**: It receives a path, reads the file text from disk, and asks the Lockfile model to parse and validate it. The result is a Lockfile containing the pinned UFO version and extension pins.

**Call relations**: load_manifests calls this after finding that a lockfile exists, so it can compare installed extensions against the operator-approved list.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 164–165)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a lockfile back to disk in a stable JSON format. This is used by tooling that records the exact extension set a deployment should run.

**Data flow**: It receives a path and a Lockfile object. It converts the object to indented JSON, adds a final newline, and writes that text to disk.

**Call relations**: This is the write-side companion to read_lockfile. Command-line tools can use it to create the file that load_manifests later enforces at startup.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 168–182)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension advertised in the Python environment. It also rejects duplicate extension names and blocks third-party extensions from claiming privileged internal capabilities.

**Data flow**: It asks Python’s package metadata for ufo.extension entry points. Each entry point is loaded and called to get a Manifest. The function builds a map from manifest name to the manifest and its entry point, or raises an error if something unsafe or ambiguous is found.

**Call relations**: load_manifests uses discovered as the raw installed-extension list, and migration_locations uses it to connect active manifests back to their package directories.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 185–196)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds installed extension packs. A pack is a named bundle of extensions plus optional pack-level skills and onboarding steps.

**Data flow**: It reads ufo.pack entry points, loads and calls each one to get a Pack, and returns a map by pack name. If two packs use the same name, it raises an error.

**Call relations**: _pack_manifests calls discovered_packs when configuration selects a pack, so the active extension list can be narrowed to that bundle.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 199–204)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Locates the installed Python package or module behind an extension entry point. This is needed when the loader wants to inspect the extension’s files on disk.

**Data flow**: It receives an entry point, takes the top-level module name, and asks Python import machinery where that module lives. It returns the import specification or raises an error if the source cannot be found.

**Call relations**: extension_digest uses this to hash an extension’s source files, and migration_locations uses it to find an extension’s migrations directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 207–212)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Finds the directory that belongs to an extension package. This is where extension database migration files are expected to live.

**Data flow**: It receives a Python module specification. If the extension is a package, it returns the package directory. If it is a single file module, it returns the parent directory of that file.

**Call relations**: migration_locations calls this after _entry_spec so it can check whether an active extension has a migrations folder.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 215–235)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the fingerprint used to prove an installed extension has not changed since it was pinned. It hashes the extension’s source files, not just the entry point file.

**Data flow**: It receives an entry point, finds the package source, gathers source files while skipping bytecode cache files, and hashes both file names and file contents in sorted order. It returns a string beginning with sha256: followed by the digest.

**Call relations**: load_manifests calls this for every pinned extension in the lockfile. If the actual digest differs from the pinned digest, startup stops instead of running changed code.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 238–255)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Returns the database migration folders contributed by active extensions. These folders let extension-owned database tables evolve alongside core database schema changes.

**Data flow**: It discovers installed extensions, loads the active manifest set, finds each active extension’s package directory, and checks for a migrations subdirectory. It returns the found directories as strings.

**Call relations**: Migration application code can call this to add extension migration branches to the core migration run. It relies on load_manifests so inactive extensions do not add database tables.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 258–281)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Decides the active extension set for this run. It enforces the lockfile when present, or loads all discovered extensions in development mode when no lockfile exists.

**Data flow**: It discovers installed extensions, checks for the lockfile path, and either collects all manifests or reads the pinned list and verifies each extension’s digest. If a pack name is supplied, it narrows the result through _pack_manifests. It returns the active manifests.

**Call relations**: This is the main loading step other derivations depend on. migration_locations calls it directly, and higher-level startup code would use its result to build tools, hooks, skills, backends, and registries.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 284–316)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Turns a selected pack into the exact manifests that should be active for that pack. It also adds a synthetic manifest for the pack’s own skills and onboarding steps.

**Data flow**: It receives a pack name and the already-active extension map. It finds the declared pack, looks up each bundled extension, checks for missing or inactive ones, prevents name collisions, and returns the bundled extension manifests followed by the pack manifest.

**Call relations**: load_manifests delegates to this when configuration selects a pack. discovered_packs supplies the pack declarations it works from.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 319–328)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These declarations tell the system which environment variable should carry a connector grant marker.

**Data flow**: It receives active manifests, looks through their connectors, and keeps connectors that define a CLI credential. It returns a dictionary keyed by provider name.

**Call relations**: injecting_slots calls this while checking that all sandbox environment variable exports share one safe namespace.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 331–404)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Builds the list of credential slots that should be injected into sandboxed tool runs, while checking for dangerous naming conflicts. This prevents one secret or host choice from silently overwriting another.

**Data flow**: It receives manifests, extracts credential slots with injection rules, gathers connector CLI environment claims, and validates sentinel names, host metering dimensions, host-choice references, and environment variable ownership. It returns the validated injection slots or raises an error with a specific explanation.

**Call relations**: It uses connector_clis to include connector credential variables in the same conflict check. Proxy and sandbox setup code can then trust that the returned slots will not collide unexpectedly.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 407–471)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the complete tool list available during a turn. A turn is one interaction cycle where the system may call tools on behalf of an agent.

**Data flow**: It receives active manifests plus optional credential, index, embedding, blob, audience, and URL context. It starts with built-in tools, adds extension and connector tools, creates an ExtensionContext for each contributing extension, binds object kinds, adds object-verb tools, and returns both the tool definitions and a map from extension tool name to its context.

**Call relations**: Turn setup code uses this before dispatching tool calls. It calls core_object_kinds and object_registry so object-related commands are exposed through the same tool mechanism as ordinary tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `member_object_registry`  (lines 474–508)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object registry used when a member reads objects outside an active turn, such as through a portal view. It uses the same object kinds as turn tooling but without conversation-specific audience state.

**Data flow**: It receives manifests and optional stores/backends. It starts with core object kinds, adds extension object kinds with workspace-level contexts, adds core credential and extension object kinds, and returns the final object registry.

**Call relations**: Portal or member-read code can use this registry to list and read objects. It shares validation and core object construction with turn_tools through core_object_kinds.

*Call graph*: calls 1 internal fn (core_object_kinds); 3 external calls (__init__, context_for, object_registry).


##### `core_object_kinds`  (lines 511–538)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None) -> tuple[BoundKind, ...]
```

**Purpose**: Creates the built-in object kinds that summarize credentials and extensions. These let the system expose installed extension and credential-slot information through the same object interface as other resources.

**Data flow**: It receives manifests and an optional credential store. It creates a credential object kind from declared slots and live credential data, creates an extension object kind from active manifests, wraps both as unowned core BoundKind values, and returns them.

**Call relations**: turn_tools, member_object_registry, and validate_ext_tools call this whenever they assemble object registries that include core-provided objects.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 6 external calls (__init__, __init__, __init__, __init__, named_extensions, declared_slots).


##### `skill_registry`  (lines 541–564)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the set of loadable skills available in the deployment. Skills are reusable instruction or capability packages that agents can refer to by name.

**Data flow**: It starts with core skills, reads skill specifications from active manifests, discovers skill files from disk, checks for duplicate names, adds any generated skills, and returns a SkillRegistry.

**Call relations**: Startup code can call this after loading manifests so skill lookup and skill-index rendering see one unambiguous skill set.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 567–571)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent and its default capabilities.

**Data flow**: It receives manifests and flattens every manifest’s subagent profile list into one tuple in manifest order.

**Call relations**: Serve or turn setup code can feed this into a SubagentRegistry. Duplicate-name enforcement happens when that registry is constructed.


##### `durable_surfaces`  (lines 574–580)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Identifies surfaces whose replies should be delivered through a durable writeback path. A surface is an integration channel, and durable means the system stores work to be sent back later instead of relying only on the current request.

**Data flow**: It receives manifests, scans their surface declarations, keeps surfaces that define a post handler, and returns their names as a frozen set.

**Call relations**: Admission or conversation setup code can use this to know when to register writeback polling for turns entering those surfaces.


##### `turn_subagent_grants`  (lines 583–593)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Builds extra tool grants that extensions give to subagent profiles they may not own. This lets one extension widen a subagent’s available tools without editing that subagent’s original profile.

**Data flow**: It receives manifests, reads each subagent tool grant, unions tool names by target profile, and returns a mapping from profile name to frozen tool-name set.

**Call relations**: The turn loop can combine these grants with a profile’s own tool list, then intersect with the actual live tool registry so unknown profiles or missing tools do not break startup.


##### `turn_runtime_skills`  (lines 596–616)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Asks extensions for runtime-generated skills for the current agent and workspace. These are skills computed at run time instead of loaded from static files.

**Data flow**: It receives manifests plus credential, index, and embedding support. For each manifest with a runtime skill provider, it verifies a credential store exists, creates that extension’s context, awaits the provider, and collects the returned RuntimeSkill objects.

**Call relations**: Turn setup can call this when it needs skills that depend on live workspace or credential state. It creates contexts through context_for so providers run with the same scoped access pattern as tools.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 622–642)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and builds the configured index backend. An index backend is the component that stores and searches indexed text or vectors.

**Data flow**: It receives manifests, an optional configured backend name, and an optional credential store. It uses the configured name or default, searches manifest index declarations, checks credential requirements, creates the extension context, and returns the backend instance. If none matches, it raises NotRegisteredError.

**Call relations**: Startup configuration code calls this once to obtain the deploy’s index service. Extension factories receive a scoped context through context_for.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 645–665)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and builds the configured embedding client. An embedding client turns text into numeric representations that search systems can compare.

**Data flow**: It receives manifests, an optional configured name, and an optional credential store. It chooses the configured or default name, finds a matching embed declaration, verifies credential support if needed, creates the context, and returns the client. If none matches, it raises NotRegisteredError.

**Call relations**: Startup code uses this to wire the embedding service into indexing, memory, and extension contexts. Like index_backend, it relies on extension-registered factories.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 668–693)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider if an active extension registers it. Memory search is the feature that lets the system search past or stored knowledge.

**Data flow**: It receives manifests, optional credential/index/embed support, and a provider name. It finds matching memory search specs, rejects duplicate providers, checks credential requirements, builds the provider with an ExtensionContext, wraps it as MemorySearch, and returns it. If none exists, it returns null.

**Call relations**: Memory setup code can call this to attach an extension-provided search implementation. It follows the same context and fail-early rules used by other extension backends.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 696–732)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Checks extension tools and object kinds at boot before any user turn runs. This catches duplicate tool names, object registration conflicts, and missing credential setup early.

**Data flow**: It receives manifests and an optional credential store. It combines built-in tools with extension and connector tools, verifies credential requirements for tool-declaring extensions, builds an object registry including extension and core object kinds, adds object-verb tools, and constructs a ToolRegistry to force validation.

**Call relations**: Deployment startup can call this as a safety check. It uses core_object_kinds and object_registry in the same style as turn_tools, but without creating per-workspace extension contexts.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.__post_init__`  (lines 773–776)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that every hook in a HookChain belongs to the same audience as the chain. Audience means the intended visibility or conversation scope for the hook’s work.

**Data flow**: After the HookChain is created, it flattens all bound hooks and compares each hook context’s audience with the chain audience. If any differ, it raises an error.

**Call relations**: This runs automatically when turn_hooks creates a HookChain. It prevents later hook execution from mixing contexts meant for different audiences.


##### `HookChain.fire`  (lines 778–854)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one turn event and combines their results. Hooks can deny an action, change tool input or output, or add extra context, depending on which event is being fired.

**Data flow**: It receives an event, payload, optional turn and agent records, and speaker member ID. It finds hooks for the event, skips tool-specific hooks that do not apply, builds a HookContext for each, runs each handler with a timeout, validates allowed outcomes, and folds results from left to right. It returns a HookResolution describing denial, modified input/output, injected text, or failure-closed information.

**Call relations**: The turn engine calls this at lifecycle points such as before tool use or after tool use. Gating events fail closed if a hook crashes or times out, while observation-style events log failures and continue.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 857–901)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain used during a turn. It binds each declared turn-lifecycle hook to the ExtensionContext of the extension that declared it.

**Data flow**: It receives manifests, credential and backend support, optional turn tailer, audience, and public URL. It groups hook specs by supported turn event, checks that hook-declaring extensions have a credential store, creates contexts, wraps specs as BoundHook objects, and returns a HookChain.

**Call relations**: Turn setup calls this before the turn loop starts firing hook events. It hands HookChain.fire the ordered, context-bound hooks it needs to run safely.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 916–927)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Publishes a newly recorded connection to connection hooks. A connection here is an account or service link that has just landed successfully.

**Data flow**: It receives a ConnectionRecorded payload and runs each bound hook with a short timeout. It passes the payload and extension context to the handler. If a handler raises an error or times out, it logs the failure and continues without changing the recorded connection.

**Call relations**: The connect flow calls this after committing a connection. Unlike gating turn hooks, these are observe-only, so failures are swallowed and extension jobs can retry follow-up work later.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 930–956)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the hook chain for connection_recorded events. These hooks let extensions react when a user finishes connecting an external account or service.

**Data flow**: It receives manifests and optional credential, index, and embedding support. It finds hook specs for connection_recorded, checks credential store availability, creates each extension context, wraps the specs as BoundHook objects, and returns a ConnectionHookChain.

**Call relations**: Connection setup code calls this to prepare the chain that ConnectionHookChain.fire later publishes to when a connection lands.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### `core/src/ufo/ext/extension_kind.py`

`domain_logic` · `startup naming, then object list/get/status request handling`

This file turns the system’s active extension manifests into an object kind called `extension`. A manifest is the extension’s declaration: its name, version, tools, credential slots, jobs, hooks, and other things it contributes. The file makes those declarations visible in a safe way, like a public catalog card for each extension. It shows names and counts, but never secret values.

The first job is naming. Extension names may contain underscores, spaces, or other characters, but workspace object names must be lowercase and hyphenated. `named_extensions` converts manifest names into that shape, checks that each name is valid, and stops startup if two extensions would end up with the same object name.

`ExtensionSpec` is the readable shape of one extension: version plus the names of the things it contributes. `ExtensionObjects` is the read-only object handler. It can list extensions, fetch one extension’s full declaration, and report status such as whether the extension asks for sandbox internet access or depends on other extension “seams” (integration points). It refuses apply and delete operations because changing installed extensions is a deploy-level action through the lockfile. In other words, this file is for inspection, not modification.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: This function gives every active extension a safe workspace object name. It prevents confusing name clashes, such as two different manifest names both turning into the same hyphenated name.

**Data flow**: It receives the loaded extension manifests. For each manifest, it lowercases the manifest name, replaces runs of non-letter-or-number characters with hyphens, trims extra hyphens, and checks the result against the project’s object-name rules. It returns a dictionary from the safe object name to the original manifest, or raises an error if two manifests collide.

**Call relations**: This is used when the active manifest set is being prepared for exposure as objects. It relies on regular-expression replacement to render the name and on `validate_object_name` to enforce the same naming rules used by other workspace objects.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This function returns a pageable list of installed extensions, with a short summary for each one. It is what lets a user see, at a glance, which extensions are active and how much they contribute.

**Data flow**: It starts with the stored mapping of extension object names to manifests. It turns each manifest into an `ExtensionSpec`, then builds one row per extension containing the display name, version, tool count, and credential-slot count. It passes those rows plus the user’s list query into the paging helper, and returns the resulting page.

**Call relations**: When an object-list request reaches this object kind, this method builds the list view. It asks `_spec` to extract the readable declaration for each manifest, wraps each result in an `ObjectRow`, and hands the rows to `object_page` so filtering, ordering, or paging can be applied consistently with other object kinds.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: This function returns the full readable declaration for one extension. It is used when someone wants to inspect exactly what a specific extension adds to the deploy.

**Data flow**: It receives an object name and looks it up in the extension mapping. If there is no matching extension, it returns `None`. If it finds one, it converts the manifest into an `ExtensionSpec` and wraps it in an object detail result with no creation or update timestamps, because these are loaded declarations rather than stored rows.

**Call relations**: When a single-object read request reaches this object kind, this method performs the lookup. It delegates the manifest-to-readable-shape work to `_spec`, then creates an `ObjectDetail` so the result matches the object system’s normal detail format.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This function reports what an extension asks from the deploy environment. It shows operational needs, such as sandbox internet access and required integration points, without changing anything.

**Data flow**: It receives an extension object name and looks up the matching manifest. If none exists, it returns `None`. If found, it returns a small dictionary containing the manifest’s `sandbox_internet` setting and its `requires` list.

**Call relations**: This is used when the object system asks for the status side of an extension object rather than its spec. Unlike `get`, it does not build the whole extension declaration; it only returns the deploy-facing requirements recorded on the manifest.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function deliberately refuses create or update attempts for extension objects. It protects the rule that extensions are installed or removed through deploy tooling and the lockfile, not through object mutation.

**Data flow**: It receives the requested name, new spec, optional old spec, and generation check information, but it does not use them to change anything. Instead, it raises a `VerbNotSupported` error with a message explaining that extension installation is a deploy act.

**Call relations**: When the object system routes an apply-style mutation to this kind, this method stops the flow immediately. It creates the standard unsupported-verb error so callers get the same kind of refusal they would for other read-only object kinds.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function deliberately refuses delete attempts for extension objects. It prevents a user from uninstalling an extension through the workspace object interface.

**Data flow**: It receives the object name and generation check information, but it performs no lookup and makes no change. It raises a `VerbNotSupported` error that points the user back to deploy-level extension removal.

**Call relations**: When a delete request is sent to this object kind, this method is the guardrail. It produces the standard unsupported-verb error, keeping extension removal tied to the deploy lockfile rather than runtime object operations.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: This helper translates an extension manifest into the public `ExtensionSpec` shape. It gathers the names of the things users can encounter, while leaving out private values such as credential contents.

**Data flow**: It receives one manifest. It copies the manifest’s name and version, then collects tool names, object-kind names, credential-slot names, surface names, job names, hook event names, source backend names, and subagent profile names. It returns a new `ExtensionSpec` containing those names as tuples.

**Call relations**: This is the shared conversion step used by both list and get views. `ExtensionObjects.list` uses it to make summaries and counts for every extension, while `ExtensionObjects.get` uses it to render the full declaration for one extension.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-model-catalog` — The shared catalog of AI models, providers, routing rules, reasoning modes, key lookup rules, and usage shapes.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-connector-tool-catalog` — The discovered connector actions from systems like Gmail, Slack, GitHub, Composio, Pipedream, and MCP servers.
- `reg-memory-store` — The durable remembered facts and notes that agents can search, browse, update, consolidate, and show with provenance.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-extension-kv-store` — Private per-workspace JSON/key-value state owned by extensions for setup, feature bookkeeping, and small durable extension data that is not a user-visible object.
- `reg-ephemeral-cache-bus` — The selected Redis/cache/pub-sub backend and its ephemeral keys, locks, and connection state used to coordinate live delivery, workers, and shared runtime services.
- `reg-http-route-map` — The process-wide web application routing state, including mounted core routes, extension routes, middleware, static assets, and ingress handlers used to dispatch incoming requests.
- `reg-skill-workflow-catalog` — The registered agent skills, helper subagent profiles, workflow profiles, and related prompt/activity metadata injected into turns and surfaced to users.
