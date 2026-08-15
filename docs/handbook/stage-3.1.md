# Core extension and skill loading runtime  `stage-3.1`

This stage is shared startup support for the system’s extension and skill features. It prepares extra abilities before the main agent work begins, much like laying out tools on a workbench.

The main entry point is `ufo.ext.loader`. It looks for installed extensions, checks that the selected ones are allowed and safe to combine, then converts their declarations into usable pieces: tools the agent can call, hooks that run at set moments, skills, object types, credentials, and backend services. This gives the rest of the system one organized view of what extensions provide.

Skills get their own runtime in `ufo.skills.runtime`. A skill is a folder of instructions and files that teach the agent how to do a task. This file reads those folders, follows dependencies between skills, and copies the needed material into a sandboxed workspace, which is an isolated area where the agent can work without touching the original files.

The two `__init__.py` files simply make `ufo.ext` and `ufo.skills` importable Python packages.

## Files in this stage

### Extension loading package
Extension package files that expose the extension namespace and orchestrate discovery, validation, and capability assembly.

### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and per-turn setup`

Extensions in this project do not register themselves by calling into the running app. Instead, they publish small package entry points, like signs on a shop door saying what they offer. This file reads those signs, asks each extension for its Manifest, and builds the active extension set.

A lockfile can pin exactly which extensions are allowed and what their installed code should look like. When the lockfile exists, this loader checks each extension’s digest, which is a fingerprint of its source files. If an extension is missing or changed, startup fails instead of quietly running unexpected code. Without a lockfile, the system behaves like a development setup and loads everything it discovers.

After discovery, this file translates manifests into the practical pieces used at runtime: the tools available during a turn, credential injection rules, object registries, search and embedding backends, memory search providers, runtime skills, subagents, durable reply surfaces, and hook chains. A hook is extension code that reacts to moments in a turn, such as before a tool runs or after it finishes.

The important theme is “fail early.” Name collisions, missing credential support, unknown backend choices, unsafe credential environment variables, and invalid hook outcomes are rejected here so later request handling does not break in confusing ways.

#### Function details

##### `lockfile_path`  (lines 150–151)

```
def lockfile_path() -> Path
```

**Purpose**: Chooses where the extension lockfile lives. It lets an operator override the default path with an environment variable.

**Data flow**: It reads the UFO_LOCKFILE environment variable if it is set. If not, it uses the default file name ufo.lock. It returns that location as a Path object.

**Call relations**: When load_manifests needs to know whether the deployment is pinned, it asks lockfile_path for the file to check.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 154–155)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads the pinned extension list from disk and turns it into a validated Lockfile object.

**Data flow**: It receives a path, reads the file text, parses the JSON, and validates that it matches the expected lockfile shape. The result is a Lockfile with the pinned UFO version and extension pins.

**Call relations**: load_manifests calls this after it finds a lockfile, so it can compare installed extensions against the pinned list.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 158–159)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a Lockfile object to disk as formatted JSON. This is the matching writer for the reader used at startup.

**Data flow**: It receives a destination path and a Lockfile object. It serializes the lockfile with indentation, adds a final newline, and writes that text to the path.

**Call relations**: This function is used by tooling that creates or updates the lockfile, while load_manifests later reads the same format.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 162–172)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension package in the current Python environment. It also prevents two extensions from claiming the same manifest name.

**Data flow**: It asks Python’s package metadata for all ufo.extension entry points. Each entry point is loaded and called to produce a Manifest. The function returns a dictionary from extension name to the Manifest and entry point that produced it.

**Call relations**: load_manifests uses this as the raw installed-extension list. migration_locations also uses it to connect active manifests back to their installed package locations.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 175–186)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds every installed extension pack. A pack is a bundle that selects a coherent group of extensions and may add its own skills or onboarding steps.

**Data flow**: It asks Python’s package metadata for ufo.pack entry points, loads and calls each one, rejects duplicate pack names, and returns a dictionary from pack name to Pack.

**Call relations**: _pack_manifests calls this when configuration asks to activate a named pack.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 189–194)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds the import information for the top-level Python package behind an extension entry point. This is needed to locate source files and migrations.

