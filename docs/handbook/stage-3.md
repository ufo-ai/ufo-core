# Extension, app, provider, and capability registration  `stage-3`

This stage happens during startup. It is the system’s sign-in desk for capabilities: it finds what is installed, checks what is allowed, and records what the rest of UFO can use. The extension loader is the front door. It reads extension declarations and turns them into usable tools, hooks, skills, object types, credentials, web surfaces, backends, and background jobs. The skills runtime explains what a skill is, reads skill folders, lists available skills, and loads selected skills into an agent’s safe work area, called a sandbox.

The built-in workspace registration adds core apps such as Chat, Code, Issues, Wiki, Metrics, Radar, Meetings, Artifacts, and writing or briefing skill bundles. Backend provider registration adds outside services such as AI model providers, search, embeddings, connectors, feature flags, and Redis communication support.

The many extension manifests are registration cards for specific abilities: browser use, coding, research, sites, connectors, gbrain sources, iMessage, Slack, web portals, memory, objectives, monitors, report digests, scheduled tasks, debugging, and self-improvement. The sample extension acts like a fake practice shop so the whole registration system can be tested without real services.

## Sub-stages

- [Built-in workspace app and skill bundle registration](stage-3.1.md) `stage-3.1` — 12 files
- [Backend provider and external service registration](stage-3.2.md) `stage-3.2` — 9 files

## Files in this stage

### Extension loading foundations
Core startup code discovers extensions, validates what may run, registers skills, and demonstrates the full extension contract with a sample pack.

### `core/src/ufo/host/ext/loader.py`

`orchestration` · `startup and per-turn setup, with hook chains active during turns and connection recording`

Extensions are how UFO grows new abilities without hard-coding every feature into the core program. This file acts like a careful customs desk at startup: it discovers extension packages, reads their manifests, rejects duplicates or unsafe privilege claims, and, when a lockfile is present, verifies that the installed code exactly matches the pinned version. Without this, a deployment could silently run the wrong extension code, load two things with the same name, or expose tools and credentials in inconsistent ways.

After discovery, the file converts the active manifests into practical runtime pieces. It builds the tool list for a turn, creates scoped extension contexts so an extension can only see the resources it is allowed to use, registers object kinds and actions, gathers migration folders, exposes skill providers, selects index and embedding backends, and wires event hooks. A hook is a callback that can react to moments such as “before a tool runs” or “after a connection is recorded.”

The file is intentionally strict at the edges. Many errors fail loudly during boot rather than later during a user turn. That makes extension loading more like checking a bridge before traffic crosses it: if names collide, credentials are missing, digests drift, or a hook returns an invalid result, the system either refuses to start or safely limits the damage.

#### Function details

##### `lockfile_path`  (lines 175–176)

```
def lockfile_path() -> Path
```

**Purpose**: Finds which lockfile path the system should use. Operators can override the default path with an environment variable.

**Data flow**: It reads the process environment for the lockfile setting. If that setting is present, it turns it into a filesystem path; otherwise it uses the default `ufo.lock` path. It returns that path without reading the file.

**Call relations**: When `load_manifests` decides whether the deployment is pinned or in development mode, it asks this function where to look for the lockfile.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 179–180)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads a lockfile from disk and validates it as a `Lockfile` object. This is how the loader learns which extensions are pinned.

**Data flow**: It receives a path, reads the text stored there, parses the JSON, and validates that the fields match the lockfile shape. It returns a typed lockfile object ready for later checks.

**Call relations**: `load_manifests` calls this after it finds a lockfile, then uses the returned pins to decide exactly which extensions may run.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 183–184)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a validated lockfile object back to disk as readable JSON. Tools that pin or bundle extensions can use this to produce the file that startup later trusts.

**Data flow**: It receives a path and a lockfile object. It converts the object to formatted JSON, adds a final newline, and writes that text to disk. It does not return a value.

**Call relations**: This is the write-side partner of `read_lockfile`: command-line tooling can create the lockfile, and `load_manifests` later reads the same format.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 187–201)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension advertised through Python entry points. It also rejects duplicate extension names and blocks third-party extensions from declaring privileged member-context access.

**Data flow**: It asks Python’s package metadata for all `ufo.extension` entry points. For each one, it loads and calls the advertised zero-argument function to get a manifest, checks safety rules, and stores the manifest together with the entry point under the manifest name. It returns a name-to-manifest map.

**Call relations**: `load_manifests` uses this as the raw installed-extension list, and `migration_locations` uses it to locate extension source packages for database migrations.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 204–215)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds installed packs, which are named bundles of extensions and pack-level contributions such as skills or onboarding steps. It rejects two packs claiming the same name.

**Data flow**: It reads the `ufo.pack` entry points, loads each callable, calls it to get a pack object, and stores the pack by name. It returns the complete installed pack map.

**Call relations**: `_pack_manifests` calls this when configuration selects a pack, so it can narrow the active manifests to the pack’s bundled set.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 218–223)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Locates the import information for the top-level Python package that owns an extension entry point. This is needed to find source files and migration directories.

**Data flow**: It receives an entry point, takes the top package name from its module path, and asks Python’s import system where that package lives. If the package cannot be found or has no source location, it raises an error; otherwise it returns the module specification.

**Call relations**: `extension_digest` uses this to know which files to hash, and `migration_locations` uses it to find the package directory that may contain migrations.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 226–231)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Returns the directory that should contain an extension’s optional `migrations` folder. It works for both package-style and single-file extensions.

**Data flow**: It receives a module specification. If the extension is a package, it returns the package directory; if it is a single module file, it returns that file’s parent directory.

**Call relations**: `migration_locations` calls this after `_entry_spec` so it can check whether the extension ships database migration files.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 234–250)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes a stable fingerprint of an installed extension’s source code. This lets a lockfile detect tampering or accidental code drift.

**Data flow**: It receives an entry point, finds the owning package or module, reads all relevant source files while skipping bytecode cache files, and passes the file names and bytes to `extension_content_digest`. It returns a `sha256:` digest string.

**Call relations**: `load_manifests` calls this for each pinned extension and compares the result with the lockfile’s stored digest before allowing the extension to run.

*Call graph*: calls 2 internal fn (_entry_spec, extension_content_digest); called by 1 (load_manifests); 1 external calls (Path).


##### `extension_content_digest`  (lines 253–259)

```
def extension_content_digest(files: Mapping[str, bytes]) -> str
```

**Purpose**: Hashes a set of named files in a deterministic way. Both file names and file contents affect the result.

**Data flow**: It receives a mapping from relative file names to file bytes. It processes the names in sorted order, hashes each name and each content blob, folds those into one SHA-256 hash, and returns the final digest string with the expected prefix.

**Call relations**: `extension_digest` prepares the installed extension files and hands them to this function so the lockfile check has one compact fingerprint to compare.

*Call graph*: called by 1 (extension_digest); 1 external calls (sha256).


##### `migration_locations`  (lines 262–279)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Finds database migration folders contributed by the currently active extensions. Migrations are versioned database changes that let extensions add their own tables safely.

**Data flow**: It discovers installed extensions, loads the active manifests, matches active manifests back to their entry points, and checks each package for a `migrations` directory. It returns the existing migration directory paths as strings.

**Call relations**: Database migration code can call this to layer extension migrations on top of core migrations. It relies on `load_manifests` so inactive installed extensions do not change the database.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 282–305)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Decides which extension manifests are active for this run. It enforces the lockfile when present, or loads all discovered extensions in development mode when no lockfile exists.

**Data flow**: It discovers installed extensions and checks the lockfile path. If no lockfile exists, every discovered manifest becomes active. If a lockfile exists, each pinned extension must be installed and its digest must match. If a pack name is supplied, the active set is narrowed through `_pack_manifests`. It returns the active manifests as a tuple.

**Call relations**: This is the central source of truth for extension activation. Other builders, such as `migration_locations`, use its result so tools, jobs, routes, migrations, and hooks all see the same extension set.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 308–341)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Builds the manifest list for one selected pack. A pack is treated as a coherent bundle of extensions plus its own pack-level contributions.

**Data flow**: It receives a pack name and the already-active extension map. It finds the installed pack, verifies every extension named by the pack is active, rejects name collisions, then creates a synthetic manifest for the pack’s own skills and onboarding data. It returns bundled extension manifests followed by the pack manifest.

**Call relations**: `load_manifests` calls this only when configuration selects a pack. This keeps pack selection as a narrowing step after normal lockfile safety checks.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 344–353)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These describe environment variables used to pass grant sentinels into sandboxed tool runs.

**Data flow**: It receives active manifests, walks their connectors, and keeps only connectors that declare a CLI credential. It returns a map from OAuth provider name to the CLI credential declaration.

**Call relations**: `injecting_slots` uses this map while checking that no sandbox environment variable is claimed for two different meanings.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 356–429)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into outbound traffic or sandbox environments, and checks for dangerous naming conflicts. A credential slot is a named place where a secret, such as an API key, can be stored and later used.

**Data flow**: It receives manifests, extracts slots with injection rules, gathers all declared slot names, and compares exported environment variables, sentinels, host choices, and metering dimensions. If two declarations would silently overwrite each other or point to an impossible host choice, it raises an error. Otherwise it returns the injectable slots.

**Call relations**: It calls `connector_clis` so connector CLI variables and slot variables share one conflict check. The proxy and sandbox setup can then trust that the returned declarations will not quietly mis-authenticate requests.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 432–526)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, blob: WorkspaceBlobStore | None=None, *, audi
```

**Purpose**: Builds the complete set of tools available during one turn, including built-in tools, extension tools, connector tools, and object-action tools. It also builds the extension contexts needed to run extension-owned tools safely.

**Data flow**: It receives active manifests plus optional credential, index, embedding, blob, audience, and link settings. It starts with built-in tools and core object kinds, then for each manifest creates an extension context if the manifest contributes tools or objects. Plain tools are added to the tool list; tools bound to object kinds become actions; object kinds are registered. It returns the final tool definitions, a map from extension tool name to its context, and the object verb helper.

**Call relations**: Turn execution calls this before dispatching tools. It hands object declarations to `object_registry` and `action_registry`, adds core object kinds through `core_object_kinds`, and returns structures the engine uses to route calls to either core code or the right extension context.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `member_object_registry`  (lines 538–603)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object kinds and actions visible to member-facing reads outside a live turn, such as a portal page. It mirrors the turn registry but without a conversation-scoped audience.

**Data flow**: It receives active manifests and optional stores/backends. It starts with core object kinds and built-in actions, creates workspace-level extension contexts for manifests that contribute objects or bound actions, registers those kinds and actions, and adds core extension-derived object kinds. It returns a `MemberObjectRegistry` containing the kind and action maps.

**Call relations**: Portal and management surfaces use this registry to render rows and controls. It shares validation paths with `turn_tools` by using the same object and action registry builders plus `core_object_kinds`.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, context_for, action_registry, object_registry).


##### `frame_admissible`  (lines 606–620)

```
def frame_admissible(manifests: tuple[Manifest, ...], registry: MemberObjectRegistry) -> frozenset[str]
```

**Purpose**: Computes which tools and object actions an embedded frame is allowed to call. This is a security boundary for app pages posting messages back into UFO.

**Data flow**: It receives active manifests and a member object registry. It gathers built-in tools and declared extension tools, then asks the object-view helper to extract only callables marked as usable from frames. It returns their allowed identifiers as a frozen set.

**Call relations**: The frame-originated action path can check this set before accepting a posted call. It depends on the action registry already built by `member_object_registry`.

*Call graph*: 1 external calls (frame_admissible_ids).


##### `core_object_kinds`  (lines 623–670)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, *, public_base_url: str | None=None, artifact_token_secret: str='') -> tuple[BoundKind, ...]
```

**Purpose**: Creates core-owned object kinds that depend on the active extension set: credentials, extensions, surfaces, and artifacts. These let users inspect extension-related state through the same object system as other resources.

**Data flow**: It receives manifests plus optional credential and link settings. It builds object definitions for credential slots, installed extensions, registered surfaces, and artifacts, each with the store needed to list or read them. It wraps each as a bound kind with no extension context and returns them.

**Call relations**: `turn_tools`, `member_object_registry`, and `validate_ext_tools` all call this so the runtime, portal, and boot validation agree on the core object kinds exposed for the current deployment.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 9 external calls (__init__, __init__, __init__, __init__, __init__, named_extensions, declared_slots, artifact_object, registered_surfaces).


##### `skill_registry`  (lines 673–697)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the deploy-wide registry of loadable skills. A skill is reusable instruction or capability material that can be loaded by name.

**Data flow**: It starts with core skills, then walks active manifests and discovers skills from each declared path on disk. If an extension or generated skill reuses an existing name, it raises an error. It returns a `SkillRegistry` containing all accepted skills and remembering which ones were bundled rather than generated.

**Call relations**: Startup code can use this once to make skill loading and skill-index rendering simple. It delegates disk parsing to the skill discovery helper.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 700–704)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized assistant role that can be used during turns.

**Data flow**: It receives manifests and flattens all manifest subagent profiles in manifest order. It returns them as a tuple without changing them.

**Call relations**: The serving layer can feed this result into the subagent registry, where duplicate profile names are rejected rather than silently shadowed.


##### `durable_surfaces`  (lines 707–713)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds surfaces whose replies are delivered through durable writeback. A surface is durable here when it declares a `post` handler.

**Data flow**: It receives manifests, scans all declared surfaces, keeps those with a post handler, and returns their names as a frozen set.

**Call relations**: Admission or turn setup can use this set to know when to register writeback work for conversations entering those surfaces.


##### `turn_subagent_grants`  (lines 716–726)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Builds the extra tool grants that extensions give to subagent profiles they do not own. This lets one extension widen a profile’s usable tools without editing that profile.

**Data flow**: It receives manifests, walks each manifest’s subagent tool grants, unions tool names by target profile, and returns a map from profile name to frozen tool-name set.

**Call relations**: The turn loop can fold this map into a subagent profile’s own tool list, then intersect with the live tool set so missing tools or profiles do not break the turn.


##### `turn_member_skills`  (lines 729–771)

```
async def turn_member_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, agent_name: str) -> tuple[tu
```

**Purpose**: Builds the member-skill cards available to a specific agent and returns a loader for those skills. Member skills are saved or workspace-provided skills contributed by extensions.

**Data flow**: It receives manifests, optional backend resources, and the agent name. For each provider, it requires a credential store, creates an extension context, asks the provider for cards, filters out cards not targeted at this agent, and keeps the first provider for each skill name while logging later collisions. It returns the visible cards and an async materializer function.

**Call relations**: Turn setup uses this to show or route saved member skills for the current agent. The nested `turn_member_skills.materialize` function later uses the captured provider map to load one selected skill.

*Call graph*: 2 external calls (context_for, log).


##### `turn_member_skills.materialize`  (lines 764–769)

```
async def materialize(name: str) -> RuntimeSkill | None
```

**Purpose**: Loads one member skill by name using the provider selected earlier by `turn_member_skills`. If no provider claimed the name, it reports that nothing was found.

**Data flow**: It receives a skill name and reads the provider map captured from the outer function. If the name is absent, it returns `None`; otherwise it calls that provider’s materialization method with the saved extension context and returns the runtime skill.

**Call relations**: This function is returned by `turn_member_skills` as the turn’s skill loader. It is intentionally tied to the filtered, collision-resolved card set built by the outer function.


##### `member_skill_listing`  (lines 774–800)

```
async def member_skill_listing(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Materializes all member skills from all active providers for a management listing. Unlike turn-specific loading, it does not filter by agent.

**Data flow**: It receives manifests and optional backend resources. For each member-skill provider, it requires a credential store, creates an extension context, asks the provider to materialize all skills, keeps the first skill for each name, and logs duplicates. It returns the collected runtime skills.

**Call relations**: Portal or management pages use this to show the workspace’s full member-skill inventory. It follows the same credential and collision policy as `turn_member_skills`.

*Call graph*: 2 external calls (context_for, log).


##### `index_backend`  (lines 806–826)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and builds the configured index backend. An index backend stores and searches indexed text or vectors for the workspace.

**Data flow**: It receives manifests, an optional configured backend name, and an optional credential store. It uses the configured name or the default name, searches extension index declarations for a match, checks whether credentials are required, creates a minimal extension context, and calls the backend factory. If no extension registers the name, it raises `NotRegisteredError`.

**Call relations**: Startup wiring calls this to create the index service that later gets passed into extension contexts, memory tools, and jobs.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 829–849)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and builds the configured embedding backend. An embedding backend turns text into numeric vectors for semantic search.

**Data flow**: It receives manifests, an optional configured backend name, and an optional credential store. It chooses the configured or default name, finds a matching extension declaration, checks credential availability, creates an extension context, and calls the factory. If none is registered, it raises `NotRegisteredError`.

**Call relations**: Startup code uses this result wherever embeddings are needed, including indexing, memory search, and extension contexts.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 852–877)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory-search provider if an active extension registers it. Memory search is the feature that retrieves relevant prior information.

**Data flow**: It receives manifests, credentials, optional index and embedding clients, and a provider name. It gathers matching providers, returns `None` if there are none, rejects duplicates, checks credential availability, builds an extension context, and wraps the provider’s built implementation in `MemorySearch`.

**Call relations**: Higher-level memory features can ask this for the configured provider. It uses the same scoped context pattern as tools and backends.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 880–928)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Performs boot-time validation of extension tools, object kinds, and bound actions without building per-workspace contexts. It catches misconfiguration before the service appears healthy.

**Data flow**: It receives manifests and an optional credential store. It gathers built-in and extension tools, turns bound tools into actions, checks credential requirements, builds an object registry including core extension-derived kinds, validates actions, adds object-verb tools, and constructs a `ToolRegistry` to force tool-name collision checks. It returns the validated action registry.

**Call relations**: Deployment startup can call this as an early safety check. It shares registry construction with `turn_tools` through `core_object_kinds`, but keeps contexts empty because no workspace is involved yet.

*Call graph*: calls 1 internal fn (core_object_kinds); 6 external calls (__init__, __init__, __init__, __init__, action_registry, object_registry).


##### `HookChain.__post_init__`  (lines 969–972)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that every hook in a hook chain was created for the same audience as the chain itself. An audience is the group or conversation scope the hook is allowed to see.

**Data flow**: After the dataclass is created, it flattens all bound hooks and compares each hook context’s audience with the chain audience. If any differ, it raises an error; otherwise construction succeeds unchanged.

**Call relations**: This runs automatically when `turn_hooks` creates a `HookChain`, preventing a hook from being fired in the wrong audience context.


##### `HookChain.fire`  (lines 974–1052)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all turn-lifecycle hooks for one event and combines their results. Hooks can deny an action, modify tool input or output, or inject extra context depending on the event.

**Data flow**: It receives an event, payload, optional turn and agent records, and an optional speaker member id. It selects hooks for the event, skips tool-specific hooks that do not match the current call, gives each hook the latest folded payload inside a scoped hook context, enforces a timeout, validates the hook’s returned outcome, and updates the running result. It returns a `HookResolution` describing denial, modified input/output, injected text, or a fail-closed error.

**Call relations**: The turn engine calls this at moments such as before tool use or after tool use. It uses strict fail-closed behavior for gating events unless the hook is marked best-effort, while non-gating hook failures are logged and swallowed so observation hooks cannot break the turn.

*Call graph*: 5 external calls (__init__, __init__, __init__, timeout, replace).


##### `turn_hooks`  (lines 1055–1099)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain used during a turn. It binds every active extension’s turn-related hooks to that extension’s workspace-scoped context.

**Data flow**: It receives manifests, credentials, optional backends and tailer, plus audience and URL settings. For each manifest with hooks, it requires a credential store, creates an extension context, keeps only turn-lifecycle hook events, wraps each as a bound hook, groups them by event, and returns a `HookChain`.

**Call relations**: Turn setup calls this before the turn runs. The returned `HookChain.fire` method is then used by the engine at each supported event point.

*Call graph*: 3 external calls (__init__, __init__, context_for).


##### `ConnectionHookChain.fire`  (lines 1114–1125)

```
async def fire(self, connection: ConnectionRecorded) -> None
```

**Purpose**: Runs hooks that react after a connection has been recorded, such as an extension creating follow-up resources for a newly connected account. These hooks observe the event but cannot undo the recorded connection.

**Data flow**: It receives a `ConnectionRecorded` payload. For each bound hook, it builds a hook context, runs the handler with a timeout, and logs any exception instead of raising it. It returns nothing.

**Call relations**: The connection flow calls this after committing a connection. Because failures are swallowed, the connection remains recorded and an extension’s later job can retry unfinished work.

*Call graph*: 3 external calls (__init__, timeout, log).


##### `connection_hooks`  (lines 1128–1154)

```
def connection_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> ConnectionHookChain
```

**Purpose**: Builds the chain of hooks that should fire when a connection is recorded. It binds each relevant hook to the same kind of extension context used by extension jobs.

**Data flow**: It receives manifests, an optional credential store, and optional backends. It selects hooks whose event is `connection_recorded`, requires credentials when hooks exist, creates an extension context for each contributing manifest, wraps the hooks, and returns a `ConnectionHookChain`.

**Call relations**: Connection setup calls this so the connect flow has one object to publish landed connections to. The returned chain later delegates actual execution to `ConnectionHookChain.fire`.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### `core/src/ufo/runtime/skills/runtime.py`

`domain_logic` · `startup, skill selection, request handling, sandbox preparation`

A skill is a small package of instructions and files. Its main file, SKILL.md, contains a short machine-readable header plus the human-readable workflow the agent should follow. This file is the bridge between those folders on disk, saved member-created skills, and the agent’s live runtime.

It first knows how to parse a skill: it checks that SKILL.md exists, reads its YAML frontmatter (a structured header), separates the instruction body, gathers extra asset files, and discovers nested child skills. It then represents skills in two forms: a full RuntimeSkill with instructions and files, and a smaller SkillCard used for search and dependency planning without reading every file.

The SkillRegistry is the central catalogue. It combines built-in deploy skills, generated skills, and member-saved skills. When an agent asks for a skill, the registry computes the full “closure,” meaning the requested skill plus every dependency it names, without loading duplicates or looping forever on cycles.

Finally, this file turns loaded skills into two things: text for the model’s context, and files installed under $UFO_HOME/skills in the sandbox. It also remembers which workflows are already in context, so repeated loads do not waste space by injecting the same instructions again.

#### Function details

##### `skill_root`  (lines 51–53)

```
def skill_root(name: str) -> str
```

**Purpose**: Builds the stable runtime folder path for a skill. This gives every skill a predictable home under $UFO_HOME/skills.

**Data flow**: It receives a skill name, joins it onto the skills root string, and returns a path-like string such as $UFO_HOME/skills/name. It does not read or change anything.

**Call relations**: RuntimeSkill.root calls this when another part of the system needs to know where a skill’s files will appear inside the sandbox.

*Call graph*: called by 1 (root).


##### `RuntimeSkill.all_files`  (lines 89–90)

```
def all_files(self) -> dict[str, bytes]
```

**Purpose**: Collects everything that belongs to a skill as files, including SKILL.md and any bundled assets. This is useful when the system needs to hash, package, or send the full skill.

**Data flow**: It reads the skill’s stored raw SKILL.md text and its asset file list, turns SKILL.md into bytes, and returns one dictionary from file path to file bytes.

**Call relations**: RuntimeSkill.content_digest uses it to make a stable fingerprint of the skill, and _wire_skill uses it to prepare the files for transfer into the sandbox.

*Call graph*: called by 2 (content_digest, _wire_skill).


##### `RuntimeSkill.root`  (lines 92–93)

```
def root(self) -> str
```

**Purpose**: Returns the sandbox folder where this skill should be installed. It hides the exact path-building rule behind a simple method.

**Data flow**: It reads the RuntimeSkill name, passes that name to skill_root, and returns the resulting $UFO_HOME/skills/... path.

**Call relations**: It is called by _wire_skill while preparing a skill’s files for sandbox loading, so file paths can be checked and made relative to the correct skill root.

*Call graph*: calls 1 internal fn (skill_root); called by 1 (_wire_skill).


##### `RuntimeSkill.card`  (lines 95–103)

```
def card(self) -> SkillCard
```

**Purpose**: Creates the lightweight routing version of a skill. This card contains enough information to search for and resolve dependencies without carrying the full instruction text and files.

**Data flow**: It reads the skill’s name, description, dependency list, and agent targeting list, then returns a SkillCard with those fields.

**Call relations**: The registry uses these cards when building indexes and dependency closures, especially so deploy skills and member-saved skills can be treated in the same shape.

*Call graph*: 1 external calls (__init__).


##### `RuntimeSkill.content_digest`  (lines 105–111)

```
def content_digest(self) -> str
```

**Purpose**: Computes a stable fingerprint for a skill’s full contents. If any file name or file content changes, the digest changes too.

**Data flow**: It gathers all files, sorts them by path, hashes each path and each file’s bytes, combines those hashes, and returns a sha256-prefixed digest string.

**Call relations**: SystemSkillBundle.from_skills uses the same idea when packaging deploy skills, and _wire_skill sends this digest along with files so caches and sandbox images can recognize exact content.

*Call graph*: calls 1 internal fn (all_files); called by 1 (_wire_skill); 1 external calls (sha256).


##### `SystemSkillBundle.from_skills`  (lines 123–148)

```
def from_skills(cls, skills: Iterable[RuntimeSkill]) -> 'SystemSkillBundle'
```

**Purpose**: Builds an immutable archive of deploy-time system skills. This lets the server, terminal cache, and sandbox share one exact bundle of built-in skill files.

**Data flow**: It receives RuntimeSkill objects, rejects conflicting duplicate names, records each skill’s digest and file list in a manifest, writes all files into a deterministic ZIP archive, and returns a SystemSkillBundle containing the bundle digest, archive bytes, and manifest bytes.

**Call relations**: Startup and serving code call this when preparing the shared system skill surface, so later sandbox loads can refer to known bundled skills by digest instead of resending every file.

*Call graph*: called by 4 (init_runtime, _mount_shared_surfaces, run, system_skill_bundle); 4 external calls (sha256, BytesIO, dumps, ZipFile).


##### `SystemSkillBundle._write`  (lines 151–154)

```
def _write(archive: zipfile.ZipFile, path: str, content: bytes) -> None
```

**Purpose**: Writes one file into the system skill ZIP archive in a repeatable way. Repeatability matters because the same inputs should produce the same bundle bytes.

**Data flow**: It receives an open ZIP archive, a path, and file bytes. It creates a ZIP entry with a fixed timestamp and normal file permissions, then writes the content into the archive.

**Call relations**: SystemSkillBundle.from_skills uses this helper for the manifest and for every skill file while constructing the bundle.

*Call graph*: 2 external calls (writestr, ZipInfo).


##### `LoadedSkill.prompt_body`  (lines 167–177)

```
def prompt_body(self) -> str
```

**Purpose**: Turns one loaded skill into the text that will be shown to the model. It clearly labels whether the agent asked for the skill or it arrived as a dependency.

**Data flow**: It reads the skill name, dependency source, and instruction body. It returns a markdown block with a header and the skill’s workflow instructions, but not the metadata or bundled file contents.

**Call relations**: loaded_context uses this for each newly injected skill when building the full context text for a load.


##### `LoadedSkills.reseed`  (lines 203–222)

```
def reseed(self, loads: Iterable[tuple[LoadedRef, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the memory of which skill workflows are already in the model’s context. This prevents repeated loads from pasting the same instructions again.

**Data flow**: It clears the current tracking sets, reads resolved load entries and optional preloaded skills, adds every present skill name to in_context, and records only directly requested skills in asked_for.

**Call relations**: It calls reset first, then is used around conversation-window reconstruction so the tracker reflects what the model actually sees, including preloaded subagent skills.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 224–229)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the skills the agent explicitly asked for and clears the tracker. This is used when a boundary, such as compaction, drops detailed workflow bodies but needs to remember what to reload later.

**Data flow**: It sorts the asked_for names into a tuple, clears both tracking sets, and returns the saved names.

**Call relations**: It calls reset after taking the names, so the next context window starts with a clean record.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 231–233)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered loaded-skill state. It is the shared clean-up step for reseeding and draining.

**Data flow**: It mutates the LoadedSkills object by emptying both in_context and asked_for sets. It returns nothing.

**Call relations**: LoadedSkills.reseed calls it before rebuilding state, and LoadedSkills.drain calls it after extracting the names to carry forward.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 236–242)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates the structured header of SKILL.md from its instruction body. It enforces the expected frontmatter fence format so malformed skills fail early.

**Data flow**: It receives the raw SKILL.md text, checks that it starts with the --- fence, finds the closing fence, and returns the metadata text and the remaining body text. If the fences are missing, it raises an error.

**Call relations**: parse_skill_content calls this before interpreting the YAML metadata and creating a RuntimeSkill.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 245–250)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate child skill folders inside a skill directory. A child skill is a subfolder that has its own SKILL.md.

**Data flow**: It receives a directory path, looks through its direct children, keeps only directories containing SKILL.md, sorts them, and returns that list.

**Call relations**: parse_skill uses it to avoid mixing child skill files into the parent, and discover_skills uses it to recursively register nested skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 253–290)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Parses a skill from an in-memory set of files. This is used both for disk-loaded skills and for member skills stored as bytes elsewhere.

**Data flow**: It receives a claimed directory name, a map of file paths to bytes, and optional registry naming information. It decodes SKILL.md, splits and reads the YAML frontmatter, validates the name and agent list, gathers asset files, and returns a RuntimeSkill. Missing or malformed data raises an error.

**Call relations**: parse_skill gathers files from disk and then hands them here, so all skill validation follows the same rules no matter where the files came from.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 293–302)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill directory from disk and turns it into a RuntimeSkill. It keeps child skill subtrees separate from the parent’s asset files.

**Data flow**: It receives a directory path, finds child skill folders, reads all ordinary files outside those child subtrees, and passes the collected bytes to parse_skill_content. It returns the parsed RuntimeSkill.

**Call relations**: discover_skills calls this for each skill folder before walking any child skill folders.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 305–323)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, then flattens them into a name-to-skill map. Child skills get path-like names such as parent/child.

**Data flow**: It parses the current directory as a skill, stores it under its registry name, finds immediate child skill directories, recursively discovers each child, and returns one combined dictionary.

**Call relations**: _load_core_skills calls this for each built-in skill directory during module startup, creating the built-in skill catalogue.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 326–333)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the project’s built-in skills from the directory beside this file. These are the fixed core skills available at deploy time.

**Data flow**: It receives a root path, lists visible skill directories, discovers each skill tree, merges the results into one dictionary, and returns it.

**Call relations**: The module calls this during import to create CORE_SKILLS_BY_NAME and the core SkillRegistry used as the base registry.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.__post_init__`  (lines 360–362)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the default set of bundled skill names after a registry is created. If no explicit bundle list is given, every deploy skill is treated as bundled.

**Data flow**: It checks whether bundled_names is missing. If so, it sets it to all current deploy skill names without otherwise changing the registry.

**Call relations**: This runs automatically when SkillRegistry objects are created, including the core registry and registries made by merging or adding member skills.


##### `SkillRegistry.named`  (lines 364–368)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a deploy skill by name and gives a helpful error if it is unknown. This is for callers that need the full RuntimeSkill, not just a card.

**Data flow**: It receives a name, checks the deploy-skill dictionary, and returns the matching RuntimeSkill. If absent, it raises an error built by _unknown.

**Call relations**: It relies on _unknown to include close-name suggestions, making mistakes easier to diagnose.

*Call graph*: calls 1 internal fn (_unknown).


##### `SkillRegistry._unknown`  (lines 370–373)

```
def _unknown(self, name: str) -> ValueError
```

**Purpose**: Creates a clear error message for an unknown skill name. It suggests nearby known names when possible.

**Data flow**: It receives the bad name, gets all known names, finds close text matches, and returns a ValueError with the name and optional suggestions.

**Call relations**: SkillRegistry.named and SkillRegistry._card use this whenever a requested skill cannot be found.

*Call graph*: calls 1 internal fn (known_names); called by 2 (_card, named); 1 external calls (get_close_matches).


##### `SkillRegistry._card`  (lines 375–382)

```
def _card(self, name: str) -> SkillCard
```

**Purpose**: Finds the routing card for a skill name, whether it is a deploy skill or a member-saved skill. This gives dependency resolution one common view of both tiers.

**Data flow**: It receives a name, first checks deploy skills and turns one into a card, then checks member cards. If neither exists, it raises the unknown-skill error.

**Call relations**: SkillRegistry.closure and its inner dependency walker call this whenever they need to expand a requested name or dependency name.

*Call graph*: calls 1 internal fn (_unknown); called by 2 (closure, add).


##### `SkillRegistry.known_names`  (lines 384–387)

```
def known_names(self) -> frozenset[str]
```

**Purpose**: Returns every skill name that this registry can resolve. This includes both deploy skills and member-saved skill cards.

**Data flow**: It reads the keys from the deploy dictionary and the member-card dictionary, combines them into one frozen set, and returns it.

**Call relations**: _unknown uses this set to suggest close matches, and save paths can use the same idea to check whether a new skill name is claimable.

*Call graph*: called by 1 (_unknown).


##### `SkillRegistry.all_cards`  (lines 389–394)

```
def all_cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: Returns all loadable skills in the lightweight card form. This is the view used for skill search and selection.

**Data flow**: It converts each deploy RuntimeSkill to a SkillCard, appends member SkillCards, and returns them as one tuple.

**Call relations**: Search code can score these cards without reading full member skill bodies or bundled files.


##### `SkillRegistry.bundled_skills`  (lines 396–399)

```
def bundled_skills(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Returns the deploy skills that are part of the static bundle. These are the skills that can be shared through the terminal archive or sandbox image.

**Data flow**: It reads the bundled name set, filters the deploy-skill dictionary to those names, and returns the matching RuntimeSkill objects.

**Call relations**: Serving code calls this when mounting shared surfaces, so only intended bundled skills are exposed as system skill content.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.closure`  (lines 401–425)

```
def closure(self, *names: str) -> tuple[LoadedRef, ...]
```

**Purpose**: Computes the full set of skills needed for a load: the requested skills first, then their dependencies, each only once. This is like making a packing list before opening any boxes.

**Data flow**: It receives one or more requested names, creates direct LoadedRef entries for them, walks each dependency list through cards, skips names already seen, and returns the ordered tuple of LoadedRef objects.

**Call relations**: The loop engine calls this while planning loaded skill closures. It uses _card for each name, and its inner add helper performs the recursive dependency walk safely.

*Call graph*: calls 1 internal fn (_card); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 415–420)