**Data flow**: It receives an EntryPoint, takes the first part of its module name, and asks Python where that package comes from. It returns the package’s ModuleSpec, or raises an error if the source cannot be found.

**Call relations**: extension_digest uses it to find the files that should be fingerprinted. migration_locations uses it to find a package’s migrations directory.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 197–202)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the folder that belongs to an extension package. This matters because migration files, if present, live beside the extension’s code.

**Data flow**: It receives a ModuleSpec. For a package directory, it returns that directory. For a single-file module, it returns the parent folder of that file.

**Call relations**: migration_locations calls this after _entry_spec so it can look for a migrations subfolder.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 205–225)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Creates a stable fingerprint of an installed extension’s source code. This lets the system detect drift or tampering when a lockfile pins an extension.

**Data flow**: It receives an EntryPoint, finds the package source, reads all relevant source files, and feeds both file names and file contents into a SHA-256 hash. It returns a string beginning with sha256: followed by the digest.

**Call relations**: load_manifests calls this for each pinned extension and compares the result with the digest stored in the lockfile.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 228–245)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Collects database migration folders from the active extensions. Migrations are scripts that update the database structure.

**Data flow**: It discovers installed extensions, loads the active manifest set, finds each active extension’s package directory, and checks for a migrations folder. It returns the existing migration folder paths as strings.

**Call relations**: The migration runner can use this list alongside core migrations, so extension-owned tables are created only for extensions that are active.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 248–273)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Builds the active set of extension manifests. It is the main gate between installed packages and what the running system actually trusts and uses.

**Data flow**: It discovers installed extensions and checks for a lockfile. Without one, every discovered extension becomes active. With one, only pinned extensions are loaded, and each must match its pinned digest. If a pack is requested, the active set is narrowed through _pack_manifests.

**Call relations**: Many later derivations depend on this active manifest tuple. migration_locations also calls it so database migrations match the same active extension set.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 276–308)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Turns a selected pack name into the exact manifest list it represents. It makes sure the pack exists and all bundled extensions are installed and active.

**Data flow**: It receives a pack name and the current active extension map. It finds the Pack, pulls in the manifests for each bundled extension, checks for missing or inactive extensions, then appends a synthetic Manifest for the pack’s own skills and onboarding steps.

**Call relations**: load_manifests hands off to this when configuration selects a pack, so all later readers see the pack-shaped manifest set.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 311–320)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Collects command-line credential declarations from connector extensions. These describe environment variables used to expose usable grants to tools or proxies.

**Data flow**: It receives manifests, walks through their connectors, and keeps only connectors that declare a CLI credential. It returns a dictionary keyed by provider name.

**Call relations**: injecting_slots uses this to check that connector credential variables do not conflict with credential-slot variables.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 323–396)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Finds credential slots that should be injected into tool sandboxes or outbound requests, and checks that their exported names are safe. It prevents subtle authentication bugs caused by two declarations fighting over the same variable or sentinel.

**Data flow**: It receives manifests, gathers credential slots with injection rules, and compares their sentinels, environment variable names, host choices, and metering dimensions. If declarations conflict, it raises an error. Otherwise it returns the injectable slots.

**Call relations**: It calls connector_clis so connector-provided credential exports share the same conflict checks as extension credential slots.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 399–456)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, audience: Audience, public_base_url: str |
```

**Purpose**: Builds the complete tool list available during a turn. It combines built-in tools, extension tools, connector tools, and object-operation tools.

**Data flow**: It receives active manifests plus optional credential, index, embed, audience, and URL inputs. For each extension with tools or object kinds, it builds an ExtensionContext, adds its tools, remembers which context belongs to each extension tool, binds object kinds, and finally adds object verb tools. It returns the tool definitions and the extension context map.

**Call relations**: During turn setup, the engine uses this to know what can be called and which extension context to pass when an extension-owned tool runs. It calls core_object_kinds and object_registry so object types appear as tools too.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `member_object_registry`  (lines 459–493)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, public_base_url: str | No
```

**Purpose**: Builds the object registry used for member-facing reads outside a live turn. This lets the portal or similar surfaces list and read objects from core and extensions.

**Data flow**: It receives manifests and optional backing services. It binds core object kinds, then extension object kinds with workspace-level contexts, checks credential requirements, adds core-derived credential and extension object kinds, and returns a registry keyed by object name.

**Call relations**: It follows the same object-kind rules as turn_tools, but it is used outside a conversation turn, so the contexts are not audience-scoped.

*Call graph*: calls 1 internal fn (core_object_kinds); 3 external calls (__init__, context_for, object_registry).


##### `core_object_kinds`  (lines 496–523)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None) -> tuple[BoundKind, ...]
```

**Purpose**: Creates the object kinds that core itself exposes based on the active extensions. These include credential-slot objects and extension-summary objects.

**Data flow**: It receives manifests and optionally a credential store. It builds a credential ObjectKind from declared credential slots and an extension ObjectKind from active manifests. It returns both wrapped as BoundKind entries with no extension context.

**Call relations**: turn_tools, member_object_registry, and validate_ext_tools call this when assembling object registries that include both core and extension-owned kinds.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 6 external calls (__init__, __init__, __init__, __init__, named_extensions, declared_slots).


##### `skill_registry`  (lines 526–549)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the full skill registry for the deployment. Skills are reusable instruction or behavior bundles that can be loaded by name.

**Data flow**: It starts with core skills, then reads skill specifications contributed by active manifests from disk, including nested skills. It adds any generated skills too. If a skill name is already taken, it raises an error. It returns a SkillRegistry.

**Call relations**: Startup code can use this registry so later skill loading and skill-index rendering do not need to resolve duplicate names.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 552–556)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects all subagent profiles declared by active extensions. A subagent profile describes a specialized helper agent.

**Data flow**: It receives manifests, walks through their subagent lists in manifest order, and returns all profiles as one tuple.

**Call relations**: The server uses this output to build the SubagentRegistry. Duplicate-name checking happens when that registry is constructed.


##### `durable_surfaces`  (lines 559–565)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Identifies conversation surfaces whose replies should be delivered later by a writeback poller. A surface is durable when it declares a post handler.

**Data flow**: It receives manifests, scans their surface declarations, keeps surface names that have a post function, and returns them as a frozen set.

**Call relations**: Admission or turn-entry code can use this set to decide when to create writeback tracking rows.


##### `turn_subagent_grants`  (lines 568–578)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Collects extra tool permissions that extensions grant to subagent profiles they may not own. This lets one extension widen a subagent’s tool access without editing that subagent directly.

**Data flow**: It receives manifests, groups grants by target profile name, unions the granted tool names, and returns a dictionary from profile name to frozen set of tool names.

**Call relations**: The turn loop can fold these grants into a subagent profile’s own tool list, then intersect with the live tool registry so unknown tools simply do not appear.


##### `turn_runtime_skills`  (lines 581–601)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Asks active extensions for runtime-generated skills for the current bound agent or workspace. These are skills produced by code rather than only read from disk at startup.

**Data flow**: It receives manifests and optional backing services. For each extension with a runtime skill provider, it requires a credential store, builds an ExtensionContext, awaits the provider, and appends the returned RuntimeSkill objects. It returns all runtime skills as a tuple.

**Call relations**: Turn setup or agent binding code can call this when it needs live extension-provided skills. It uses context_for so each provider runs with its extension’s scoped access.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 607–627)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and builds the configured index backend. An index backend stores and searches indexed content, such as text records or embeddings.

**Data flow**: It receives manifests, a configured backend name, and an optional credential store. It uses default when no name is configured, searches extension index specs for that name, checks credential requirements, builds an ExtensionContext, and returns the backend from the spec factory. If none is found, it raises NotRegisteredError.

**Call relations**: Startup configuration calls this once and passes the resulting backend into other contexts and jobs that need indexing.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 630–650)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and builds the configured embedding client. An embedding client turns text into numeric vectors used for similarity search.

**Data flow**: It receives manifests, a configured name, and an optional credential store. It defaults the name when unset, searches extension embed specs, checks credentials, builds an ExtensionContext, and calls the matching factory. If no extension registers the selected name, it raises NotRegisteredError.

**Call relations**: Startup code resolves this backend and threads it into index work, memory tools, and extension contexts that need embeddings.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 653–678)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory search provider from the active extensions. Memory search lets the system retrieve relevant past information.

**Data flow**: It receives manifests, optional credential, index, and embed services, plus a provider name. It finds matching provider specs. If none exist, it returns None; if more than one exists, it raises an error. Otherwise it builds the provider with an ExtensionContext and wraps it as MemorySearch.

**Call relations**: Runtime setup can call this to enable the configured memory-search feature. It uses context_for so the provider gets the services and credentials declared by its extension.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 681–717)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Checks extension tool and object registrations early, before a deployment starts serving turns. It catches collisions and missing credential setup at boot instead of during a user request.

**Data flow**: It receives manifests and an optional credential store. It gathers built-in and extension tools, rejects tool-declaring extensions that need credentials when no credential key exists, builds an object registry including extension and core object kinds, adds object verb tools, and constructs a ToolRegistry to force validation.

**Call relations**: Boot code can call this as a safety check. It uses core_object_kinds and object_registry in the same style as turn_tools, but without building per-workspace extension contexts.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.__post_init__`  (lines 758–761)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that every hook in a HookChain belongs to the same audience as the chain. An audience is the scope of people or conversation context the hook is allowed to act within.