```
def add(card: SkillCard, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency and its own dependencies to a closure, unless that skill is already included. This prevents duplicate entries and avoids infinite recursion in dependency cycles.

**Data flow**: It receives a SkillCard and the name of the skill that pulled it. It records a LoadedRef if the card is new, then looks up and adds each dependency in turn.

**Call relations**: This helper lives inside SkillRegistry.closure and is called while expanding dependency chains after the directly requested skills have been seeded.

*Call graph*: calls 1 internal fn (_card); 1 external calls (__init__).


##### `SkillRegistry.materialize`  (lines 427–449)

```
async def materialize(self, refs: Sequence[LoadedRef]) -> tuple[LoadedSkill, ...]
```

**Purpose**: Turns planned skill references into full loaded skills with instructions and files. This is where member-saved skills are actually read.

**Data flow**: It receives LoadedRef objects, looks up deploy skills directly, asks the materializer callback for member skills when needed, validates that the returned skill matches the requested name, and returns LoadedSkill objects with dependency and bundled flags.

**Call relations**: After closure decides what names are needed, materialize supplies the RuntimeSkill bodies that loaded_context and sandbox loading need.

*Call graph*: 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 451–460)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the public skill index shown in prompts. It lists top-level deploy skills only, keeping child skills and member skills out of the stable system prompt.

**Data flow**: It reads deploy skills in registration order, keeps only those without a parent, and returns name-description pairs.

**Call relations**: Prompt-building code calls this to fill the skill index that helps the agent know what built-in skills can be loaded.

*Call graph*: called by 2 (_prompt_skill_index, prompt_index).


##### `SkillRegistry.merged_with`  (lines 462–483)

```
def merged_with(self, generated: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes generated deploy-controlled skills. It refuses shadowing so a generated or member skill cannot silently replace an existing deploy skill.

**Data flow**: It copies the deploy-skill map, appends generated skills whose names are free, logs and skips generated name collisions, removes member cards that now collide with deploy names, and returns a new SkillRegistry.

**Call relations**: Turn setup can call this when adding generated skills such as spawn catalog or setup skills while preserving the rule that deploy skills win name conflicts.

*Call graph*: 2 external calls (__init__, log).


##### `SkillRegistry.with_member`  (lines 485–504)

```
def with_member(self, cards: Sequence[SkillCard], materialize: SkillMaterializer) -> 'SkillRegistry'
```

**Purpose**: Creates a registry that includes the current agent’s saved member skills. Member skills are allowed only when they do not shadow deploy skills.

**Data flow**: It receives member SkillCards and a materializer callback, filters out any card whose name collides with deploy skills while logging the refusal, and returns a new SkillRegistry with the member tier attached.

**Call relations**: Per-turn setup uses this to bind an agent’s saved skills into the otherwise stable deploy registry. Later closure can see the cards, and materialize can read their full files.

*Call graph*: 2 external calls (__init__, log).


##### `_loaded_tree`  (lines 510–528)

```
def _loaded_tree(loaded: Sequence[LoadedSkill]) -> str
```

**Purpose**: Creates a compact tree view of every file made available by a load. This tells the model where files are, without pasting file contents.

**Data flow**: It receives LoadedSkill objects, collects every skill-file path under $UFO_HOME/skills, sorts them, adds directory lines only once, and returns an indented text tree.

**Call relations**: loaded_context appends this tree after the workflow text so the agent can refer to bundled assets by path.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 531–544)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the full text that one skill load contributes to the model context. It includes new workflow instructions, notes repeated skills, and always lists loaded files.

**Data flow**: It receives loaded skills and a set of skill names already in context. It turns only new skills into prompt bodies, adds one note for repeated skills, appends the loaded file tree, and returns the combined markdown text.

**Call relations**: Both normal load_skill flow and subagent preloading use this, so the same loaded skills are presented consistently whether they came from a tool result or a system prompt preload.

*Call graph*: calls 1 internal fn (_loaded_tree).


##### `_wire_skill`  (lines 547–554)

```
def _wire_skill(skill: RuntimeSkill) -> dict[str, object]
```

**Purpose**: Converts a RuntimeSkill into the wire format expected by the sandbox loader. “Wire format” means a safe, serializable shape for sending file bytes across a boundary.

**Data flow**: It receives a RuntimeSkill, gathers all files, checks and trims each path relative to the skill root, base64-encodes each file’s bytes into text, adds the content digest, and returns a dictionary with digest and files.

**Call relations**: install_skill and load_skills call this before asking the Sandbox to install user-provided or non-bundled skill files.

*Call graph*: calls 3 internal fn (all_files, content_digest, root); called by 2 (install_skill, load_skills); 2 external calls (urlsafe_b64encode, contained_relative).


##### `install_skill`  (lines 557–561)

```
async def install_skill(sandbox: Sandbox, skill: RuntimeSkill) -> None
```

**Purpose**: Installs one materialized skill into the sandbox runtime directory. This is useful for loading a single user skill into $UFO_HOME/skills.

**Data flow**: It receives a Sandbox and a RuntimeSkill, converts the skill with _wire_skill, calls the sandbox’s load_skills method with that one user skill, and raises an error if the sandbox does not report a path for it.

**Call relations**: It hands the actual file transfer to Sandbox.load_skills, using _wire_skill to prepare the payload safely.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


##### `load_skills`  (lines 564–571)

```
async def load_skills(sandbox: Sandbox, loaded: Sequence[LoadedSkill]) -> None
```

**Purpose**: Installs a whole resolved set of loaded skills into the sandbox. Bundled deploy skills are referenced by digest, while non-bundled skills are sent with their files.

**Data flow**: It receives a Sandbox and LoadedSkill entries, separates bundled skills into a system digest map, converts non-bundled skills into user file payloads, calls Sandbox.load_skills, and raises an error if any loaded skill is missing from the returned roots.

**Call relations**: After the registry has planned and materialized a skill closure, this function performs the sandbox installation step so the agent can access the skill files at runtime.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


### `extensions/sample/ufo_ext_sample.py`

`orchestration` · `startup registration, then throughout tool calls, jobs, routes, hooks, surfaces, search, model, sandbox, and provider use`

This file matters because it is the project's conformance sample: a real installed extension that imports only the public SDK, not private internals. If a change breaks the public extension contract, this file is meant to expose it. It declares a manifest, which is the extension's menu of capabilities: tools, scheduled jobs, HTTP routes, onboarding, object types, hooks, sources, indexes, models, browser leases, connectors, surfaces, flags, memory search, and more.

Most implementations here are deliberately simple and predictable. For example, the echo tool stores the message it received, the search provider always returns the same result, the model client streams one canned reply, and the carrier pretends to run a sandbox by echoing command arguments. The important part is not realism; it is that each piece is called through the same public seam a real extension would use.

The extension records many calls into its workspace-scoped extension store. That durable store acts like a receipt book. Tests can later read the receipts and confirm that UFO passed the right context, routed the request correctly, enforced permissions, fired hooks, and preserved identities. Without this file, the project would lack one compact, installed example that exercises almost the entire extension API from the outside.

#### Function details

##### `_echo`  (lines 341–345)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It records the incoming message in the extension's durable store, then returns the same text to the caller.

**Data flow**: It receives a tool context and parsed echo input. It checks that the tool was given an extension context, writes the input message as a store record, and returns a tool result containing that message as text.

**Call relations**: The manifest registers this as the handler for the sample echo tool. A pre-tool hook can deny this tool before it runs, which lets the sample prove that denied tools do not write their receipt.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 348–370)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool. It writes a note into the extension's own database table and reads it back to prove extension-owned database migrations and transactions work.

**Data flow**: It receives a tool context and note text. It finds the current workspace, opens a workspace-scoped transaction, updates or inserts the note row, reads the stored note, and returns that stored text.

**Call relations**: The manifest registers this tool separately from the simple echo tool. Scheduled job ownership is also based on the table this function writes, so it helps prove that extension tables can be used by later extension flows.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 373–399)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample scheduled job. It records that the job ran, then optionally inspects agent trajectories, proposes an agent prompt change, writes a workspace file, and runs a probe command.

**Data flow**: It receives an extension context. It first writes a job receipt. If corpus, files, and probe services are available, it reads recent trajectories, stores their count, proposes a prompt edit for the first one, writes a file into the workspace, runs a command that reads that file, and stores the result.

**Call relations**: The manifest registers this as the scheduled job handler. It uses several extension-context services in sequence, so tests can confirm that off-turn jobs receive the same useful capabilities as interactive extension code.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 402–405)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Serves the sample HTTP route. It records the request body and extension home URL, then echoes the body back as plain text.

**Data flow**: It receives an extension context and HTTP request. It reads the request body, decodes it, stores the body and home URL, and returns the body in a plain text response.

**Call relations**: The manifest exposes this through a POST route. The route uses the file's workspace resolver before this handler runs, proving that routed extension requests can be scoped to a workspace.

*Call graph*: calls 1 internal fn (home_url); 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 444–455)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists sample widget objects stored by this extension. It turns stored widget records into object rows suitable for the UFO object UI and APIs.

**Data flow**: It receives a tool context and list query. It reads all extension-store entries with the widget key prefix, validates each stored value, builds rows with names and visible fields, and returns a paged object result.

**Call relations**: The widget object kind in the manifest calls this when the system needs to show a collection of widgets. It relies on WidgetStore._ext to get the extension context and hands the rows to the shared object paging helper.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 457–467)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the widget's specification and timestamps, or nothing if that widget does not exist.

**Data flow**: It receives a tool context and object name. It reads one extension-store key, validates the stored widget shape, and returns object detail with the current generation marker.

**Call relations**: The object system calls this for widget detail reads. Later write operations use the generation returned here as a safety fence against editing stale data.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 469–480)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Checks whether a widget is still the same version the caller expects. It does not change the widget; it only refuses if the generation has moved.

**Data flow**: It receives a tool context, widget name, and optional expected generation. It reads the stored widget and compares its generation to the expected one. It returns nothing when the row is current, and raises an error when it is stale.

**Call relations**: The object system can call this as a lightweight freshness check. It delegates the actual comparison to WidgetStore._require_current, the same helper used by apply and delete.

*Call graph*: calls 2 internal fn (_ext, _require_current).


##### `WidgetStore.apply`  (lines 482–507)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time on update and gives every successful write a fresh generation marker.

**Data flow**: It receives a tool context, widget name, new spec, optional old spec, and optional expected generation. It reads any existing record, checks the generation if needed, builds a new stored widget with current time and a new UUID generation, and writes it back to the extension store.

**Call relations**: The object system calls this for create and update verbs. It uses WidgetStore._ext for context access and WidgetStore._require_current to reject stale edits.

*Call graph*: calls 2 internal fn (_ext, _require_current); 3 external calls (__init__, now, uuid4).


##### `WidgetStore.delete`  (lines 509–522)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only for a workspace admin. It also checks the generation fence before deleting an existing row.

**Data flow**: It receives a tool context, widget name, and expected generation. It reads the current stored widget, verifies it is the expected version if present, asks the tool context whether the speaker is an admin, and either deletes the store entry or raises an admin-required error.

**Call relations**: The object system calls this for widget delete requests. It proves that object verbs can enforce both stale-write protection and permission checks through the public tool context.

*Call graph*: calls 3 internal fn (speaker_is_admin, _ext, _require_current); 1 external calls (__init__).


##### `WidgetStore._require_current`  (lines 524–528)

```
def _require_current(self, name: str, stored: StoredWidget, expected_generation: UUID | None) -> None
```

**Purpose**: Protects widget writes from stale edits. It compares the stored widget's generation with the generation a caller thinks it is editing.

**Data flow**: It receives a widget name, stored widget, and expected generation. If the two generations differ, it raises an error saying the widget changed while editing; otherwise it returns quietly.

**Call relations**: WidgetStore.apply, WidgetStore.delete, and WidgetStore.status all call this helper before trusting a previously read object. It centralizes the sample's optimistic locking rule.

*Call graph*: called by 3 (apply, delete, status).


##### `WidgetStore._ext`  (lines 530–533)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Extracts the extension context from a tool context. It fails loudly if the object store was called without extension state.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it; otherwise it raises a runtime error.

**Call relations**: All WidgetStore read and write methods use this helper before touching the extension store. It keeps each method from repeating the same safety check.

*Call graph*: called by 5 (apply, delete, get, list, status).


##### `RelicStore.list`  (lines 541–545)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic collection. It always returns one canned relic row.

**Data flow**: It receives a tool context and list query. It builds one object row for the fixed relic and returns it through the shared object paging helper.

**Call relations**: The manifest registers RelicStore for the relic object kind. This list path proves that system-produced, read-only objects can still appear in normal object listings.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 547–552)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Gets the one sample relic if the requested name matches. It returns no detail for any other name.

**Data flow**: It receives a tool context and relic name. It compares the name with the fixed relic name, then either returns object detail with a fixed inscription or returns null.

**Call relations**: The object system calls this for relic detail reads. It pairs with RelicStore.list to show a readable object kind whose data is not user-authored.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 554–561)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports a simple live status for a relic. It marks the relic as excavated rather than authored.

**Data flow**: It receives a tool context, name, and optional expected generation. It ignores those inputs and returns a small status dictionary.

**Call relations**: The object system may call this when checking a relic instance. Unlike widgets, relics do not use generation fences because they are read-only sample data.


##### `RelicStore.apply`  (lines 563–572)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. This proves an object kind can be visible but not writable.

**Data flow**: It receives the attempted relic write details. Instead of storing anything, it raises a verb-not-supported error with the sample refusal message.

**Call relations**: The object system calls this when someone tries the apply verb on relics. The refusal complements RelicStore.delete so every mutation path is blocked.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 574–581)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a relic. Relics in this sample are read-only.

**Data flow**: It receives the delete request details. It does not read or change storage; it raises a verb-not-supported error.

**Call relations**: The object system calls this for relic delete requests. Together with RelicStore.apply, it proves read-only object kinds can participate in the object surface safely.

*Call graph*: 1 external calls (__init__).


##### `_target_record`  (lines 621–632)

```
def _target_record(target: ObjectActionTarget | None) -> dict[str, JsonValue] | None
```

**Purpose**: Turns an object action target into a plain JSON-friendly record. This makes it easy to store what object an action was aimed at.

**Data flow**: It receives either an object action target or nothing. If there is no target, it returns null; otherwise it copies the kind, name, agent name, generation, and expected generation into simple values.

**Call relations**: Most sample object actions and bless hooks call this before writing receipts. It gives all those receipts the same readable target shape.

*Call graph*: called by 9 (_audit, _beseech, _bless, _bless_fold, _bless_replace, _calibrate, _divine, _engrave, _polish).


##### `_action_ext`  (lines 635–638)

```
def _action_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Gets the extension context needed by sample action tools. It prevents action handlers from silently running without extension storage.

**Data flow**: It receives a tool context. It returns the embedded extension context, or raises a runtime error if it is missing.

**Call relations**: The audit, polish, engrave, divine, calibrate, bless, and beseech handlers call this at the start. It is the action-tool equivalent of WidgetStore._ext.

*Call graph*: called by 7 (_audit, _beseech, _bless, _calibrate, _divine, _engrave, _polish).


##### `_audit`  (lines 641–651)

```
async def _audit(ctx: ToolContext, args: AuditInput) -> ToolResult
```

**Purpose**: Runs the workspace audit action. It records what subject was audited and what target context was supplied.

**Data flow**: It receives a tool context and audit input. It gets the extension context, converts the target to a plain record, stores the audit receipt, and returns a short text result.

**Call relations**: The manifest binds this tool to the workspace collection. It demonstrates a collection-level object action that is marked safe to run in parallel.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_polish`  (lines 654–657)

```
async def _polish(ctx: ToolContext, args: PolishInput) -> ToolResult
```

**Purpose**: Runs the sample polish action for a widget. It records how many coats were requested.

**Data flow**: It receives a tool context and polish input. It stores the coat count and target record, then returns text confirming the polish count.

**Call relations**: The manifest binds this to widget instances and marks it agent-targetable. It uses the shared action helpers so tests can inspect exactly which widget was targeted.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_engrave`  (lines 660–696)

```
async def _engrave(ctx: ToolContext, args: EngraveInput) -> ToolResult
```

**Purpose**: Runs a side-effecting widget action that changes the widget generation. It also demonstrates idempotency, meaning the same request key can be retried without doing the work twice.

**Data flow**: It receives a tool context and engraving input. It checks for a matching prior idempotency key, verifies the target and generation, reads the widget, writes it back with a new generation, records the engraving receipt, optionally simulates interruption, and returns confirmation text.

**Call relations**: The manifest binds this to widget instances and marks it side-effecting with a presentation prompt. Hook and retry tests use it to prove target fencing, idempotency, and interruption behavior.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 5 external calls (__init__, __init__, __init__, now, uuid4).


##### `_divine`  (lines 699–702)

```
async def _divine(ctx: ToolContext, args: DivineInput) -> ToolResult
```

**Purpose**: Runs a sample read action that returns untrusted third-party text. It records the query and target before returning the canned divination.

**Data flow**: It receives a tool context and query input. It stores the query and target record, then returns a fixed text answer.

**Call relations**: The manifest marks this action as untrusted and collection-bound. That lets the system prove it can wall off external-looking text as data rather than instructions.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_calibrate`  (lines 705–710)

```
async def _calibrate(ctx: ToolContext, args: CalibrateInput) -> ToolResult
```

**Purpose**: Runs the sample calibrate action. It records a numeric offset for the widget collection.

**Data flow**: It receives a tool context and calibrate input. It stores the offset and target, then returns confirmation text.

**Call relations**: The manifest marks this tool as profile-only. The sample uses it to prove that some tools can be held for specific agent profiles rather than broadly exposed.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_bless`  (lines 713–718)

```
async def _bless(ctx: ToolContext, args: BlessInput) -> ToolResult
```

**Purpose**: Runs the sample bless action. It can either fail on purpose or record and return a blessing phrase.

**Data flow**: It receives a tool context and bless input. If the input asks to fail, it raises an error. Otherwise it stores the phrase and target record and returns confirmation text.

**Call relations**: The manifest binds hooks specifically around this action. The pre hook can alter its input, the post hook can replace its output, and the failure hook can observe its errors.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_beseech`  (lines 721–729)

```
async def _beseech(ctx: ToolContext, args: BeseechInput) -> ToolResult
```

**Purpose**: Runs an action that asks the user a question. It returns a final-act payload encoded in text so the system can recognize a user-input request.

**Data flow**: It receives a tool context and question input. It stores the question and target, builds an AskUserInput object containing that question, and returns text with a directive plus the serialized payload.

**Call relations**: The manifest binds this to the widget collection and declares its final act model. It proves an extension tool can end by asking a human for information.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 4 external calls (__init__, __init__, __init__, __init__).


##### `_bless_fold`  (lines 732–746)

```
async def _bless_fold(ctx: HookContext) -> HookOutcome
```

**Purpose**: Pre-processes bless tool input before the bless action runs. It appends a suffix to the phrase and records that the pre-hook saw the call.