**Data flow**: After a HookChain is created, it flattens all bound hooks and compares each hook context’s audience to the chain audience. If any differ, it raises a ValueError. Otherwise the chain is left unchanged.

**Call relations**: This runs automatically when turn_hooks creates a HookChain, protecting HookChain.fire from mixing hooks with the wrong audience scope.


##### `HookChain.fire`  (lines 763–839)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one turn event and combines their results. It is the point where extensions can deny an action, change tool input or output, or inject extra context.

**Data flow**: It receives an event, payload, optional turn and agent records, and an optional speaker member ID. It walks through hooks registered for that event, skips tool-specific hooks when the tool name does not match, builds a HookContext, runs each hook with a timeout, validates the returned outcome, and folds the results. It returns a HookResolution describing denial, modified input or output, injected text, or fail-closed details.

**Call relations**: The turn engine calls this at specific moments such as before tool use or after tool use. For gating events, failures become denials so unsafe work does not proceed. For non-gating events, failures are logged and swallowed so observation hooks do not break the turn.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 842–886)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, tailer: TurnTailer | None=None, *, audience:
```

**Purpose**: Builds the hook chain used during a turn. It binds each extension’s declared turn-lifecycle hooks to that extension’s scoped context.

**Data flow**: It receives manifests, credential and service dependencies, an optional turn tailer, audience, and public URL. For each manifest with hooks, it requires a credential store, builds an ExtensionContext, ignores page_change hooks because they are run elsewhere, groups the remaining hooks by event, and returns a HookChain.

**Call relations**: Turn setup calls this so HookChain.fire can later run extension reactions at the right moments. It creates BoundHook objects and then constructs the final HookChain.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### `core/src/ufo/ext/__init__.py`

`other` · `import time`

In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. This file is that marker for the `ufo.ext` package. Think of it like a label on a drawer: the label does not hold any tools itself, but it tells Python that the drawer is part of the project’s organized module system.

Because the file is empty, it does not set up configuration, expose shortcuts, or run any startup behavior. Its main value is structural. Without it, depending on the Python version and packaging setup, imports that expect `ufo.ext` to be a normal package might fail or behave differently. Keeping the file also makes the project layout clearer to readers: this directory is meant to contain extension-related code.


### Skill runtime package
Skill package files that expose skill imports and define skill folder loading, dependency resolution, and sandbox placement.

### `core/src/ufo/skills/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. Think of it like a label on a drawer: the drawer may hold many useful tools, but this label simply tells Python, “this drawer belongs to the project and can be opened by name.” Without this file, depending on the Python version and import setup, code elsewhere might not be able to reliably import modules under `ufo.skills`. Because it is empty, it does not create objects, run setup code, or change behavior when imported. Its value is structural: it helps organize the codebase and keeps imports predictable.


### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading during conversation turns`

A skill is a small folder of help for the agent: a SKILL.md file with a short description and instructions, plus optional extra files such as examples or templates. This file is the bridge between those folders and a running conversation. Without it, the system would not know which skills exist, which other skills they depend on, where to safely copy their files, or how to avoid repeating the same instructions in the model’s context.

The file first defines the shape of a parsed skill, including its name, description, instructions, dependencies, child-skill relationship, and bundled files. It then provides parsers that read SKILL.md frontmatter, which is a YAML metadata block at the top of the markdown file, and separate it from the human-readable workflow body.

It also builds registries: searchable lists of available skills. A registry can find a skill by name, expand a requested skill into the full set of skills it depends on, and produce a short index of top-level skills for prompts.

At load time, the file does two important things. It creates text to show the agent: the requested skill instructions, dependency instructions, and a compact tree of mounted files. It also writes the actual skill files into the sandbox under .skills/<name>/, using path containment checks so a skill cannot write outside its own safe area.

#### Function details

##### `skill_mount_root`  (lines 44–47)

```
def skill_mount_root(name: str) -> str
```

**Purpose**: Builds the workspace path where one skill’s files should be placed. It gives every skill its own folder under the shared .skills area.

**Data flow**: It receives a skill name, joins it to the standard skills mount directory, and returns a string such as the workspace’s .skills area plus that name. It does not read or change anything else.

**Call relations**: RuntimeSkill.mount_root calls this helper when code needs the safe root folder for a particular skill. It keeps path construction in one place so mounting and later file references agree.

*Call graph*: called by 1 (mount_root).


##### `RuntimeSkill.mounted_files`  (lines 66–67)

```
def mounted_files(self) -> dict[str, bytes]
```

**Purpose**: Returns all files that should be copied into the sandbox for a skill. This includes the original SKILL.md file and any bundled assets.

**Data flow**: It reads the RuntimeSkill’s raw SKILL.md text and stored asset files, encodes the markdown text as bytes, and returns one dictionary mapping relative file paths to file contents. It does not write the files itself.

**Call relations**: mount_skill calls this when it is ready to copy a skill into the sandbox. The method provides the exact file set that will be written.

*Call graph*: called by 1 (mount_skill).


##### `RuntimeSkill.mount_root`  (lines 69–70)

```
def mount_root(self) -> str
```

**Purpose**: Returns the sandbox folder where this specific skill should live. This is the per-skill version of the general mount-root helper.

**Data flow**: It reads the skill’s name, passes that name to skill_mount_root, and returns the resulting workspace path. Nothing is changed.

**Call relations**: mount_skill calls this before writing files, so every file from the skill is placed under the correct .skills/<name>/ area.

*Call graph*: calls 1 internal fn (skill_mount_root); called by 1 (mount_skill).


##### `LoadedSkill.prompt_body`  (lines 82–92)

```
def prompt_body(self) -> str
```

**Purpose**: Creates the text block that one loaded skill contributes to the model’s context. It labels whether the skill was directly requested or was brought in because another skill depends on it.

**Data flow**: It reads the loaded skill’s name, instructions, and optional dependency source. It builds a markdown header plus the skill instructions, and returns that text. It does not include bundled file contents.

**Call relations**: This method is used when the system turns loaded skills into prompt text. It supplies the per-skill instruction block that later gets combined with other skill blocks and the mounted-file tree.


##### `LoadedSkills.reseed`  (lines 108–125)