**Data flow**: It receives a hook context. If the payload is a pre-tool-use event for bless with BlessInput, it stores the call and target receipt and returns modified input; otherwise it returns nothing.

**Call relations**: The manifest installs this as a pre-tool hook for the canonical bless action. It hands modified input back to the tool runner, proving hooks can rewrite tool input.

*Call graph*: calls 1 internal fn (_target_record); 2 external calls (__init__, __init__).


##### `_bless_replace`  (lines 749–757)

```
async def _bless_replace(ctx: HookContext) -> HookOutcome
```

**Purpose**: Replaces the successful bless output. It records the original output, then tells the system to use a canned replacement.

**Data flow**: It receives a hook context. If the payload is a successful post-tool-use event, it stores the call, output, and target, then returns a modified output instruction.

**Call relations**: The manifest installs this as a post-tool hook for bless. It runs after _bless succeeds and proves post hooks can rewrite what the caller sees.

*Call graph*: calls 1 internal fn (_target_record); 1 external calls (__init__).


##### `_bless_failure`  (lines 760–764)

```
async def _bless_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed bless calls. It observes errors without changing the failure result.

**Data flow**: It receives a hook context. If the payload is a post-tool-use-failure event, it stores the call and failure output, then returns nothing.

**Call relations**: The manifest installs this for bless failures. It complements _bless_replace by proving success and failure hooks go to different handlers.


##### `SampleSource.fetch`  (lines 783–792)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one sample content page from a typed source configuration. It proves source backends can turn configured external content into pages for UFO to sync.

**Data flow**: It receives source config, an optional cursor, and source auth. It creates one page whose body and title come from the configured topic, then returns it with no next cursor.

**Call relations**: The onboarding setup registers this source, and the source provider in the manifest builds SampleSource. Core calls fetch when polling registered sources.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 805–807)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the sample in-memory index. A chunk is a small searchable piece of text plus metadata.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in a dictionary keyed by its digest, replacing any older chunk with the same digest.

**Call relations**: The manifest registers SampleIndex as an index backend. Search-related tests call this through the indexing seam before asking for lexical or vector matches.


##### `SampleIndex.delete`  (lines 809–811)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks that belong to a given scope. A scope means a particular owner kind and owner id.

**Data flow**: It receives an index scope. It finds all stored chunks that match that scope and removes them from the in-memory dictionary.

**Call relations**: Core calls this through the index backend when a whole owner scope should be cleared. It uses _in_scope to apply the same scope rule used by other index methods.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 813–814)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the sample index contains any chunks for a given scope.

**Data flow**: It receives an index scope. It scans stored chunks and returns true as soon as one chunk matches the scope; otherwise it returns false.

**Call relations**: Core can use this through the index backend to decide whether an owner already has indexed content. It shares the _in_scope helper with delete and prune.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 816–822)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks in a scope while keeping a named set of current chunk digests.

**Data flow**: It receives a scope and a keep set. It deletes each stored chunk that belongs to the scope but whose digest is not in the keep set.

**Call relations**: Index maintenance calls this after re-indexing content. It proves an extension index backend can support cleanup without replacing the whole index.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 824–833)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by plain word matching. It scores chunks by how often query terms appear in their text.

**Data flow**: It receives a query, allowed subjects, owner kind, and result limit. It filters chunks by subject and owner kind, counts lowercase query term occurrences, converts positive scores into hits, sorts best first, and returns up to the limit.

**Call relations**: Core calls this through the index search seam for text-based retrieval. It uses SampleIndex._scoped for filtering and _hit to build standard hit objects.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 835–843)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by vector similarity. A vector is a list of numbers used to compare meaning-like positions.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and limit. It filters chunks, computes a dot product score with each chunk embedding, turns positive scores into hits, sorts best first, and returns up to the limit.

**Call relations**: Core calls this through the vector-search part of the index backend. It uses SampleIndex._scoped for filtering, _dot for scoring, and _hit for results.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 845–850)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters stored chunks to the owner kind and subjects allowed by a query.

**Data flow**: It receives a subject set and owner kind. It scans the in-memory chunk dictionary and returns only chunks whose owner kind matches and whose subject is included.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this before scoring. It keeps the sample's access filtering consistent between both search styles.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 859–860)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for each input text. It is a fake embedding backend for testing selection and wiring.

**Data flow**: It receives a tuple of texts. It ignores the text contents and returns one identical sample vector for every text.

**Call relations**: The manifest registers this as an embedding backend. Core can call it when building vector search data without depending on a real model provider.


##### `_in_scope`  (lines 863–864)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether an indexed chunk belongs to a particular index scope.

**Data flow**: It receives a chunk and scope. It compares the chunk's owner kind and owner id with the scope and returns true only when both match.

**Call relations**: SampleIndex.delete, has_chunks, and prune call this helper. It gives those maintenance methods one shared definition of scope membership.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 867–870)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a simple dot product between two vectors. This is the sample's vector similarity score.

**Data flow**: It receives two tuples of numbers. If either is empty, it returns zero; otherwise it multiplies matching positions and sums the products.

**Call relations**: SampleIndex.vector calls this while scoring candidate chunks. It stands in for the math a real vector index would do internally.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 873–882)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Converts a chunk and score into a standard search hit object.

**Data flow**: It receives a chunk and numeric score. It copies the chunk's identity, owner, subject, ordinal, and text into a Hit and attaches the score.

**Call relations**: Both lexical and vector search call this after scoring chunks. It keeps the returned hit shape identical across search modes.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 885–892)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records that onboarding completed and registers the sample source for the shared subject.

**Data flow**: It receives an extension context. It writes an onboarding receipt, builds typed source config with the sample topic, and asks the context to register that source.

**Call relations**: The manifest lists this as an onboarding step. It connects the setup flow to the source-sync flow by creating a source that later fetches SampleSource pages.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 895–898)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Refuses the sample echo tool before dispatch. It proves that a pre-tool hook can stop a tool from running.

**Data flow**: It receives a hook context. It returns a Deny outcome with the sample denial reason and does not inspect or change anything else.

**Call relations**: The manifest attaches this pre-tool hook to the echo tool. Because it runs before _echo, tests can confirm that _echo does not write its normal store receipt when denied.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 901–909)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool calls after they finish. It is an observer hook for success events.

**Data flow**: It receives a hook context. If the payload is a successful post-tool-use event, it stores the tool name; otherwise it does nothing.

**Call relations**: The manifest registers this for all successful tool calls. It demonstrates the normal post-use event path, separate from denied and failed calls.


##### `_record_post_failure`  (lines 912–918)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records tool calls that ended in an error. It is the failure counterpart to the success post hook.

**Data flow**: It receives a hook context. If the payload is a post-tool-use-failure event, it stores the tool name; otherwise it does nothing.

**Call relations**: The manifest registers this for failed tool calls. Tests can compare its receipts with _record_post receipts to verify the success/failure split.


##### `_record_stop`  (lines 921–927)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn. It proves stop hooks receive the answer about to be committed.

**Data flow**: It receives a hook context. If the payload is a stop event, it stores the answer text; otherwise it does nothing.

**Call relations**: The manifest registers this for stop events. It runs near turn completion, after tools and reasoning have produced a final answer.


##### `_record_pre_compact`  (lines 930–937)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information before conversation compaction. Compaction means shortening older context so the model has room to continue.

**Data flow**: It receives a hook context. If the payload is a pre-compact event, it stores the reason and token estimate before compaction.

**Call relations**: The manifest registers this for pre-compaction events. It pairs with _record_post_compact to prove both sides of the compaction lifecycle fire.


##### `_record_post_compact`  (lines 940–952)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction. It captures the summary and token counts before and after shortening.

**Data flow**: It receives a hook context. If the payload is a post-compact event, it stores the summary and token counts.

**Call relations**: The manifest registers this for post-compaction events. It follows _record_pre_compact in the compaction flow.


##### `_record_page_change`  (lines 955–968)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change batches. It also notes whether the off-turn context included a model.

**Data flow**: It receives a hook context. If the payload contains page changes, it stores the page ids and whether ctx.ext.model is present.

**Call relations**: The manifest registers this for page-change events. Source sync and memory updates can trigger it, proving data-plane notifications reach extension hooks.


##### `_SampleConnectorOAuth.authorize_url`  (lines 981–982)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the sample connector's OAuth authorization URL. OAuth is the common web flow where a user grants an app access to an external account.

**Data flow**: It receives a state value and redirect URI. It returns a fixed sample authorization URL with those two values included as query parameters.

**Call relations**: The connector provider exposes this object through the manifest. Core calls this when starting the connect flow for the sample connector.


##### `_SampleConnectorOAuth.exchange`  (lines 984–987)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange. Instead of contacting a real provider, it returns a fixed connected account id.

**Data flow**: It receives the authorization code, redirect URI, workspace id, and state. It ignores the secrets and returns an OAuthAccount for the sample account.

**Call relations**: Core calls this after the user returns from authorization. The broker later uses the account id when executing connector tools.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 1000–1001)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample broker's tool catalog. It offers one canned tool.

**Data flow**: It receives workspace, provider, and query information. It ignores the query and returns a tuple containing one BrokerTool with a fixed slug and description.

**Call relations**: Connector search and dynamic tool discovery call this through the broker seam. _SampleBroker.search also calls it when building a search result.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 1003–1010)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives workspace, provider, and slug. If the slug is not the sample slug, it raises UnknownBrokerTool; otherwise it returns a BrokerTool with a small JSON schema.

**Call relations**: Core calls this when it needs to know how to call a dynamic connector tool. It pairs with execute, which enforces the same known-slug rule.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 1012–1029)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Executes the fake broker tool by echoing the call details. It proves connector execution sends provider, arguments, account, and idempotency data correctly.

**Data flow**: It receives workspace id, provider, tool slug, arguments, account id, and optional idempotency key. It rejects unknown slugs; otherwise it returns a dictionary containing those call details.

**Call relations**: Dynamic connector tools call this through the broker. Its echoed response can then be inspected directly or passed to file_outputs.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 1031–1042)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds produced files named by the broker response. It turns echoed file-output URLs into BrokerFile objects.

**Data flow**: It receives a broker response dictionary. It looks inside the response's arguments for a list named file_output_urls, and for each string URL returns a file record using the URL's final path part as the name.

**Call relations**: Core calls this after broker execution when it needs to collect files produced by a connector tool. It relies on execute echoing arguments back.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 1044–1070)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a fake upload slot for a file being sent to a connector tool. It also simulates deduplication when the same content key is staged again.

**Data flow**: It receives workspace, provider, tool slug, filename, MIME type, and MD5 hash. It builds a content-addressed key; if already staged, it returns an upload without a put URL, otherwise it records the key and returns a file URL plus the argument to pass to the tool.

**Call relations**: Core calls this before connector execution when a tool argument needs an uploaded file. It proves the upload leg of connector tooling without a real object store.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 1072–1075)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a fake broker search result. It includes the sample tool and a short plan telling the agent what to call.

**Data flow**: It receives workspace, provider, and query. It asks _SampleBroker.tools for the available tool and wraps it with a fixed search plan.

**Call relations**: Connector search calls this through the broker seam. It reuses tools so catalog and search results stay consistent.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 1077–1078)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fake bearer credential for a connected account. A bearer credential is a token sent to prove access.

**Data flow**: It receives workspace, provider, and account id. It returns a Credential whose bearer token is the sample prefix plus that account id.

**Call relations**: Core calls this when it needs a broker-held credential for connector egress. The returned value lets tests verify account-specific credential wiring.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 1085–1103)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector's server-side execute tool. It resolves which connected account the current agent may use and records the call.

**Data flow**: It receives a tool context and connector input. It asks the context for the bound account id for the sample connector, stores that account, requested tool name, and idempotency key, and returns the account id as text.

**Call relations**: The connector provider registers this as an extra tool. It proves that a connector tool can resolve account grants through ToolContext.connector_account before doing work.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 1113–1114)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps a bytes value as a one-piece asynchronous stream. It is a tiny helper for writing inbound surface files.

**Data flow**: It receives bytes. When iterated, it yields those bytes once and then ends.

**Call relations**: _surface_ingest calls this when an inbound request includes text to save as a workspace file. It matches APIs that expect streamed file content.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_model`  (lines 1120–1126)

```
async def _surface_model(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports which model was wired into a surface route. It returns null if no model was provided.

**Data flow**: It receives a surface context and request. It reads ctx.model, extracts its model id if present, and returns that in a JSON response.

**Call relations**: The durable sample surface registers this GET route. It proves surface routes can receive model wiring instead of only serving stored data.

*Call graph*: 1 external calls (JSONResponse).


##### `_surface_ingest`  (lines 1129–1156)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message from the sample durable surface. It links a user identity, finds or creates a conversation, optionally writes an inbound file, and admits a turn.

**Data flow**: It receives a surface context and HTTP request. It parses the JSON body, resolves or links a member, finds the conversation for the external id, writes optional inbound text as a workspace file, admits the message with the external id as an idempotency key, and returns turn and conversation ids plus whether a run opened.

**Call relations**: The manifest registers this as the POST route on the durable surface. It calls _one_chunk for file streaming and uses several SurfaceContext methods to prove the full surface admission flow.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 1159–1160)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed reply reference after a durable surface writeback. A reply reference is the outside system's id for the posted response.

**Data flow**: It receives a surface context and writeback. It ignores the details and returns the fixed sample post reference string.

**Call relations**: The durable surface registers this as its post callback. Core calls it when delivering an assistant reply back to the external surface.


##### `_surface_attach`  (lines 1163–1168)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared artifacts from the blob store into delivered blob keys. It proves surface attachments can be streamed out and back in.

**Data flow**: It receives a surface context, writeback, and reply reference. For each artifact, it opens a read stream from the artifact blob key and writes that stream to a delivered key based on the turn id and filename.

**Call relations**: The durable surface registers this as its attachment callback. It runs after posting a reply when the writeback includes files.


##### `_surface_live_admit`  (lines 1171–1193)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message on the sample live surface. Live surfaces stream updates through the hub instead of creating durable writeback rows.

**Data flow**: It receives a surface context and request. It parses input, resolves or adopts a member identity, finds the conversation, admits the message, reads the turn owner, reads recent spend totals, and returns all of that as JSON.

**Call relations**: The live surface registers this POST route. It proves the live admission path, identity adoption from a peer surface, owner lookup, and spend rollup.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 1196–1200)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live frames for a turn as newline-delimited JSON. This lets a caller watch a live turn progress.

**Data flow**: It receives a surface context and request. It reads the turn id from the path, builds a streaming response from _surface_frames, and labels it as newline-delimited JSON.

**Call relations**: The live surface registers this GET route. It delegates the actual hub tailing to _surface_frames.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 1203–1206)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live turn frames from the surface hub and yields them as JSON lines.

**Data flow**: It receives a surface context and turn id. It opens a tail stream for that turn, iterates over frames, serializes each frame to JSON, adds a newline, and yields bytes.

**Call relations**: _surface_live_stream uses this as the body producer for StreamingResponse. It proves the SurfaceContext.tail API can be consumed by extension code.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 1217–1220)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a fake model completion. It starts the stream, sends one text delta, and reports fixed token usage.

**Data flow**: It receives a model request. It yields a stream-start event, a text chunk containing the sample reply, and a usage event with one input and one output token.

**Call relations**: The manifest registers this client under the sample model id. Core selects it like any real model backend, allowing tests to check model registry and pricing wiring.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 1230–1231)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the fake browser debugging endpoint. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no inputs besides the lease. It returns a CdpEndpoint containing the fixed sample websocket URL.

**Call relations**: Browser code calls this through the CdpLease protocol after SampleCdpProvider creates a lease. It proves endpoint retrieval is wired.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 1233–1234)

```
async def token(self) -> str
```

**Purpose**: Returns a durable token that can be used to reattach to the fake browser lease.

**Data flow**: It receives no inputs besides the lease. It returns the fixed CDP URL as the token.

**Call relations**: Browser code can save this token and later pass it to SampleCdpProvider.reattach. In the sample, token and endpoint are intentionally the same fixed value.


##### `SampleCdpLease.place_file`  (lines 1236–1237)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Pretends to place a file for browser use. It simply returns the path unchanged.

**Data flow**: It receives a path and a file-byte reader. It does not read or move the file; it returns the same path.

**Call relations**: Browser automation calls this through the lease when a file needs to be made available. The sample keeps it simple to prove the method is callable.


##### `SampleCdpLease.download_dir`  (lines 1239–1240)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the fake browser download directory.

**Data flow**: It receives no inputs besides the lease. It returns the fixed sample download directory path.

**Call relations**: Browser code calls this when looking for downloaded files. It pairs with fetch_download, which reads files from that directory.


##### `SampleCdpLease.fetch_download`  (lines 1242–1243)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file from the sample download directory.

**Data flow**: It receives a download guid, treats it as a filename under the sample download directory, reads the bytes in a worker thread, and returns those bytes.

**Call relations**: Browser code calls this through the lease after a download is available. It is the one CDP lease method here that touches the local filesystem.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 1245–1246)

```
async def aclose(self) -> None
```

**Purpose**: Closes the fake browser lease. There is no real browser to release, so it does nothing.

**Data flow**: It receives no inputs besides the lease. It returns without changing anything.

**Call relations**: Browser code calls this during cleanup through the CdpLease protocol. The no-op behavior is enough to prove cleanup calls reach the extension object.


##### `SampleCdpProvider.lease`  (lines 1256–1257)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new fake browser lease. It ignores the optional sandbox.

**Data flow**: It receives an optional sandbox. It returns a new SampleCdpLease.

**Call relations**: The manifest registers this provider under the sample CDP backend. Core calls lease when it selects this extension-provided browser backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 1259–1260)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reattaches to a fake browser lease from a token. In the sample, every token returns the same fixed lease.

**Data flow**: It receives a token string. It ignores the token content and returns a new SampleCdpLease.

**Call relations**: Browser code calls this when resuming an existing CDP session. It pairs with SampleCdpLease.token.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 1271–1272)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed credential for the sample auth proxy backend.

**Data flow**: It receives workspace id, provider, and account id. It ignores them and returns a Credential with the fixed sample bearer token.

**Call relations**: The manifest registers this auth proxy. Core calls it when a sync or network path needs credentials supplied by an extension auth proxy.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 1284–1292)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns a canned web search result and a canned direct answer.

**Data flow**: It receives a search query. It ignores the query contents and returns SearchResults containing one fixed hit and a fixed answer string.

**Call relations**: The manifest registers this as a search provider. Research tools can select and call it without reaching a real search service.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1294–1295)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches a canned page for a requested URL. It keeps the requested URL but supplies fixed text.

**Data flow**: It receives a fetch request. It returns a FetchedPage whose URL is the requested URL and whose text is the sample fetch text.

**Call relations**: Core calls this when a search provider supports fetching full pages. It complements SampleSearchProvider.search.

*Call graph*: 1 external calls (__init__).


##### `build_flag_provider`  (lines 1298–1309)

```
def build_flag_provider(_cache_ttl_seconds: float) -> InMemoryProvider
```

**Purpose**: Builds an in-memory feature flag provider. Feature flags are named switches used to turn behavior on or off.

**Data flow**: It receives a cache time value, though the in-memory provider does not need it. It creates two flags: one that resolves true and one that resolves false, then returns the provider.

**Call relations**: The manifest registers this builder for the sample flag backend. Core uses it to prove extension-provided flag providers can be selected and evaluated.

*Call graph*: 2 external calls (InMemoryFlag, InMemoryProvider).


##### `SampleMemorySearch.search`  (lines 1318–1336)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match.

**Data flow**: It receives search queries, a source reader containing subjects, and optional start and end times. It stores the queries, sorted subjects, and time bounds in the extension store, then returns a fixed MemoryMatch.

**Call relations**: The manifest registers SampleMemorySearch as a memory search provider. Core builds it with an extension context and calls search when memory lookup is requested.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1338–1339)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list.

**Data flow**: It receives no inputs besides the provider instance. It returns a tuple containing the sample memory kind.

**Call relations**: Memory listing code can call this before list_recent. It tells the system that the provider can list the same kind it returns from search.


##### `SampleMemorySearch.list_recent`  (lines 1341–1367)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a request for recent memory and returns one canned result page.

**Data flow**: It receives subjects, limit, optional kinds, and optional cursor. It stores those request details in the extension store, including cursor fields if present, then returns a ListingPage with one sample MemoryMatch.

**Call relations**: Core calls this through the memory-search provider seam when recent memory is requested. Its stored receipt lets tests verify subjects, kinds, limits, and cursor data were passed correctly.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1379–1380)

```
def __init__(self) -> None
```

**Purpose**: Initializes the fake sandbox carrier's in-memory file map.

**Data flow**: It receives no inputs besides the new instance. It creates an empty dictionary that later write and read calls use.

**Call relations**: The manifest registers SampleCarrier as a carrier factory. Core creates an instance when selecting this extension-provided sandbox carrier.


##### `SampleCarrier.create`  (lines 1382–1388)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle. It does not start a real container.

**Data flow**: It receives a sandbox spec. It returns a SandboxHandle with the conversation id, fixed container id, run token, and a runtime root based on the conversation id.

**Call relations**: Sandbox setup calls this through the carrier protocol. It proves carrier selection and handle creation without Docker or another real runtime.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1390–1398)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a fake existing sandbox if a resume id is present.

**Data flow**: It receives a sandbox spec. If there is no resume id, it returns null; otherwise it returns a SandboxHandle whose container id is the resume id.

**Call relations**: Sandbox resume code calls this through the carrier protocol. It complements create by proving attach behavior can be extension-provided.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1400–1403)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Pretends to run a command in the sandbox. It returns the command arguments joined as stdout.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It builds stdout by joining the arguments with spaces, uses empty stderr, and returns exit code zero.

**Call relations**: Core calls this when executing sandbox commands through the selected carrier. The echo-like result makes it easy to prove this carrier was used.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1405–1406)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Stores bytes at a fake sandbox path.

**Data flow**: It receives a sandbox handle, path, and content bytes. It saves the bytes in the carrier's in-memory dictionary under that path.

**Call relations**: Sandbox file-copy code calls this through the carrier protocol. SampleCarrier.read later returns the same bytes.


##### `SampleCarrier.read`  (lines 1408–1411)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads bytes previously written to a fake sandbox path.

**Data flow**: It receives a sandbox handle and path. If the path was not written, it raises FileNotFoundError; otherwise it yields the stored bytes as an asynchronous stream.

**Call relations**: Core calls this when retrieving files from the sandbox carrier. It pairs directly with SampleCarrier.write.


##### `SampleCarrier.file_op`  (lines 1413–1416)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a standard UFO filesystem operation against the fake carrier. It delegates the operation to the shared helper.

**Data flow**: It receives a handle, operation name, and parameters. It passes itself, the handle, operation, and parameters to ufo_fs_file_op and returns that helper's result.

**Call relations**: Core calls this for higher-level file operations. Delegating to the SDK helper proves custom carriers can reuse common filesystem behavior.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `SampleCarrier.dial`  (lines 1418–1419)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network target for a port inside the sample container.

**Data flow**: It receives a sandbox handle and port. It returns a DialTarget whose host is the fixed container name plus the port and whose TLS flag is false.

**Call relations**: Core calls this when it needs to reach a service exposed by a sandbox. The sample proves the carrier can supply dial information.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1422–1430)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace for an HTTP request from its bearer token. A bearer token is the credential in an Authorization header.

**Data flow**: It receives a request. It parses the Authorization header, requires the Bearer scheme and a non-empty token, then asks workspace_claim to extract the workspace id; otherwise it returns null.

**Call relations**: The sample route uses this as its identify function, and resolve_surface_workspace reuses it for surfaces. It ensures handlers run only after workspace scoping.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1433–1435)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Asynchronously identifies the workspace for a surface request. It uses the same bearer-token logic as normal routes.

**Data flow**: It receives a request and surface auth object. It ignores the auth object and returns whatever resolve_workspace finds in the request headers.

**Call relations**: Both sample surfaces use this as their identify function. It wraps the synchronous route resolver in the async shape expected by SurfaceSpec.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1438–1439)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Provides a no-op summarizer for the sample conversation slot. A conversation slot is a named panel of extra conversation-related data.

**Data flow**: It receives a conversation slot context. It does nothing and returns null.

**Call relations**: The manifest registers this with the sample conversation slot provider. It satisfies the slot interface while keeping the sample content empty.


##### `_conversation_slot_read`  (lines 1442–1443)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns an empty set of workspace changes for the sample conversation slot.

**Data flow**: It receives a conversation slot context. It returns a WorkspaceChanges object with no changes and truncated set to false.

**Call relations**: The manifest registers this as the slot's read function. Core calls it when rendering or fetching the sample conversation slot.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1446–1722)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the complete declaration of what this sample extension contributes. It is the central wiring point for all fake tools, providers, hooks, routes, objects, surfaces, and setup steps in this file.

**Data flow**: It creates a sample connector broker and then constructs a Manifest containing names, versions, tool definitions, object kinds, jobs, routes, onboarding, prompt sections, agents, credentials, connectors, hooks, surfaces, source/index/embed/model providers, hubs, terminal transports, skills, browser providers, carriers, auth proxies, search providers, flags, memory search, and conversation slots. The returned Manifest is what the host reads to install and call the extension.

**Call relations**: The extension loader calls this entry function at registration time. Every handler and class defined above is connected to the outside world here, so this function turns a pile of sample parts into one usable extension.

*Call graph*: 42 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


### Agent capability packs
These manifests register specialized tools, skills, prompts, credentials, and subagents for browsing, coding, interactive analysis, research, and website building.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup`

This file is like the label and instruction card for the browser extension. When the system loads extensions, it needs to know what this extension is called, what version it is, what tools it adds, and what special helper agents it can create. Without this file, the rest of the system would not know that a browser-focused subagent exists or how the main agent should ask it to do web automation.

The file gathers three main pieces. First, it imports browser tools, which are the lower-level abilities used to interact with a browser. Second, it imports delegation tools, which are the safer front-door tools the main agent uses to ask a browser subagent to do work. Third, it imports the browser subagent profile, which describes the specialist agent that actually works inside the browser.

It also reads a Markdown prompt section from disk. That text becomes part of the main agent’s instructions, so the main agent learns when and how to delegate browser tasks instead of trying to use browser controls directly.

The important design idea is separation: the main agent gets delegation tools, while the browser subagent gets the full browser surface. This keeps browser automation scoped to the specialist child agent.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration for the browser package. The system uses this declaration to learn the extension name, version, tools, subagent profile, prompt text, and required support services.

**Data flow**: It starts with constants and imported objects already prepared by the module: the extension name and version, the browser tools, the delegation tools, the browser subagent profile, and the prompt text read from the Markdown file. It wraps the prompt text in a PromptSection, then places everything into a Manifest object. The result is a single Manifest value that the extension loader can register with the wider system.

**Call relations**: When the extension is being loaded, the system calls this function to ask, “What do you provide?” Inside, it creates a PromptSection for the browser instructions and a Manifest that packages all browser extension pieces together. It does not run the browser itself; it hands back the declaration that later parts of the system use to expose delegation tools and spawn the browser subagent.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup and extension registration`

This file answers a practical question: how does the system safely give a coding-focused child agent the tools and GitHub access it needs to work on software repositories? Without this manifest, the platform would not know that the coding extension exists, which subagents it provides, which tools they may use, which prompts guide them, or how GitHub authentication should be wired in.

The file defines two child-agent profiles. The normal one, named “coding,” is meant to inspect repositories, edit files, run commands, and report back. The second, “fable_escalation,” is a stronger fallback used when ordinary coding attempts have failed. Both receive the same kind of input and output: a freeform objective goes in, and a freeform result comes back.

It also declares GitHub credential slots. A “credential slot” is a named place where the platform can store or mint access, like a labeled socket for secrets. The extension can use either an installed UFO GitHub App or a personal access token fallback. The file is careful not to put real secrets directly into the sandbox; instead it uses sentinel values and injection rules so the platform can substitute short-lived tokens at the right time.

Finally, the manifest exposes a setup tool called “connect_github” and a web route that completes the GitHub App installation flow. In short, this file is the bridge between the coding agent’s abilities and the platform’s extension system.

#### Function details

##### `github_app_id`  (lines 119–132)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has a complete GitHub App registration in its environment variables. It prevents a half-configured GitHub App from silently putting users into a confusing broken state.

**Data flow**: It reads four environment variables: the GitHub App ID, client ID, client secret, and private key. If none are present, it returns null, meaning this deployment is not using the GitHub App registration path. If some are present but others are missing, it raises an error explaining which ones must be added. If all are present, it returns the GitHub App ID.

**Call relations**: This function is used during module setup, before the manifest is built, to decide whether GitHub App token sources can be created. Its result controls whether the extension offers app-minted tokens or falls back to other credential paths.


##### `manifest`  (lines 203–234)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the object the UFO platform reads to learn what the coding extension provides. It gathers the subagents, skill folders, credentials, setup tool, and installation route into one declaration.

**Data flow**: It starts from constants and objects defined earlier in the file: names, version, child-agent profiles, skill paths, credential slots, and GitHub setup handlers. It creates skill specifications for the coding skill, declares the GitHub connection tool, binds that tool to the GitHub installation credential object, and adds a route for the GitHub installation callback. The output is a Manifest object that the platform can load.

**Call relations**: When the extension is loaded, the platform calls this function to register the coding pack. Inside it, the function creates supporting objects such as SkillSpec, ToolDef, ObjectBinding, ActionPresentation, and RouteSpec, and it uses credential_object_name to attach the GitHub connection action to the right stored credential.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, credential_object_name).


### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `extension load and tool request handling`

This file gives the system two notebook-like tools. A REPL is an interactive interpreter: you send code, it runs, and successful code becomes part of the saved session for later calls. The JavaScript tool is meant for browser automation and visual work, including returning images. The Python tool is meant for spreadsheet work with openpyxl.

The important safety rule is that state is only saved when a call exits successfully. If code fails or times out, the saved session stays exactly as it was before that call. This prevents one broken experiment from poisoning every later run. It is like writing notes in pencil first, and only copying them into the permanent notebook after they check out.

For JavaScript, the file builds a temporary `.mjs` run file, adds a small `emitImage` helper, links globally installed Node packages so imports can work, runs Node in the sandbox, then reads back any emitted images. For Python, it builds a temporary script and adds a footer that prints a JSON version of `result` if the user set it.

Both tools run through the system’s background task machinery. If the caller stops waiting, the process may continue, and the result returns handles for checking it later instead of pretending the work finished.

#### Function details

##### `_meter_run`  (lines 71–87)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one metric for a JavaScript or Excel REPL run, including whether the interpreter exited cleanly or with a known failure code. This helps operators tell the difference between broken user code and broken tool setup.

**Data flow**: It receives the tool context, the tool name, and the process exit code. It turns the current profile into a metric label, folds unusual exit codes into a general "other" bucket, and sends a `repl_run_total` count to the observability system. It returns nothing and only changes monitoring data.

**Call relations**: Both `js_repl` and `xlsx_repl` call this after their interpreter process finishes. It hands the final observation to `emit_metric`, using `turn_profile` to include which kind of agent turn produced the run.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 90–109)

```
def global_modules_link(directory: str, roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds a shell command that makes globally installed Node.js packages visible to the JavaScript REPL. Without this, ES-module imports like Playwright might not resolve inside the temporary run directory.

**Data flow**: It receives a workspace directory and a list of possible global Node module roots. It returns a shell script string that creates a local `node_modules` folder and fills it with symbolic links, which are shortcut files pointing to the real global packages. It does not run the script itself.

**Call relations**: `js_repl` calls this before running Node. The returned command is executed in the sandbox, and it uses `shell_path` to quote paths safely for the shell.

*Call graph*: called by 1 (js_repl); 1 external calls (shell_path).


##### `js_emit_relative`  (lines 116–124)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Creates the per-call filename where JavaScript image output will be written. Using a fresh file per call prevents an old still-running process from mixing its images into a newer result.

**Data flow**: It receives a short call identifier. It returns a workspace-relative path like `repl/js-emit-<id>.jsonl`, where JSON lines will store emitted image data.

**Call relations**: `js_repl` calls this at the start of every JavaScript run. The returned path is then used by `js_emit_prelude` to tell JavaScript where to write images, and later by `_emitted_images` to read them back.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 127–164)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Generates the JavaScript helper code that defines `emitImage`. User code can call this helper to send screenshots or generated images back in the tool result.

**Data flow**: It receives the image-output path for this call. It returns JavaScript source code that installs `globalThis.emitImage`, accepts image bytes or base64 text, limits image size and count, and writes the most recent images as JSON lines to the chosen file.

**Call relations**: `js_repl` places this generated code at the top of the temporary Node.js run file before the user’s code. It uses `json.dumps` so the path is safely embedded as a JavaScript string.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `_candidate_source`  (lines 232–240)

```
async def _candidate_source(ctx: ToolContext, relative: str, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should run for the next REPL call. It combines the previously saved successful code with the new code, unless the user asked for a reset.

**Data flow**: It receives the sandbox context, the saved-state path, the new code, and a reset flag. If reset is true, it deletes the saved state. If there is no saved state, it returns just the new code plus a newline. Otherwise, it reads the saved code and appends the new code. The saved state is not changed here.

**Call relations**: Both `js_repl` and `xlsx_repl` call this before creating their temporary run files. It uses the sandbox to check, read, or delete files, and `shell_path` to make file paths safe in shell commands.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (shell_path).


##### `_repl_result`  (lines 243–255)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Packages the result of a REPL process that actually finished. It turns stdout, stderr, the exit code, and optional images into the standard tool-result shape.

**Data flow**: It receives text output, error output, an exit code, and optionally images. It creates a JSON payload containing the output and exit code. If the exit code is not zero, it adds a notice explaining that REPL state was not saved. It returns a `ToolResult`, marked as an error when the exit code is nonzero.

**Call relations**: `js_repl` and `xlsx_repl` call this after a completed run. It creates `TextContent` for the JSON text and includes any `ImageContent` objects passed from `_emitted_images`.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 258–274)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Builds the result for a REPL call that exceeded the caller’s wait time. It explains that state was not advanced and, when possible, gives handles for checking the still-running task later.

**Data flow**: It receives the background task record and the number of seconds actually waited. If there is no live process id, it returns an error message saying the wait expired and state is unchanged. If a process is still alive, it returns task id, process id, log path, and a note that state was not saved.

**Call relations**: `js_repl` and `xlsx_repl` call this when `run_task` reports a timeout. It delegates wording to `timeout_notice` and handle formatting to `task_handles`.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 282–296)

```
async def _emitted_images(ctx: ToolContext, emit_relative: str, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code emitted through `emitImage` and converts them into tool-result image objects. It also removes the temporary image file after reading it.

**Data flow**: It receives the sandbox context plus the relative and absolute paths for this call’s image file. If the file does not exist, it returns an empty tuple. If it exists, it reads the JSON lines, deletes the file, validates each line as an emitted image, skips invalid lines, and returns up to the configured limit as `ImageContent` objects.

**Call relations**: `js_repl` calls this after Node finishes and before creating the final result. It consumes the file written by the helper code generated in `js_emit_prelude`.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, shell_path).


##### `js_repl`  (lines 299–323)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs one JavaScript REPL call in Node.js, preserving successful code across calls and allowing user code to return images. This is the main handler behind the `js_repl` tool.

**Data flow**: It receives the tool context and validated JavaScript input. It finds the saved-state and temporary-run paths, builds the candidate source, creates a fresh image-output path, writes a run file containing the image prelude plus code, links global Node modules, and starts Node through `run_task`. If the run times out, it returns an expired result. If it exits successfully, it saves the candidate source as the new state. Finally, it returns stdout, stderr, exit code, and any emitted images.