```
def reseed(self, loads: Iterable[tuple[LoadedSkill, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the tracker of which skill instructions are already visible to the model. This prevents the system from paying the context cost of repeating instructions the model already has.

**Data flow**: It receives previous load results and optional preloaded skills. It first clears the current tracker, then records every skill currently in context and separately records which ones the agent explicitly asked for. Preloaded skills are marked as in context but not as asked for.

**Call relations**: It calls LoadedSkills.reset before rebuilding its state. It is used when the conversation window or a subagent prompt needs an accurate memory of which workflows are already present.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 127–132)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of skills the agent explicitly asked for, then clears the tracker. This is useful at a boundary where old instruction bodies may be dropped but the system still wants to remember what should be reloaded later.

**Data flow**: It reads the asked-for set, sorts it into a stable tuple, clears both tracking sets, and returns the tuple of names. Afterward, the tracker is empty.

**Call relations**: It calls LoadedSkills.reset after collecting the names. It hands forward only the skills the agent chose directly, because reloading those will also bring back their dependencies.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 134–136)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered skill-loading state. It is the small shared cleanup step used before reseeding or after draining.

**Data flow**: It empties the in-context set and the asked-for set. It returns nothing and only changes this tracker object.

**Call relations**: LoadedSkills.reseed calls it before rebuilding state from known loads, and LoadedSkills.drain calls it after saving the asked-for names.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 139–145)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a SKILL.md file into its metadata block and its instruction body. It also checks that the file really starts and ends its metadata block correctly.

**Data flow**: It receives the full SKILL.md text. If the required opening fence is missing or the closing fence is not found, it raises an error. Otherwise, it returns two strings: YAML metadata and markdown body.

**Call relations**: parse_skill_content calls this before reading the skill’s name, description, and dependency list. It is the first validation gate for a skill file.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 148–153)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate child folders that are themselves skills. A child skill is recognized by having its own SKILL.md file.

**Data flow**: It receives a directory path, looks at its direct children, keeps only directories that contain SKILL.md, sorts them, and returns that list. It does not inspect deeper descendants directly.

**Call relations**: parse_skill uses this so a parent skill does not accidentally bundle a child skill’s files as its own assets. discover_skills uses it to recursively register child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 156–189)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory collection of skill files into a RuntimeSkill object. This lets skills loaded from disk and skills saved as bytes go through the same validation rules.

**Data flow**: It receives a claimed folder name, a mapping of file paths to bytes, and optional registry and parent names. It requires SKILL.md, decodes it, splits metadata from instructions, reads YAML metadata, checks that the frontmatter name matches the folder name, collects non-SKILL.md files as assets, and returns a RuntimeSkill.

**Call relations**: parse_skill calls this after reading files from disk. Internally it calls _split_frontmatter, yaml.safe_load to parse the YAML metadata, and PurePosixPath to identify SKILL.md files by path name.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 192–201)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill directory from disk and parses it into a RuntimeSkill. It keeps child-skill folders separate so the parent does not claim the child’s files.

**Data flow**: It receives a filesystem directory path and optional registry and parent names. It finds child skill directories, walks the directory tree for files, excludes files inside child skill folders, reads the remaining files as bytes, and passes them to parse_skill_content. The result is one RuntimeSkill for the directory itself.

**Call relations**: discover_skills calls this for each skill directory it registers. This function calls _child_skill_dirs and pathlib’s recursive file walk before handing the collected bytes to parse_skill_content.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 204–222)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, then returns them as a flat name-to-skill map. This lets nested folders be registered under path-like names such as parent/child.

**Data flow**: It receives a skill directory and optional names. It parses the current directory as one RuntimeSkill, stores it by registry name, finds immediate child skill directories, recursively discovers each child, and merges those results into one dictionary.

**Call relations**: _load_core_skills calls this while building the built-in skill registry. It calls parse_skill for the current folder and _child_skill_dirs to find child skills to recurse into.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 225–232)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the project’s built-in skills from the core skills folder. This happens when the module is imported so the built-in registry is ready for use.

**Data flow**: It receives a root directory, lists visible subdirectories that are not hidden or private-looking, discovers skills inside each one, and returns a dictionary keyed by skill name.

**Call relations**: It calls discover_skills for each top-level built-in skill directory. The module uses its result to create CORE_SKILLS_BY_NAME, CORE_SKILLS, and the core SkillRegistry.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 249–254)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up one skill by name and gives a helpful error if it does not exist. This is the registry’s basic “find this skill” operation.

**Data flow**: It receives a name, checks the registry dictionary, and returns the matching RuntimeSkill. If the name is missing, it builds a readable list of available names and raises a ValueError.

**Call relations**: SkillRegistry.closure and its inner add step call this whenever they need to resolve requested skills or dependency names into actual RuntimeSkill objects.

*Call graph*: called by 2 (closure, add).


##### `SkillRegistry.closure`  (lines 256–279)

```
def closure(self, *names: str) -> tuple[LoadedSkill, ...]
```

**Purpose**: Expands requested skill names into the full load set: the requested skills plus every dependency they require. It keeps each skill only once and remembers whether a skill was directly requested or pulled in by another skill.

**Data flow**: It receives one or more skill names. It first records the unique requested names as direct LoadedSkill entries, then walks each requested skill’s dependencies, adding each missing dependency and its own dependencies. It returns an ordered tuple of LoadedSkill objects.

**Call relations**: core/src/ufo/loop/engine._loaded_skill_closures calls this when a turn needs to know what a requested skill load actually includes. It calls SkillRegistry.named to resolve names and creates LoadedSkill entries for direct and dependency-loaded skills.

*Call graph*: calls 1 internal fn (named); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 269–274)

```
def add(skill: RuntimeSkill, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency skill and recursively adds the dependencies of that dependency. It is the inner worker that makes dependency expansion cycle-safe and non-duplicating.

**Data flow**: It receives a RuntimeSkill and the name of the skill that pulled it in. If the skill is already in the loaded dictionary, it stops. Otherwise, it stores a LoadedSkill marked as a dependency and repeats the process for each dependency listed by that skill.

**Call relations**: This helper is used only inside SkillRegistry.closure. It calls SkillRegistry.named to turn dependency names into RuntimeSkill objects and creates LoadedSkill records as it expands the dependency chain.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 281–289)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Produces the short list of skills that should be shown as loadable choices. It includes only top-level skills, not child skills.