**Call relations**: The manifest registers this as the handler for the JavaScript tool. During a call it coordinates `_candidate_source`, `js_emit_relative`, `js_emit_prelude`, `global_modules_link`, `run_task`, `_meter_run`, `_expired_result`, `_emitted_images`, and `_repl_result`.

*Call graph*: calls 8 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (shell_path, run_task, uuid4).


##### `xlsx_repl`  (lines 326–341)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs one Python REPL call for Excel and spreadsheet work, preserving successful code across calls. This is the main handler behind the `xlsx_repl` tool.

**Data flow**: It receives the tool context and validated Python input. It builds the candidate source from prior saved code plus new code, writes a temporary Python file, adds a footer that prints JSON for `result` if it exists, and runs it with `python3` through `run_task`. If the run times out, it returns an expired result. If it exits successfully, it saves the candidate source as the new state. It returns stdout, stderr, and the exit code.

**Call relations**: The manifest registers this as the handler for the Excel-oriented REPL tool. Its flow relies on `_candidate_source`, `run_task`, `_meter_run`, `_expired_result`, and `_repl_result`.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (shell_path, run_task).


##### `manifest`  (lines 344–364)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, tools, skill packs, and sandbox internet setting. Without this, the host would not know that `js_repl` and `xlsx_repl` exist.

**Data flow**: It takes no input. It constructs two tool definitions, one for JavaScript and one for Excel/Python, points each to its input model and handler, creates skill specifications for the bundled data skills, enables sandbox internet access, and returns a `Manifest` object.

**Call relations**: The extension loader calls this when discovering the package. It hands the host system the `ToolDef` entries that connect user tool calls to `js_repl` and `xlsx_repl`, plus `SkillSpec` entries for the data skills.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup / extension loading`

This file is like the label on a plug-in box: it lists everything inside the research pack so the main system can load it correctly. Without it, the system would not know that this extension offers web-research tools, special research subagents, a web-related prompt section, or reusable research skills.

At import time, the file reads a Markdown prompt file called `web_section.md`. That text becomes the “web” prompt section, meaning guidance that can be added to the agent’s instructions when web research is available. It also points to a `skills` folder and names two skills that can be loaded when needed: one for research assistance and one for research reports.

The important function, `manifest`, gathers all of these pieces into a `Manifest`, which is the standard package description used by the host application. It includes normal research tools, a wider research delegation tool, two research subagent profiles, the source-tracking conversation slot, and the skill definitions.

One important safety detail is `requires=("search_providers",)`. The research pack does not own search credentials itself. Instead, it depends on a configured search provider. This makes the application fail early at startup if research is enabled without a search backend, rather than failing later when someone tries to search.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension’s manifest, which is the system-readable declaration of what this extension adds. It is used so the host application can register the research tools, subagents, prompt text, skills, conversation data slot, and required search-provider dependency.

**Data flow**: It reads the constants already prepared in this file, such as the extension name, version, web prompt text, skill folder, tool list, subagent profiles, and source-tracking slot. It wraps the web prompt text in a `PromptSection`, turns each named skill folder into a `SkillSpec`, and places everything into a `Manifest`. The result is a single manifest object that the rest of the system can consume during setup.

**Call relations**: During extension loading, the host system calls `manifest` to find out what the research pack contributes. Inside that call, it creates a `PromptSection` for the web instructions, creates `SkillSpec` objects for the research skills, and hands all of that to `Manifest` so the host can register the tools and profiles before any research turn begins.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / subagent registration`

This file is like a job description for two specialist workers inside the larger agent system. The main agent can delegate research tasks to these workers instead of doing every search itself. Without this file, the system would not know that a “research” or “deep_research” subagent exists, what instructions to give it, or which tools it is allowed to use.

The file sets names for the two profiles, chooses their language models, and lists the tools they can reach for. These include web search, fetching web pages, browser-based tasks, external tools, file reading and writing, memory search, and spreadsheet-style work. It deliberately gives them research tools, but not every possible tool, so their work stays focused.

It also reads two prompt files from disk. These prompts are the written instructions that shape each subagent’s behavior. The regular research profile uses a high-reasoning GPT model chosen for cost and quality. The deep research profile uses a different model and is allowed many more rounds, meaning it can keep working through a larger multi-source task for longer.

Two small Pydantic models describe the contract between the parent agent and the subagent: the parent sends an objective, and the subagent returns a result. Pydantic is a library that checks data has the expected shape, like a form that only accepts the right fields.


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup / extension load`

Think of this file like the contents label on a toolkit box. It does not build websites itself. Instead, it declares everything the main system needs to know so agents can build, preview, serve, and release hosted sites safely.

When the extension is loaded, it creates a Manifest, which is the standard package description the host system understands. The manifest includes website tools, delegation tools for handing work to a website-building subagent, and application-builder tools for reading, editing, previewing, and deploying an app-like site. It also registers a “site” object type, a “sites” surface for showing hosted pages, and subagent profiles that let the main agent spin up a specialized child agent for website work.

The file also loads a prompt section from disk. That prompt text teaches the agent how to use the site-building features in conversation. It registers a website-building skill folder, so the agent can load reusable instructions and templates.

Two safety hooks are important: before certain tool uses, the system checks repair reads and requires quality assurance before deployment. Finally, it registers a scheduled job that releases a main agent’s old homepage once the chat app becomes that agent. Without this file, the extension’s pieces would exist in code but would not be visible to the host system.

#### Function details

##### `manifest`  (lines 60–100)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Manifest object for the sites extension. The host system calls this to learn what this extension contributes: tools, prompts, skills, subagents, surfaces, hooks, conversation slots, and scheduled cleanup work.

**Data flow**: It starts with constants and imported extension pieces: names, versions, tool definitions, prompt text read from a Markdown file, skill paths, safety hook functions, and homepage-release job settings. It packages those into small specification objects such as PromptSection, SkillSpec, HookSpec, and JobSpec, then places them into one Manifest. The result is a complete declaration that the host can load; the function does not directly build or serve a site itself.

**Call relations**: During extension loading, the host asks this function for the sites extension’s manifest. Inside that setup, it creates hook specifications for pre-tool safety checks, creates a job specification for the homepage release sweep, creates prompt and skill specifications, and calls unreleased_main_homepage_workspaces to define which workspaces are candidates for that scheduled job. It then hands the finished Manifest back to the host system, which uses it to wire the extension into normal agent runs.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, unreleased_main_homepage_workspaces).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup`

This file is like a job description and toolbox list for a helper whose only job is to build and check websites. The main system can start this helper as a child task, giving it a clear objective and letting it work inside the same workspace as the parent conversation.

The file loads a website-building prompt from a nearby Markdown file. That prompt contains the detailed working instructions for the subagent. It then builds a list of tools the subagent is allowed to use: basic file tools such as reading, writing, editing, searching files, local build and site tools, JavaScript and spreadsheet REPL tools, and optional web research tools. A REPL is an interactive scratchpad where code can be run to inspect or test something.

Two important tools are deliberately left out. The subagent cannot use `publish_website`, because publishing a full app is reserved for the parent agent that is directly serving the user. It also cannot use `share_file`, because the child task does not deliver files directly to the user; instead, it leaves work in the shared workspace for the parent to inspect and pass along.

Finally, the file defines small input and output data models, then packages everything into `WEBSITE_BUILDING_PROFILE`, which the wider system can register and run.


### External connectors and channels
These registrations expose connector tools, knowledge sources, messaging surfaces, credentials, and provider backends for external systems.

### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension discovery`

This file is like the extension’s signboard at the front desk. When the UFO system loads extensions, it needs a clear list of what each extension brings with it: its name, version, tools, data objects, and any instructions that should be shown to the AI. This file provides exactly that for the connectors extension.

The connectors extension is meant to expose “external tools” from many possible connector providers. A connector might let the system search, describe, or run actions against outside services. Rather than declaring separate tools for every provider, this file declares one shared set of connector tools that work across all registered connectors.

It also loads a prompt section from `prompts/connectors_section.md`. A prompt section is reusable instruction text that becomes part of the AI’s guidance, helping it understand how and when to use these connector tools.

Without this file, the extension could still contain useful code, but the host system would not know how to present it: the tools would not be registered, the connector-related objects would not be advertised, and the AI would miss the connector-specific instructions.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the connectors extension. The host system uses this manifest to discover the extension’s name, version, available tools, supported object types, and prompt instructions.

**Data flow**: It starts with constants already defined in the file: the extension name and version, the connector tool list, the connector-related object definitions, and the prompt text read from disk. It wraps the prompt text in a `PromptSection`, then packages everything into a `Manifest`. The result is a single manifest object that describes what this extension contributes to the system.

**Call relations**: This function is called when the extension is being discovered or loaded. Inside it, the function creates a `PromptSection` for the connector instructions and then creates the `Manifest` that hands those instructions, tools, and objects back to the wider UFO extension system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/gbrain/ufo_ext_gbrain/manifest.py`

`config` · `startup / extension loading`

Think of this file as the extension’s label on a plug-in box. When UFO loads the gbrain extension, it needs to know what new things the extension can add to the system. This file gives that answer in one compact place.

The gbrain extension can sync markdown pages from two kinds of sources. One source reads from a GitHub repository. The other reads from a local folder, useful when serving or testing content from disk. The file also declares a credential slot, which is a named place where the system can store a secret such as a GitHub token. That token is only needed when the GitHub repository is private; public repositories can be read without it.

The manifest also registers the gbrain object kind, which tells the system what kind of source record can exist for this extension. Without this file, the rest of the extension code might exist, but UFO would not know how to discover it, what source backends it provides, or what credential name to ask for.

#### Function details

##### `manifest`  (lines 16–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the gbrain extension manifest, which is the formal description of what this extension adds to UFO. The system uses it to discover the gbrain source type, the GitHub and folder source backends, and the optional GitHub token credential.

**Data flow**: It starts with constants and imported building blocks: the extension name and version, the gbrain object definition, the Git backend, the folder backend, and the GitHub token name. It packages these into a Manifest object. The result is a ready-to-read description that tells UFO which source providers can be created and what credential slot is available.

**Call relations**: When the extension is loaded, UFO calls this function to ask, “What do you provide?” The function creates SourceProvider entries for the GitHub and folder readers, creates a CredentialSlot for the GitHub token, and hands everything back inside a Manifest so the wider sync system can later build the right source reader when a gbrain source is registered.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/imessage/ufo_ext_imessage/manifest.py`

`config` · `startup / extension discovery`

This is the extension’s sign-up sheet. When UFO loads extensions, it needs a clear answer to questions like: What is this extension called? What can it do? How can the rest of the system talk through it? What secrets does it need? This file answers those questions by building a Manifest, which is a structured description of the extension.

The file declares the extension name and version, then defines one main function, manifest(). Inside that function, it creates two working pieces that both use the Spectrum cloud provider: an ImessageSurface, which represents the place where messages can be received and sent, and an ImessageConnect tool, which lets a workspace member connect their iMessage phone.

The tool definition explains the human-facing action: a member proves they control a phone by texting a code to an assigned line. The tool is marked as untrusted and side-effecting, meaning the system should treat its input carefully and expect it to change real-world state.

The surface definition tells UFO how to listen for iMessage activity, post messages, attach files, and speak through this channel. Finally, the manifest lists the environment variables that must exist for deployment. Without this file, UFO would not know that the iMessage extension exists or how to use it.

#### Function details

##### `manifest`  (lines 21–55)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of the iMessage extension. UFO uses this description to learn the extension’s name, version, available tool, message surface, and required deployment secrets.

**Data flow**: It starts with no outside arguments. It reads constants and imported classes from the extension and SDK, creates an iMessage surface and a phone-connection tool using the Spectrum provider, wraps them in ToolDef and SurfaceSpec objects, and returns one Manifest object. The returned manifest is the finished package of instructions UFO can load.

**Call relations**: During extension loading, the system calls this function to ask, “What does this extension provide?” The function creates ImessageSurface and ImessageConnect as the active pieces, then hands their methods to SurfaceSpec and ToolDef so the wider UFO runtime can call them later when users connect phones or when messages need to be received and sent.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension discovery`

This file is the Slack extension’s registration form. When the main system loads extensions, it needs a clear answer to questions like: What secrets does this extension need? What web requests should be routed to it? What tools can the agent use? What background reactions should happen during a user turn? Without this file, the Slack code could exist, but the core system would not know how to reach it or when to call it.

The manifest declares two per-workspace credential slots: a Slack bot token and, for bring-your-own Slack apps, a signing secret used to verify Slack requests. It also declares one Slack “surface,” meaning one place where users interact with the system. That surface has routes for incoming Slack events, interactive button/menu actions, and OAuth callback installation. OAuth itself uses deploy-wide environment variables, not these per-workspace slots.

The file also connects Slack-specific behavior into the wider agent loop. Before certain connector tools run, a hook can mark Slack send activity. When a user prompt is submitted, another hook starts following the Slack thread so status and progress messages can continue correctly. When a connection is recorded, another hook can update the Slack connect button. Finally, it points to a setup skill that helps users create and connect their own Slack app if they are not using OAuth.

#### Function details

##### `manifest`  (lines 55–97)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension’s manifest, which is the object the core system reads to understand how Slack should be installed, contacted, and used. Someone would use this function when loading the extension so Slack becomes available as a supported surface.

**Data flow**: It starts with constants and imported Slack handlers, tools, hooks, and skill paths. It packages them into credential slot descriptions, web route definitions, a surface definition, hook definitions, and a setup skill entry. The result is a single Manifest object that changes nothing by itself, but gives the rest of the system the map it needs to wire Slack in.

**Call relations**: During extension loading, the core calls this function to get Slack’s registration details. Inside it, small specification objects are created for credentials, routes, the Slack surface, hooks, and the setup skill; these are then handed to the Manifest constructor so the core can later route Slack web traffic, run Slack hooks at the right moments, expose Slack tools, and offer the Slack app setup skill.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, __init__).


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup/config load`

This file does not do the syncing work itself. Instead, it describes everything the larger system needs in order to plug the sources extension in correctly. Think of it like a restaurant menu plus kitchen instructions: it lists what can be ordered, which staff should react to events, and what recurring cleanup task should run.

The extension exposes one source backend for each registered connector in `CONNECTORS`. A connector is the provider-specific piece that knows how to talk to an outside service. The file wraps each connector in a small factory so the sync system can build a `ConnectorBackend` when it needs one.

It also declares credential slots. These are named places where a user or deployment can provide their own API keys, often called BYOK, meaning “bring your own key.” Those keys are read by the `direct` authentication proxy, which is the built-in fallback authentication path when no external broker is being used.

The manifest also registers object kinds for sources, source triggers, and pages; hooks that react when pages change or when a connection is recorded; and a scheduled job that retries creating connected sources if the first attempt did not land. Without this file, the host would not know this extension exists or how to wire its providers, credentials, hooks, and scheduled work into the rest of the system.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 41–42)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use source backend. The system can call the factory whenever it needs a backend for a specific provider.

**Data flow**: It receives a credential-access object, though this factory does not use it directly. It creates a fresh connector instance from the connector class stored in the factory, wraps that connector in a `ConnectorBackend`, and returns the backend to the caller.

**Call relations**: The manifest creates one `ConnectorSourceFactory` for each registered connector and gives it to a `SourceProvider`. Later, when the host needs to build that provider’s source backend, it calls this factory, which hands back the `ConnectorBackend` that the sync runner can use.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 45–82)

```
def manifest() -> Manifest
```

**Purpose**: This builds the full manifest for the sources extension. A manifest is the structured description the host app reads to learn what the extension contributes.

**Data flow**: It starts from constants in this file and registered connector classes from `CONNECTORS`. It turns those into source provider entries, credential slot entries, hook entries, an authentication proxy entry, and a scheduled retry job entry. It returns one `Manifest` object containing all of that information.

**Call relations**: The host calls this during extension loading. Inside, it creates hook specs for page-change and connection-recorded events, creates a retry job using the workspace candidates from `connection_workspaces`, builds source providers through `ConnectorSourceFactory`, and registers the direct authentication proxy that creates `DirectAuthProxy` instances when credentials are available.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, connection_workspaces, items).


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This file solves a simple but important problem: when a source row says it uses a backend such as Slack, Airtable, or Stripe, the system needs a reliable way to find the right connector code. Instead of searching the codebase at startup, this file imports every supported connector and builds one explicit registry called CONNECTORS.

The registry is a dictionary whose keys are connector names and whose values are the connector classes. A connector class is the piece of code that knows how to read data from one outside service. Making the list explicit means adding a new provider is straightforward: import its connector and add it to the tuple. It also avoids hidden startup work from automatic discovery.

The helper function _connector_registry builds the dictionary and checks for a dangerous mistake: two connectors using the same name. If that happened, a source asking for one backend might silently get the wrong connector. This file prevents that by raising an error immediately.

The constant SOURCE_KIND marks these entries as source-type objects elsewhere in the system. Overall, this file is like the front desk directory: given a provider name, it points the sync machinery to the right office.

#### Function details

##### `_connector_registry`  (lines 66–74)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: This function turns a list of connector classes into a lookup table keyed by each connector’s name. It also protects the system from duplicate connector names, which would make backend selection ambiguous.

**Data flow**: It receives a tuple of connector classes. For each class, it reads the class’s name value, checks whether that name has already been used, and then stores the class under that name in a dictionary. It returns the completed dictionary, or stops with a ValueError if two connectors claim the same name.

**Call relations**: This function is used when the file is imported to create the CONNECTORS registry. The rest of the source-sync system can then look up a backend name from stored source data and get back the connector class that knows how to talk to that outside service.


### Application surfaces
These extension manifests mount user-facing or admin-facing web surfaces and portal functionality into the host application.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This is the debugger extension’s “business card.” When the larger UFO system loads extensions, it needs a simple, standard answer to questions like: What is this extension called? What version is it? What tools does it add? What user-facing pages or routes should be made available?

The file answers those questions by building a Manifest, which is a structured description of an extension. It names the extension “debugger,” gives it version “0.1.0,” and advertises two main pieces. The first is report_problem, represented here by REPORT_PROBLEM_TOOL_DEF. That tool lets a workspace problem be reported to operators. The second is a surface, meaning a mounted user interface area or set of web routes. This surface is named SURFACE_DEBUG and uses ROUTES from the debugger surface module.

A key safety detail is the identify function, resolve_operator_workspace. In plain terms, this is the gatekeeper that decides which operator workspace a request belongs to before the debug surface is used. Without this manifest, the debugger extension could exist in the codebase but the host system would not know to expose its tool or its routes.

#### Function details

##### `manifest`  (lines 16–24)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the object the host system reads to learn what this extension provides. Someone would use it when loading extensions so the debugger’s tool and surface can be registered.

**Data flow**: It starts with fixed values from this file and imported definitions from nearby debugger modules: the extension name, version, report-problem tool definition, route list, surface name, and workspace-identification function. It wraps the route information into a SurfaceSpec, then wraps the extension name, version, tool, and surface into a Manifest. The result is a complete description of the debugger extension, with no files or network calls changed by this function itself.

**Call relations**: During extension loading, the host calls manifest to ask this extension what it contributes. Inside, it creates a SurfaceSpec for the debug surface, then creates the Manifest that includes that surface and the report-problem tool. The finished Manifest is handed back to the host, which can then mount the routes and make the tool available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup`

This is the extension’s “shipping label.” When the main UFO system looks for installed extensions, it needs a small, standard description of what each extension offers. This file provides that description for the `ufo` extension.

The extension exposes one surface, meaning one outward-facing way for a client to interact with it. Here, that surface is the terminal-facing `ufo` shell client connection. The file says which routes belong to that surface, and it points to `resolve_workspace`, the function used to identify which workspace a connection belongs to.

A useful way to think about this file is like a sign-up sheet at a front desk. It does not run the service itself. Instead, it says: “This extension is called `ufo`, this is its version, and this is the doorway clients can use.” The rest of the system can then mount that doorway consistently.

One important detail from the module comment is that this extension does not declare extra credential storage or configuration switches. If it is installed, it is mounted. Authentication is expected to happen through the bearer token checked against `UFO_TOKEN_SECRET`, not through a per-workspace credential slot.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the `ufo` extension. The host system uses this object to learn the extension’s name, version, route surface, and workspace-identification hook.

**Data flow**: It starts with the module constants for the extension name and version, plus the imported surface name, route list, and workspace resolver. It wraps the surface details in a `SurfaceSpec`, then wraps that in a `Manifest`. The result is a single manifest object that the core system can read when loading the extension.

**Call relations**: When the extension is being discovered or loaded, the host calls `manifest` to ask what this extension provides. Inside, it creates a `SurfaceSpec` to describe the client-facing surface, then creates a `Manifest` to package that surface together with the extension’s name and version for the core system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup and scheduled job registration`

This file is a manifest, which is like a shipping label for the web extension. It does not implement the web pages themselves. Instead, it tells the main UFO system what this extension contains and how it should be plugged in.

The manifest says the extension is named after the web surface, has a version, and exposes a browser-facing surface called the web portal. That surface includes routes, which are the web paths the browser can visit, and an identification function that works out which workspace the visitor belongs to. It also marks this surface as the home surface, so a plain visit to the host can open the portal.

The file also lists feature flags. A feature flag is a named on/off switch controlled outside the code, often per environment. These flags decide whether parts of the portal appear, such as Wiki, Issues, Memory, Skills, admin screens, and first-run iMessage setup.

Finally, it registers two scheduled background jobs. One gives untitled conversations readable titles based on their opening exchange. The other seeds missing agent homepages. Without this file, the core system would not know to mount the web portal, expose its tools, recognize its flags, or run its web-related background jobs.

#### Function details

##### `manifest`  (lines 60–85)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s complete declaration for the core UFO system. The core uses this declaration to know which web routes, access tools, conversation slots, feature flags, and scheduled jobs belong to the web extension.

**Data flow**: It starts from constants imported from the web extension and SDK: names, routes, flags, slots, job schedules, and job handler functions. It wraps those pieces into a `SurfaceSpec` for the browser portal, two `JobSpec` entries for background work, and finally a `Manifest` object. The result is a single structured object that the wider system can read during setup.

**Call relations**: When the extension is loaded, the system calls `manifest` to ask, “What do you provide?” Inside that answer, it creates a surface description with `SurfaceSpec.__init__`, creates two job descriptions with `JobSpec.__init__`, asks the SDK for the right job candidate sets through `untitled_conversation_workspaces` and `unseeded_agent_workspaces`, and then hands everything to `Manifest.__init__` so the core can register the web portal and its jobs.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).


### Persistent context
Memory and objectives registration keeps long-running context visible through tools, hooks, jobs, and agent guidance.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup registration, then active during tool calls, prompt submission, page-change handling, and scheduled jobs`

This file makes the memory feature visible to the rest of the system. Without it, the agent would not know how to search or write long-term memory, page changes would not be indexed or turned into facts, and the nightly cleanup and summary jobs would not run.

The file defines the shapes of tool inputs, such as a memory search request or a new memory item. It then provides the tool handlers that do the real work: searching remembered facts and source pages, writing new facts, recording corrections, and asking the system to rebuild facts derived from synced pages.

It also adds automatic recall. When a user submits a prompt, the recall hook searches for relevant memories and injects a short “Relevant memory” block into the model’s context. This is best effort: if recall is slow or broken, the conversation continues without it.

The rest of the file wires background work. Some jobs index new memory for search. Others consolidate old facts, remove duplicates, write wiki-style section and overview paragraphs, update people profiles, or reread full memory pages to retire repeated rows. Think of it like a library system: tools let people add and search cards, hooks quietly place useful cards on the desk, and scheduled jobs reorganize the catalog overnight.

#### Function details

##### `_date_bound`  (lines 254–265)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional date or date-time string into a clear UTC time boundary for memory searches. It makes bare end dates include the whole day, so searching through “2026-01-31” includes that date instead of stopping at its first second.

**Data flow**: It receives a string or nothing, plus a flag saying whether this is an end boundary. If there is no value, it returns nothing. If there is a value, it parses it as an ISO-style date or date-time, adds UTC when no timezone is present, and for bare end dates moves the bound to the next midnight. The result is a datetime object used to filter search results.

**Call relations**: The memory search tool calls this before searching. It hands the parsed start and end bounds to the search service so the store can return only memories created in the requested window.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 274–352)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs the main memory search workflow used by the memory tool and by other extensions. It searches both durable memory items and synced source-page passages, then merges the results fairly across several focused queries.

**Data flow**: It receives one to three query strings, a source reader that describes who is allowed to read what, and optional date bounds. It asks the memory store to recall matching memory items and search source pages for every query at the same time. It then interleaves the per-query results, removes duplicates, converts them into common MemoryMatch objects, and returns the combined matches.

**Call relations**: Tool handlers and registered memory-search providers use this service when they need recall. Inside, it gets the workspace store, runs the memory and source searches concurrently, and wraps each result with an object reference so callers can later open the full memory item or page.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 354–357)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory item classes can be listed by consumers that browse memory rather than search it. This avoids keeping a separate hard-coded list that could fall out of date.

**Data flow**: It reads the allowed item-class type definition and extracts its literal values. It returns those values as a tuple of strings.

**Call relations**: This supports the memory search provider interface. Consumers can ask the service what kinds are valid before requesting a recent-memory listing.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 359–409)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Returns a newest-first page of live memory items for given subjects, without doing similarity search. This is for browsing recent memory like a feed, not for finding text that matches a query.

**Data flow**: It receives readable subjects, a page size, optional item kinds, and an optional cursor that marks where browsing left off. It builds a database query for non-retired, non-superseded memory rows in the current workspace, applies the kind filter if present, and uses shared listing helpers to fetch one page. It returns MemoryMatch objects plus paging information.

**Call relations**: This is part of the provider service registered by the manifest. It relies on the shared listing utilities for cursor-based paging, so memory browsing behaves like other browsable lists in the system.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 412–419)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one memory search hit into a short line of text for the agent to read. It includes the kind, snippet, object reference, and date when available.

**Data flow**: It receives a MemoryMatch. It builds a bullet line from the match text, then adds the object reference and creation date if they exist. The output is a single human-readable string.

**Call relations**: The memory search tool uses this after the search service returns matches. It turns structured results into the text shown in the tool result.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 422–442)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the agent-facing memory_search tool. It lets the agent search remembered facts and source snippets, optionally within a date range.

**Data flow**: It receives the tool context and validated search arguments. It checks that extension context is present, parses start and end dates, builds a source reader from the tool context, and calls MemorySearchService.search. If nothing matches, it returns a plain “No matching memory” message; otherwise it returns formatted result lines.

**Call relations**: The manifest registers this as the handler for the memory_search tool. It delegates parsing to _date_bound, searching to MemorySearchService.search, and formatting to match_line.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 445–459)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the agent-facing memory_update tool. It records one durable memory item for the current audience, such as a lasting fact, preference, decision, event, or task.

**Data flow**: It receives the tool context and the memory item fields. It checks for extension context, chooses the subject from the effective audience, creates a MemoryWrite, and commits it to the memory store. It returns a confirmation naming the subject that received the memory.

**Call relations**: The manifest registers this as a side-effecting tool because it changes stored memory. It hands the actual write to the memory store rather than editing database rows directly.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_correction_handler`  (lines 462–480)

```
async def record_correction_handler(ctx: ToolContext, args: RecordCorrectionInput) -> ToolResult
```

**Purpose**: Records a corrected version of an existing memory item from the memory view. It does not edit the old row directly; it writes a new fact that points back to the item being corrected.

**Data flow**: It receives the tool context and a correction containing the old memory id plus the corrected text. It checks for extension context, writes a new fact under the speaker’s audience, sets normal confidence and fact kind, and stores a source reference that says which memory it corrects. It returns a remembered confirmation.

**Call relations**: The manifest exposes this as a bound action on the memory collection. Later deduplication can retire the older near-duplicate in favor of the newer correction.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `record_first_run_handler`  (lines 483–499)

```
async def record_first_run_handler(ctx: ToolContext, args: RecordFirstRunInput) -> ToolResult
```

**Purpose**: Records the first-run statement about what tools or systems the team uses. This gives later conversations a durable starting fact to recall.

**Data flow**: It receives the tool context and one short body string. It checks for extension context, writes the body as a normal fact under the effective audience, marks its source as the first run, commits it, and returns a confirmation.

**Call relations**: The manifest exposes this as a first-run action. It follows the same store-write path as normal memory updates but uses fixed metadata so the item is recognizable later.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 502–584)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically finds relevant memories when a user submits a prompt and, when useful, injects them into the model’s context. It is deliberately best effort: memory recall should help a turn, not block it.

**Data flow**: It receives a hook context. It first checks that the event is a user prompt and that there is a turn to attach to. It skips speakerless internal root turns, logs that skip, and otherwise computes readable subjects from the audience. It searches memory with a soft timeout, filters out topic-only recalls, truncates long items and the total injected text, logs which memory ids were included, and returns an InjectContext when there are lines to add. If recall fails, it logs the failure and returns nothing.

**Call relations**: The manifest registers this for user_prompt_submit. The hook calls into the memory store for recall and hands back injected context to the hook chain before the model runs.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 587–596)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the background job that indexes committed memory items so they can be searched semantically. Semantic search means matching by meaning, not just exact words.

**Data flow**: It receives an extension context. It verifies that both the index backend and embedding backend are available, then creates a MemoryIndexer with those backends, a text chunker, transaction access, and page-state storage. It runs the indexer, which updates index state outside this function.

**Call relations**: The manifest registers this as the memory_index scheduled job. Candidate selection decides which workspaces have unindexed memory, and this function performs the indexing work for each selected workspace.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 599–615)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Processes page-change events by turning changed source pages into searchable index chunks and mirror rows. This keeps synced documents findable through memory search.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. Otherwise it checks that index and embedding backends are wired, creates a PageIndexer, and applies the delivered page changes. It returns no hook output.

**Call relations**: The manifest registers this as one of the page_change hooks. The core runner owns the cursor and delivers batches; this function performs the indexing side of page-change processing.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 618–629)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Processes page-change events by distilling changed source pages into durable fact memory items. This turns synced documents into concise memory rows the system can recall and show in the wiki.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. Otherwise it requires a model backend, builds a FactDeriver using the memory store and model, and applies the page changes. The deriver writes replacement facts and retires facts that were replaced.

**Call relations**: The manifest registers this as a second page_change hook, separate from page indexing. It has its own cursor key so fact derivation can progress independently from indexing.

*Call graph*: 2 external calls (__init__, store_for).


##### `rebuild_page_facts_handler`  (lines 641–657)

```
async def rebuild_page_facts_handler(ctx: ToolContext, args: RebuildPageFactsInput) -> ToolResult
```

**Purpose**: Implements the rebuild_page_facts tool for workspace admins. It asks the system to rederive facts from every synced page without directly changing the facts itself.

**Data flow**: It receives the tool context and an empty input object. It checks for extension context, verifies that the speaker is an admin, deletes the fact-derivation cursor key, and returns a message explaining that rebuilding has been queued. If the speaker is not an admin, it raises an error.

**Call relations**: The manifest registers this as a side-effecting page-collection action. By clearing the derive_facts cursor, it causes the normal page-change derivation flow to reread pages from the beginning on later ticks.

*Call graph*: calls 1 internal fn (speaker_is_admin); 2 external calls (__init__, __init__).


##### `consolidate_memory`  (lines 660–668)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that groups older related facts into higher-level summary memories. This keeps long-term memory from becoming a pile of repetitive small facts.

**Data flow**: It receives an extension context. It requires an embedding backend, then creates a MemoryConsolidator with embedding access, transaction access, workspace id, and model access. It runs the consolidator, which writes summaries and supersedes originals as appropriate.

**Call relations**: The manifest registers this as the memory_consolidate scheduled job. Candidate selection limits it to workspaces old and large enough to actually have facts worth clustering.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 671–679)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that finds and retires duplicate tool-written memory rows. This helps keep the memory wiki and recall results from repeating the same thing many times.

**Data flow**: It receives an extension context. It requires an embedding backend, creates a MemoryDeduper with embedding access, transaction access, workspace id, and store access, and runs it. The deduper updates memory rows by retiring duplicates toward the newest surviving copy.

**Call relations**: The manifest registers this as the memory_dedup scheduled job. Candidate selection looks for workspaces that already have enough aged duplicate-looking rows to make a sweep worthwhile.

*Call graph*: 1 external calls (__init__).


##### `write_memory_sections`  (lines 682–687)

```
async def write_memory_sections(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that writes section-opening paragraphs for bands of the memory wiki. A band is a group such as one subject and memory kind.

**Data flow**: It receives an extension context. It creates a SectionWriter with transaction access, workspace id, and model access, then runs it. The writer reads live facts and writes or removes section paragraphs as needed.

**Call relations**: The manifest registers this as the memory_section scheduled job. Candidate selection finds workspaces where enough facts exist for a section paragraph, or where an old paragraph may need to be removed.

*Call graph*: 1 external calls (__init__).


##### `write_memory_overview`  (lines 690–695)

```
async def write_memory_overview(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that writes the opening overview paragraph for the shared memory page. This gives readers a short top-level summary before the detailed rows.

**Data flow**: It receives an extension context. It creates an OverviewWriter with transaction access, workspace id, and model access, then runs it. The writer reads the shared facts and writes or removes the overview paragraph depending on whether there is enough material.

**Call relations**: The manifest registers this as the memory_overview scheduled job. It runs shortly after section writing so the page’s summary layers are refreshed in the same nightly window.

*Call graph*: 1 external calls (__init__).


##### `write_member_profiles`  (lines 698–703)

```
async def write_member_profiles(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that writes profile entries for people, such as a member’s role and current focus. This turns shared facts into a people-oriented view.

**Data flow**: It receives an extension context. It creates a ProfileWriter with transaction access, workspace id, and model access, then runs it. The writer reads facts and roster information through its own logic and writes profile rows.

**Call relations**: The manifest registers this as the memory_people scheduled job. Candidate selection binds workspaces that have shared facts, because those facts are the raw material for profiles.

*Call graph*: 1 external calls (__init__).


##### `curate_memory_pages`  (lines 706–711)

```
async def curate_memory_pages(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the job that rereads a subject’s whole memory page and retires rows that repeat one another. This is a broader cleanup pass than simple duplicate detection.

**Data flow**: It receives an extension context. It creates a PagePass with transaction access, workspace id, and model access, then runs it. The pass reads full pages and changes memory rows according to its curation decisions.

**Call relations**: The manifest registers this as the memory_page_pass scheduled job. This job declares that it needs the deploy model because its purpose is to read a whole page in context.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 714–719)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with memory items still missing search embeddings. An embedding is a numeric representation of text used for meaning-based search.

**Data flow**: It takes no input. It creates a SQL query selecting distinct workspace ids from memory items whose embedding digest is missing. The output is the query object, not the rows themselves.

**Call relations**: The manifest passes this query builder to owner_candidates for the memory_index job. The job scheduler uses it to decide which workspace owners need indexing work.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 722–739)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces where memory consolidation could actually do useful work. It avoids scheduling the job for workspaces with too few or too-new facts.

**Data flow**: It computes an age cutoff from the current UTC time, then builds a SQL query for workspaces with enough live, non-page-derived fact rows older than that cutoff. It returns the query object.

**Call relations**: The manifest uses this as the candidate selector for the memory_consolidate job. This keeps the hourly job from spending time on workspaces that cannot form a valid cluster.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 742–763)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces with an aged backlog of likely duplicate memory rows. It focuses on tool-written rows, not page-derived facts.

**Data flow**: It computes an age cutoff from the current UTC time, then builds a SQL query grouping live rows by workspace, subject, and item class. It selects workspaces where a group has enough old copies to justify deduplication. The output is the query object.