**Data flow**: It reads all skills in registry order, filters out skills that have a parent, and returns tuples of skill name and description. It does not change the registry.

**Call relations**: core/src/ufo/serve._mount_shared_surfaces calls this to fill the prompt area that tells the agent which skills are available. Child skills are left out because they are meant to be reached through parent-skill instructions.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.merged_with`  (lines 291–303)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that adds user-saved skills after the existing core and pack skills. It refuses to let a user skill replace an existing skill with the same name.

**Data flow**: It copies the current registry mapping, checks each user skill, logs and skips any name collision, adds non-colliding user skills, and returns a new SkillRegistry. The original registry is not modified.

**Call relations**: This is used when workspace-specific user skills need to be added to the active registry. It calls the logging function when a user skill tries to shadow an existing skill, then constructs a fresh SkillRegistry.

*Call graph*: 2 external calls (__init__, log).


##### `_mounted_tree`  (lines 309–327)

```
def _mounted_tree(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Builds a compact text tree showing all files mounted for a skill load. This helps the agent know where files are without repeating long path prefixes over and over.

**Data flow**: It receives the LoadedSkill entries in one load. It gathers every mounted file path under each skill name, sorts them, expands directory levels once, and returns an indented tree string rooted at the .skills mount directory.

**Call relations**: loaded_context calls this after preparing instruction blocks. It uses PurePosixPath to split paths into clean path segments for the tree display.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 330–344)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Creates the full text shown to the model for one skill load. It includes new skill instructions, a note for instructions already in context, and a tree of mounted files.

**Data flow**: It receives LoadedSkill entries and an optional set of skill names already in context. It renders prompt bodies only for new skills, collects repeated names into a short note, appends the mounted-file tree, and returns one combined string.

**Call relations**: This is shared by normal skill loading and preloading flows. It calls _mounted_tree so the final prompt tells the agent both what instructions were loaded and where the skill files were placed.

*Call graph*: calls 1 internal fn (_mounted_tree).


##### `mount_skill`  (lines 347–355)

```
async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Copies a skill’s files into the sandboxed workspace under that skill’s own .skills folder. It protects the workspace by checking that every authored file path stays inside the skill’s mount root.

**Data flow**: It receives a SandboxSession and a RuntimeSkill. It asks the skill for its mount root and file contents, checks each relative path against that root with contained_relative, and asynchronously writes each file into the sandbox. The result is files appearing in the workspace; the function returns nothing.

**Call relations**: This is the file-writing half of skill loading. It calls RuntimeSkill.mount_root and RuntimeSkill.mounted_files to know where and what to write, contained_relative to prevent path escape, and SandboxSession.write_file to perform the actual sandbox write.

*Call graph*: calls 3 internal fn (write_file, mount_root, mounted_files); 1 external calls (contained_relative).