**Call relations**: The manifest uses this as the candidate selector for the memory_dedup job. The dedup job then runs only where there is probably something to collapse.

*Call graph*: 2 external calls (now, select).


##### `_class_count`  (lines 766–770)

```
def _class_count(item_class: ItemClass) -> sa.Function[int]
```

**Purpose**: Creates a SQL count expression for rows of one memory item class inside a grouped query. It is a small helper for candidate queries that need to compare counts of facts versus paragraphs.

**Data flow**: It receives an item class such as fact, section, or overview. It builds a database expression that counts only rows whose item_class matches that value. The result is a SQL expression used inside a larger query.

**Call relations**: _summarizable_workspaces and _overviewable_workspaces call this when deciding whether a workspace has enough facts to write a paragraph or already has a paragraph that may need updating or removal.

*Call graph*: called by 2 (_overviewable_workspaces, _summarizable_workspaces); 1 external calls (case).


##### `_summarizable_workspaces`  (lines 773–796)

```
def _summarizable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces needing section paragraph work. It catches both cases: enough facts to write a section, or an existing section paragraph that may now be stale.

**Data flow**: It builds a SQL query over live fact and section rows, grouped by workspace, subject, and memory kind. It uses class-specific counts to keep groups with enough facts or any existing section paragraph, then returns distinct workspace ids as a query object.

**Call relations**: The manifest uses this as the candidate selector for the memory_section job. It calls _class_count to express the two thresholds cleanly in one grouped database scan.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_overviewable_workspaces`  (lines 799–821)

```
def _overviewable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces needing the shared memory overview paragraph written or removed. It only considers the shared subject, because the overview belongs to the shared page.

**Data flow**: It builds a SQL query over live shared fact and overview rows, grouped by workspace. It keeps workspaces with enough facts for an overview or with any existing overview paragraph. It returns the query object.

**Call relations**: The manifest uses this as the candidate selector for the memory_overview job. It calls _class_count to distinguish fact rows from the overview paragraph row.

*Call graph*: calls 1 internal fn (_class_count); 2 external calls (or_, select).


##### `_peopled_workspaces`  (lines 824–838)

```
def _peopled_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces where the People profile pass might have something to write. A workspace with no shared facts has no material for people entries.

**Data flow**: It takes no input and builds a SQL query selecting distinct workspace ids that have at least one live shared fact. The output is the query object.

**Call relations**: The manifest uses this as the candidate selector for the memory_people job. The profile writer does the detailed reading and writing after the scheduler chooses candidate workspaces.

*Call graph*: 1 external calls (select).


##### `_curatable_workspaces`  (lines 841–857)

```
def _curatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces with memory pages large enough for the whole-page curation pass. It avoids spending model work on tiny pages.

**Data flow**: It builds a SQL query over live fact rows, grouped by workspace and subject. It keeps groups with at least the minimum number of rows needed for a page pass and returns distinct workspace ids as a query object.

**Call relations**: The manifest uses this as the candidate selector for the memory_page_pass job. The selected workspaces are then handed to curate_memory_pages.

*Call graph*: 1 external calls (select).


##### `manifest`  (lines 860–1018)

```
def manifest() -> Manifest
```

**Purpose**: Creates the extension manifest, which is the system’s registration record for memory. It tells the host what tools, object types, hooks, jobs, search provider, and web surface this extension offers.

**Data flow**: It takes no input. It constructs ToolDef objects for memory search, memory update, correction recording, first-run recording, and page-fact rebuilding; HookSpec objects for prompt recall and page changes; JobSpec objects for indexing, consolidation, deduplication, summaries, profiles, and page curation; plus object, search-provider, and surface definitions. It returns one Manifest object containing all of that.

**Call relations**: The host calls this during extension startup. Everything else in the file is made reachable through this returned manifest: tool handlers are called when agents invoke tools, hooks run on events, and jobs run on their schedules with candidate queries.

*Call graph*: 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `startup registration, then each user prompt submit`

This file is the front door for the objectives extension. An objective is work that may take more than one conversation turn, such as delegating tasks, waiting for another worker, or checking whether a rollout succeeded. The main problem it solves is memory loss between turns: when the agent wakes up after a heartbeat or a hand-back from another worker, it may not have its previous working context. So this extension injects a compact “frontier” into the next prompt, meaning the still-relevant edge of the objective: the directive, progress counts, open steps, unmet conditions, and any question already raised with the user.

The file has two main pieces. First, it defines a long prompt section that teaches the agent when to create objectives, what counts as a meaningful step, and why acceptance conditions must be real checks rather than self-made proof. Second, it registers a hook that runs when a user prompt is submitted. That hook looks up whether the current conversation has an objective. If it does, it builds a readable block of text and injects it into the agent’s context.

The result is like putting a job card on a worker’s desk at the start of every shift. The worker does not need to remember everything from yesterday; the card says what job is active, what is done, what is blocked, and what must be checked next.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function adds the current objective’s status into the agent’s prompt at the start of a turn. It exists so long-running work stays visible even when the agent’s short-term working memory has been reset.

**Data flow**: It receives a hook context, which includes the current turn and access to the extension’s storage. If there is no current turn, it returns nothing. Otherwise it opens a storage transaction, finds the objective tied to this conversation and workspace, and stops if none exists. When an objective is found, it records a metric, formats the objective name, directive, progress, open steps, acceptance conditions, blocks already raised with the user, and attempted-but-unchecked steps into plain text. It returns an InjectContext containing that text, which means the text will be placed into the agent’s context for the turn.

**Call relations**: This function is registered by manifest as the handler for the user_prompt_submit hook, so the platform calls it when a user prompt begins processing. It asks agent_current for the active workspace, uses Objectives to read the stored objective view, uses condition_summary to turn acceptance checks into readable lines, emits a metric about frontier injection, and finally hands the built text to InjectContext so the broader system can insert it into the prompt.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host application what the objectives extension provides. It packages the extension name, version, tools, prompt guidance, and prompt-injection hook into a Manifest object the system can load.

**Data flow**: It takes no input. It gathers constants and imported tool definitions from this file, creates a hook specification that connects user prompt submission to _inject_frontier, creates a prompt section containing the objectives guidance, and returns a Manifest that describes the whole extension. It does not change stored objective data itself; it describes how the extension should be wired into the system.

**Call relations**: The extension loader calls this function when the objectives extension is being registered. The returned Manifest hands the host system four tools for planning, running, recording, and reading objectives, plus a HookSpec that causes _inject_frontier to run during prompt submission, plus a PromptSection that teaches the agent how to use objectives correctly.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Scheduled automation
These manifests register recurring jobs, scheduled task objects, reports, monitors, and self-improvement evaluations.

### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `startup and recurring scheduled job registration`

This file is the front door for the monitors extension. A monitor is a long-lived watch that periodically runs a shell probe inside a conversation’s sandbox, like setting an alarm clock that wakes up every so often to check whether something has changed. The extension needs to declare three things to the larger system: what kind of object it stores, what tool users or agents can call to work with it, and what background job should run on a schedule.

The file gives the extension a name and version, then defines a runner job called `monitor_runner`. Its schedule string means the job is eligible to run once per minute. When the job fires, it calls a small helper, `_probe`, which creates a `MonitorRunner` and asks it to do the actual checking.

The important efficiency detail is the `candidates` setting. Instead of waking every workspace every minute, the job asks `due_monitor_workspaces()` which workspaces actually have monitors ready to be probed. That keeps idle workspaces cheap. In short, this file is not where monitor checking logic lives; it is the registration sheet that connects that logic to the host application’s extension, tool, object, and job systems.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job’s small entry point. When the monitor runner job fires, this function starts the real monitor-checking work for the current extension context.

**Data flow**: It receives an `ExtensionContext`, which is the host system’s bundle of information and services for this extension run. It uses that context to create a `MonitorRunner`, then calls its `run` method. Nothing is returned; the effect is that due monitor probes are carried out by the runner.

**Call relations**: The job declared in `manifest` points to `_probe` as its handler. `_probe` does not decide which monitors are due or how probes work; it hands the context to `MonitorRunner`, which is the component responsible for performing the checks.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension declaration that the host system reads to learn what the monitors extension provides. It registers the monitor tool, monitor object type, and the recurring monitor runner job.

**Data flow**: It starts from fixed extension details such as the name, version, job name, and schedule. It calls `due_monitor_workspaces()` to describe which workspaces should be considered when the scheduled job runs, then packages everything into a `Manifest` object. The result is a complete declaration the host can load.

**Call relations**: The host system calls `manifest` when loading the extension. Inside that declaration, it creates a `JobSpec` whose handler is `_probe`; later, when the scheduler sees due candidate workspaces, that job handler is what launches `MonitorRunner`.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/report_digest/ufo_ext_report_digest/manifest.py`

`orchestration` · `startup, scheduled background runs, and admin tool invocation`

The report-digest extension creates short radar-style entries for published reports. This file does not do the writing itself. Instead, it declares the pieces the rest of the system should know about, and it connects user actions and scheduled background work to the real digest-writing code.

At startup, the platform asks this file for a manifest, which is like a registration form for the extension. The manifest says where the shared writing instructions live, what tool should appear for admins, what background job should run every ten minutes, and what kind of report object the extension belongs to.

The scheduled job calls `write_digests`. That function checks that the background language model and report-storage access are available, then starts `DigestWriter`, which reads reports that still need digest entries and writes them.

The admin tool calls `rebuild_report_digest_handler`. It is deliberately limited: only workspace admins may use it, and it does not rewrite entries immediately. Instead, it marks recent reports as needing digest work again. The normal scheduled job then rewrites them later, in batches. This keeps one tool click from turning into a large burst of model work.

#### Function details

##### `write_digests`  (lines 38–43)

```
async def write_digests(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job entry point for writing missing report-digest entries. It makes sure the job has both a background model to write with and blob access to read published report content before starting the digest writer.

**Data flow**: It receives an `ExtensionContext`, which is the bundle of services and workspace information the extension gets from the platform. It reads the model and member-context blob access from that context. If either is missing, it stops with a clear runtime error. If both are present, it creates a `DigestWriter` with those resources and runs it; the result is that due reports can receive stored digest entries.

**Call relations**: The manifest registers this function as the handler for the `report_digest` scheduled job. When the scheduler chooses work for a workspace, this function hands control to `DigestWriter`, which does the actual reading and writing.

*Call graph*: 1 external calls (__init__).


##### `rebuild_report_digest_handler`  (lines 50–73)

```
async def rebuild_report_digest_handler(ctx: ToolContext, args: RebuildReportDigestInput) -> ToolResult
```

**Purpose**: This is the admin tool action for asking the system to rewrite recent report-digest entries. It protects the action so ordinary members cannot trigger a feed-wide rebuild.

**Data flow**: It receives a `ToolContext`, which describes the current tool request, and an empty input model, meaning the tool takes no settings from the user. It first checks that extension context is available. Then it asks whether the speaker is a workspace admin. If not, it raises an error message. If the speaker is allowed, it runs `DigestRebuild`, which marks reports from the recent window as due for digest work again. It returns a `ToolResult` containing plain text: either that there was nothing to rebuild, or how many reports were marked for rewriting.

**Call relations**: The manifest exposes this function as the handler for the `rebuild_report_digest` tool. When an admin uses that tool, this function performs the permission check, calls `DigestRebuild` to mark work as due, and returns user-facing text through `TextContent` and `ToolResult`. The actual rewriting is left to the normal scheduled digest job.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 76–114)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension registration that the UFO platform reads. It names the extension and declares its skill, admin tool, scheduled job, object binding, and storage permissions.

**Data flow**: It takes no input. It uses constants from this file and imported objects from the digest, object, writer, job, tool, and manifest modules to assemble a `Manifest`. The returned manifest tells the platform what this extension is called, where its writing instructions are, which tool to show, which job to schedule, how to choose candidate workspaces, what report object it belongs to, and that it needs member-context read access.

**Call relations**: The platform calls this during extension discovery or startup. Inside it, `SkillSpec` points to the shared writing standard, `ToolDef` connects the admin rebuild action to `rebuild_report_digest_handler`, `JobSpec` connects the ten-minute scheduled job to `write_digests`, and `owner_candidates(undigested_workspaces)` tells the scheduler how to find workspaces with digest work waiting.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `startup and recurring job registration`

This file is like the extension’s sign-up sheet. Without it, the main system would not know that scheduled tasks exist, what tool an agent can use to pause and wait, or which background jobs should run on a clock.

The file declares the extension name and version, then defines two small job entry functions. One starts the runner that looks for scheduled tasks whose time has arrived. The other starts the runner that resumes conversations that were paused until a later time. These are separate jobs because they are different kinds of waiting work. If one gets stuck or fails, it should not block the other.

The manifest also says how often those jobs should wake up, using a cron-style schedule string, which is a compact way to describe repeated times. Both jobs use candidate functions that first find only workspaces with due work, so the dispatcher does not waste effort checking workspaces that have nothing waiting.

Finally, the manifest declares the agent skill files for task scheduling, the special conversation slot used for automations, and a dependency on memory search. In short, this file connects the scheduled-tasks extension to the larger UFO platform.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: Starts the scheduled-task runner for one job fire. This is the background job entry used when the system wants to execute scheduled tasks whose time has arrived.

**Data flow**: It receives an ExtensionContext, which is the extension’s access pass to system services and workspace data. It builds a ScheduledTaskRunner with that context, then asks the runner to do its work. Nothing is returned; the useful result is that due scheduled tasks may be triggered.

**Call relations**: The manifest gives this function to the scheduled-task JobSpec as the job’s handler. When the clock-based job fires for a workspace with due scheduled-task rows, the platform calls this function, and it hands the real work to ScheduledTaskRunner.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: Starts the pause runner for one job fire. This is used to resume conversations that deliberately paused until a certain time.

**Data flow**: It receives an ExtensionContext from the job system. It creates a PauseRunner with that context, then runs it. It returns no value; the effect is that due paused conversations can be resumed.

**Call relations**: The manifest gives this function to the pause-runner JobSpec. When the recurring pause job fires for a workspace with due paused rows, the platform calls this function, and it delegates the actual resume work to PauseRunner.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: Builds the official description of the scheduled-tasks extension for the host system. The platform uses this to discover what the extension adds and which recurring jobs it should schedule.

**Data flow**: It starts from constants in this file, such as the extension name, version, schedule, skill folder, and job names. It also asks helper functions for the workspace-candidate filters for due scheduled tasks and due pauses. It packages all of that into a Manifest object, which becomes the system-readable declaration of the extension.

**Call relations**: The host platform calls this during extension loading. Inside, it creates job specifications for the scheduled-task runner and pause runner, creates skill specifications for the task-scheduling skill files, and returns one Manifest that ties together tools, objects, jobs, skills, dependencies, and conversation slots.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `extension load and scheduled job execution`

This file is the extension’s front desk sign-up sheet. When the larger system loads extensions, this file says: “there is a self-improvement extension, here is its name and version, and here is the timed job it wants to run.” The timed job is like a daily maintenance task on a calendar. It is not triggered by new proposals or saved data; it runs because the clock says it is time.

When the job runs, `_tick` builds the small chain of parts needed for self-improvement. First it checks that model access is available. In this project, “model” means the language model service the extension uses to propose and judge changes. If that service is missing, the job stops with a clear error, because the extension cannot do useful work without it.

If model access is present, the file wraps it in `ModelAccessLeg`, then gives that same wrapped access to the prompt proposer and the candidate evaluator. The proposer creates possible prompt improvements. The evaluator replays past work and judges whether a candidate is better. `ImproveCron` is then given those pieces and asked to run the full scheduled cycle.

One important detail is that the job asks for the deploy model, not a cheaper background model. That matters because replay may send a whole archived conversation back to the model, and those conversations were sized for the deploy model’s larger limits.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the function that runs when the scheduled self-improvement job fires. It checks that language model access is available, builds the proposer and evaluator, and starts one self-improvement cycle.

**Data flow**: It receives an `ExtensionContext`, which carries services from the host system, including model access and workspace information. If the context has no model, it raises an error and nothing else runs. If the model is present, it wraps that model access, passes it into the prompt proposer and candidate evaluator, builds an `ImproveCron` runner with those pieces, and awaits its `run` process. The result is no returned value, but the scheduled improvement work may read past trajectories, propose prompt changes, replay examples, and grade candidates through the objects it creates.

**Call relations**: The job declared by `manifest` points to `_tick` as its handler, so the host calls `_tick` when the clock schedule matches. `_tick` then creates `ModelAccessLeg`, `PromptProposer`, and `CandidateEvaluation`, gives them to `ImproveCron`, and hands off control to the cron runner for the actual improvement workflow.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It returns the extension name, version, and the scheduled job that should be registered.

**Data flow**: It starts from fixed constants: the extension name, version, job name, and cron schedule. It asks `trajectory_workspaces()` for the candidate workspaces the job can run against, builds a `JobSpec` that says when to run and which function to call, then wraps that job inside a `Manifest`. The output is a manifest object the host can read during extension loading.

**Call relations**: The host system calls `manifest` while discovering or loading extensions. The returned manifest tells the host to register one scheduled job. Later, when that job’s schedule fires, the host invokes `_tick`, which performs the live self-improvement run.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-extension-data-store` — The per-workspace extension storage area where optional features save their own small durable JSON state.
- `reg-http-route-registry` — Process-local mounted HTTP, WebSocket, callback, and extension route dispatch table used by the running web server.
- `reg-backend-provider-registry` — Process-local registry mapping provider names to active backend implementations for models, search, embeddings, memory, connectors, browser access, auth, billing, and feature services.
- `reg-object-kind-action-registry` — Process-local registry of built-in and extension object kinds, schemas, actions, visibility rules, and handlers used by the portal object APIs.
- `reg-conversation-slot-provider-registry` — Registered providers that summarize and read extension conversation slots such as artifacts, sources, sites, automations, and task panels.
