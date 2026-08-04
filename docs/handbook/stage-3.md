# Extension Discovery and Capability Registration  `stage-3`

This stage runs during startup, before any user request is handled. It is the system’s plug-in intake desk. The loader first finds installed extensions from lockfiles and manifest files, checks which ones are allowed, and turns their “registration cards” into live choices the rest of the system can use.

The provider and backend registrations add outside services, such as AI model providers, embeddings, search, and Redis coordination, so later code can ask for these services through a common interface. The surface registrations announce places people can interact with the system, such as web pages, debugger routes, and UFO channels. The tool, skill, object, and subagent registrations add practical abilities, connectors, helper agents, credential needs, and object types.

The direct manifests add more cards to the same desk. Self-improvement registers a scheduled evaluation job. Sites adds website-building tools, a site-viewing surface, a site chat object, and a website-building subagent. Slack adds routes, workspace secrets, setup skills, and tools. Sources adds connector backends, credentials, object types, an authentication proxy, and a hook for syncing external content.

## Sub-stages

- [Provider and Backend Extension Registration](stage-3.1.md) `stage-3.1` — 5 files
- [Surface Extension Registration](stage-3.2.md) `stage-3.2` — 3 files
- [Tool, Skill, Object, and Subagent Extension Registration](stage-3.3.md) `stage-3.3` — 14 files

## Files in this stage

### Extension loading orchestration
The core loader discovers installed extensions, decides which may run, and converts their manifest declarations into registered runtime capabilities.

### `core/src/ufo/ext/loader.py`

`orchestration` · `startup and per-turn setup, with hooks active during turn handling`

Extensions let UFO grow without hard-coding every tool, connector, skill, database migration, or hook into the core app. This file is the bridge between an installed Python package and the running system. It discovers extension packages through Python entry points, asks each one for a Manifest, and then decides which manifests are active. If a lockfile exists, it acts like a sealed shipping list: only pinned extensions may run, and their source code must still match the recorded digest. If there is no lockfile, the system behaves more like a development setup and loads everything it can find.

After discovery, the file converts those manifests into practical runtime pieces. It builds the tool list a turn may call, the object registry used for object-style reads and actions, the skill registry, subagent profiles, memory search providers, embedding and indexing backends, credential injection rules, and hook chains. It also performs early safety checks, such as rejecting duplicate tool names or missing credential setup, so the app fails at startup instead of halfway through a user request.

The HookChain class is the runtime part of this file. During a turn, it runs extension hooks before or after important events. Some hooks may block or edit work; others only observe. Gating hooks fail closed, like a locked door: if they crash, the action is denied rather than allowed by accident.

#### Function details

##### `lockfile_path`  (lines 139–140)

```
def lockfile_path() -> Path
```

**Purpose**: Finds the lockfile location the current process should use. This lets operators override the default ufo.lock path with an environment variable.

**Data flow**: It reads the UFO_LOCKFILE environment variable. If that variable is set, it turns that value into a filesystem path; otherwise it uses the default path ufo.lock. It returns the chosen Path object and changes nothing else.

**Call relations**: When load_manifests needs to know whether this deployment is pinned or in development mode, it asks lockfile_path where to look before reading or ignoring the lockfile.

*Call graph*: called by 1 (load_manifests); 1 external calls (Path).


##### `read_lockfile`  (lines 143–144)

```
def read_lockfile(path: Path) -> Lockfile
```

**Purpose**: Reads a lockfile from disk and turns it into a validated Lockfile object. It is used when the system must enforce the exact extension set chosen for a deployment.

**Data flow**: It receives a file path, reads the file text, parses that text as JSON, and validates it against the Lockfile shape. It returns a Lockfile object containing the pinned UFO version and extension pins.

**Call relations**: load_manifests calls this after it has found that a lockfile exists. The parsed result then drives which installed extensions are accepted and which are rejected.

*Call graph*: called by 1 (load_manifests); 1 external calls (read_text).


##### `write_lockfile`  (lines 147–148)

```
def write_lockfile(path: Path, lockfile: Lockfile) -> None
```

**Purpose**: Writes a Lockfile object back to disk as formatted JSON. This supports tools that create or update the pinned extension list.

**Data flow**: It receives a destination path and a Lockfile object. It converts the object to JSON with indentation, adds a final newline, and writes that text to the path. Its output is the changed file on disk.

**Call relations**: This is the matching writer for read_lockfile. Command-line or bundling flows outside this file can use it to produce the file that load_manifests later enforces at boot.

*Call graph*: 2 external calls (model_dump_json, write_text).


##### `discovered`  (lines 151–161)

```
def discovered() -> dict[str, tuple[Manifest, EntryPoint]]
```

**Purpose**: Finds every installed UFO extension that registered itself with Python. It also rejects two extensions that claim the same manifest name, because that would make later choices ambiguous.

**Data flow**: It asks Python for entry points in the ufo.extension group. For each entry point, it loads and calls the extension's zero-argument factory to get a Manifest. It returns a dictionary from manifest name to the manifest and the entry point it came from.

**Call relations**: load_manifests uses this as the raw installed-extension list before applying lockfile rules. migration_locations also uses it to connect active manifests back to their installed package directories.

*Call graph*: called by 2 (load_manifests, migration_locations); 1 external calls (entry_points).


##### `discovered_packs`  (lines 164–175)

```
def discovered_packs() -> dict[str, Pack]
```

**Purpose**: Finds every installed UFO pack. A pack is a bundle that selects a group of extensions and may add pack-level skills or onboarding steps.

**Data flow**: It asks Python for entry points in the ufo.pack group. Each entry point is loaded and called to produce a Pack. The function returns packs by name and refuses duplicate pack names.

**Call relations**: _pack_manifests calls this when configuration selects a pack. The discovered pack tells the loader which extension manifests should remain active together.

*Call graph*: called by 1 (_pack_manifests); 1 external calls (entry_points).


##### `_entry_spec`  (lines 178–183)

```
def _entry_spec(entry: EntryPoint) -> ModuleSpec
```

**Purpose**: Finds import information for the top-level Python package behind an extension entry point. This is needed to locate source files and migration directories.

**Data flow**: It receives an entry point, takes the first part of its module name, and asks Python's import system for that package's module specification. It returns the specification, or raises an error if the package has no importable source.

**Call relations**: extension_digest uses this to find which files should be hashed. migration_locations uses it to find where an extension's migrations directory would live.

*Call graph*: called by 2 (extension_digest, migration_locations).


##### `_package_dir`  (lines 186–191)

```
def _package_dir(spec: ModuleSpec) -> Path
```

**Purpose**: Figures out the directory that should be treated as an extension's package folder. That folder is where the loader looks for an optional migrations subdirectory.

**Data flow**: It receives a module specification. If the extension is a package with a directory, it returns that directory; if it is a single Python file, it returns that file's parent directory.

**Call relations**: migration_locations uses _package_dir after _entry_spec has found the import details for an installed extension.

*Call graph*: called by 1 (migration_locations); 1 external calls (Path).


##### `extension_digest`  (lines 194–214)

```
def extension_digest(entry: EntryPoint) -> str
```

**Purpose**: Computes the source-code fingerprint used to prove a pinned extension has not changed. This protects a locked deployment from silent code drift or tampering.

**Data flow**: It receives an extension entry point, finds the installed package or module, gathers its source files while skipping bytecode cache files, and hashes both file names and file contents in a stable order. It returns a string beginning with sha256:.

**Call relations**: load_manifests calls this for each extension named in the lockfile. If the computed digest differs from the pinned digest, boot stops instead of running unexpected code.

*Call graph*: calls 1 internal fn (_entry_spec); called by 1 (load_manifests); 2 external calls (sha256, Path).


##### `migration_locations`  (lines 217–234)

```
def migration_locations(pack: str | None=None) -> tuple[str, ...]
```

**Purpose**: Collects database migration folders contributed by active extensions. These folders let extensions add or update their own database tables alongside core migrations.

**Data flow**: It discovers installed extensions, loads the active manifest set, then looks beside each active extension package for a migrations directory. It returns the existing migration directories as strings.

**Call relations**: Migration-running code can use this to layer extension migrations onto core migrations. Inside this file, it relies on load_manifests for the active set and on discovery helpers to locate installed packages.

*Call graph*: calls 4 internal fn (_entry_spec, _package_dir, discovered, load_manifests).


##### `load_manifests`  (lines 237–262)

```
def load_manifests(pack: str | None=None) -> tuple[Manifest, ...]
```

**Purpose**: Decides which extension manifests are active for this run. It enforces the lockfile when present, or loads all discovered extensions when no lockfile exists.

**Data flow**: It starts with all discovered installed extensions. If there is no lockfile, it returns all their manifests. If a lockfile exists, it reads the pins, verifies that each pinned extension is installed, checks each digest, and returns only those manifests. If a pack is requested, it narrows the result through _pack_manifests.

**Call relations**: This is the main source of truth for active extensions. Other setup code, including migration_locations and many callers outside this file, build tools, hooks, skills, and backends from the manifests it returns.

*Call graph*: calls 5 internal fn (_pack_manifests, discovered, extension_digest, lockfile_path, read_lockfile); called by 1 (migration_locations).


##### `_pack_manifests`  (lines 265–297)

```
def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]
```

**Purpose**: Turns a named pack into the exact manifest list that pack should activate. It makes pack selection behave like choosing a prearranged kit of compatible extensions.

**Data flow**: It receives the selected pack name and the currently active extension manifests by name. It finds the pack declaration, pulls in each bundled extension from the active set, refuses missing inactive extensions, and appends a synthetic Manifest for the pack's own skills and onboarding steps.

**Call relations**: load_manifests calls this only after it has already applied discovery and lockfile rules. _pack_manifests then narrows the active list to the selected bundle.

*Call graph*: calls 1 internal fn (discovered_packs); called by 1 (load_manifests); 1 external calls (__init__).


##### `connector_clis`  (lines 300–309)

```
def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]
```

**Purpose**: Builds a lookup of connector command-line credentials by provider name. This tells other parts of the system which environment variable sentinel should represent each connector's usable grant.

**Data flow**: It receives active manifests, walks through their connectors, and keeps the connector CLI credential for each connector that declares one. It returns a dictionary keyed by OAuth provider.

**Call relations**: injecting_slots calls this while checking the shared sandbox environment-variable namespace, so connector credential exports do not accidentally conflict with credential-slot exports.

*Call graph*: called by 1 (injecting_slots).


##### `injecting_slots`  (lines 312–385)

```
def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]
```

**Purpose**: Collects credential slots that should be injected into sandboxed tool runs, and checks for dangerous naming conflicts. This prevents one secret or host setting from silently overwriting another.

**Data flow**: It receives active manifests, extracts credential slots with injection rules, then validates sentinels, exported environment variable names, host-choice references, and metering dimensions. If everything is consistent, it returns the injectable slots; otherwise it raises a clear startup error.

**Call relations**: It builds on connector_clis so connector credentials and extension credential slots are checked together. The resulting slots are used by proxy and engine flows that put credential values onto the wire or into a sandbox.

*Call graph*: calls 1 internal fn (connector_clis).


##### `turn_tools`  (lines 388–439)

```
def turn_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, audience: Audience) -> tuple[tuple[ToolDef
```

**Purpose**: Builds the complete tool set available during a conversation turn. It combines core tools, extension tools, connector tools, and object-verb tools, while remembering which extension context belongs to each extension tool.

**Data flow**: It receives active manifests, optional credential, index, and embedding services, and the audience for the turn. It starts with built-in tools, creates extension contexts when needed, adds declared tools, binds declared object kinds, adds core credential object kinds, and returns both the final tool definitions and a map from extension tool name to context.

**Call relations**: Turn setup calls this before dispatching model-requested tools. It hands object kinds to object_registry and ObjectVerbs so object operations appear as tools too, and it uses core_object_kinds to include core credential objects.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, context_for, object_registry).


##### `member_object_registry`  (lines 442–473)

```
def member_object_registry(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> dict[str, BoundKind]
```

**Purpose**: Builds the object registry used for member-facing reads outside a conversation turn. It gives portal-style code the same object kinds as turn tools, but without a specific conversation audience.

**Data flow**: It receives active manifests plus optional credential, index, and embedding services. It binds core object kinds and extension object kinds to the right contexts, adds core credential object kinds, and returns a registry keyed by object kind name.

**Call relations**: This parallels the object part of turn_tools. It calls core_object_kinds and object_registry so member-read paths and turn-time object tools see consistent object definitions.

*Call graph*: calls 1 internal fn (core_object_kinds); 3 external calls (__init__, context_for, object_registry).


##### `core_object_kinds`  (lines 476–490)

```
def core_object_kinds(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None=None) -> tuple[BoundKind, ...]
```

**Purpose**: Creates the object kind definitions owned by UFO core itself. In this file, that means exposing credentials as readable objects based on the active extension credential slots.

**Data flow**: It receives active manifests and an optional credential store. It gathers declared credential slots, builds a credential object store over those slots and live credentials, wraps it in an ObjectKind, and returns it as a bound core kind with no extension context.

**Call relations**: turn_tools, member_object_registry, and validate_ext_tools call this when assembling object registries. It supplies the core credential object kind alongside any extension-defined object kinds.

*Call graph*: called by 3 (member_object_registry, turn_tools, validate_ext_tools); 4 external calls (__init__, __init__, __init__, declared_slots).


##### `skill_registry`  (lines 493–516)

```
def skill_registry(manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...]=()) -> SkillRegistry
```

**Purpose**: Builds the complete registry of loadable skills. Skills are reusable instruction or behavior packages that can come from core, active packs or extensions, and generated runtime data.

**Data flow**: It starts with core skills, then reads skill specs from each manifest's skill paths and adds every discovered skill. It then adds any generated skills. If any skill name is already taken, it raises an error; otherwise it returns a SkillRegistry.

**Call relations**: Startup code can call this after loading manifests so later skill lookup has one unambiguous registry. It delegates disk discovery to discover_skills and packages the result in SkillRegistry.

*Call graph*: 2 external calls (__init__, discover_skills).


##### `turn_subagents`  (lines 519–523)

```
def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]
```

**Purpose**: Collects subagent profiles declared by active extensions. A subagent profile describes a specialized assistant role that can be used during turns.

**Data flow**: It receives active manifests and flattens all their subagent profile declarations in load order. It returns them as a tuple and does not perform extra validation here.

**Call relations**: The serving layer can feed this result into the subagent registry. Duplicate profile names are expected to be rejected by that registry rather than by this collector.


##### `durable_surfaces`  (lines 526–532)

```
def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]
```

**Purpose**: Finds conversation surfaces whose replies are durable, meaning they should be delivered through a later writeback process. A surface becomes durable when it declares a post handler.

**Data flow**: It receives active manifests, walks their surface declarations, selects surfaces with a post handler, and returns their names as a frozen set.

**Call relations**: Admission or conversation setup code can use this set to decide when to create writeback rows. This keeps the rule tied to what active extensions declare.


##### `turn_subagent_grants`  (lines 535–545)

```
def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: Builds the extra tool grants that extensions give to subagent profiles they do not own. This lets one extension widen a profile's tool access without editing the profile itself.

**Data flow**: It receives active manifests, walks each subagent tool grant, groups tool names by target profile, and returns a dictionary from profile name to a frozen set of granted tool names.

**Call relations**: The turn loop can combine this with a profile's own tool list and then intersect it with the live tool registry. Unknown tools or profiles are left to fall away later instead of failing here.


##### `turn_runtime_skills`  (lines 548–568)

```
async def turn_runtime_skills(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Asks active extensions for runtime-generated skills for the current bound agent. These are skills produced dynamically rather than read from static files.

**Data flow**: It receives active manifests and optional credential, index, and embedding services. For each manifest with a runtime skill provider, it creates an extension context and awaits the provider. It returns all produced RuntimeSkill objects in order.

**Call relations**: Turn or startup flows that need agent-specific skills call this after manifests are active. It uses context_for so each extension's provider runs with its own scoped access.

*Call graph*: 1 external calls (context_for).


##### `index_backend`  (lines 574–594)

```
def index_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> IndexBackend
```

**Purpose**: Selects and creates the index backend for the workspace. An index backend is the service that stores and searches indexed text or vectors.

**Data flow**: It receives active manifests, an optional configured backend name, and an optional credential store. It chooses the configured name or default, searches manifest index declarations for that name, checks credential availability if needed, creates the declaring extension's context, and returns the backend from the spec factory. If none match, it raises NotRegisteredError.

**Call relations**: Configuration setup calls this once it knows the active manifests. It hands off to the extension-provided factory, while NotRegisteredError gives callers a precise way to detect an unregistered backend name.

*Call graph*: 2 external calls (__init__, context_for).


##### `embed_backend`  (lines 597–617)

```
def embed_backend(manifests: tuple[Manifest, ...], configured: str | None, credential_store: CredentialStore | None) -> EmbedClient
```

**Purpose**: Selects and creates the embedding client for the deployment. An embedding client turns text into numeric vectors that search and memory systems can compare.

**Data flow**: It receives active manifests, an optional configured name, and an optional credential store. It chooses the configured name or default, finds a matching manifest embed declaration, checks credentials if required, creates an extension context, and returns the client built by the declaration's factory. If no declaration matches, it raises NotRegisteredError.

**Call relations**: Boot configuration uses this to wire the embedding service that later contexts, index code, and memory tools receive. Like index_backend, it delegates the actual construction to the selected extension.

*Call graph*: 2 external calls (__init__, context_for).


##### `memory_search`  (lines 620–645)

```
def memory_search(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, name: str=DEFAULT_MEMORY_SEARCH_PROVIDER)
```

**Purpose**: Builds one named memory-search provider from active extensions. Memory search is the feature that looks up relevant stored memories for the current work.

**Data flow**: It receives active manifests, optional credential, index, and embedding services, plus a provider name. It finds matching provider specs, returns None if none exist, rejects duplicates, checks credentials, creates the declaring extension context, builds the provider, and wraps it in MemorySearch.

**Call relations**: Turn or memory setup code can call this to get the active search provider. It uses context_for so the provider receives the same scoped extension access as tools and jobs.

*Call graph*: 2 external calls (__init__, context_for).


##### `validate_ext_tools`  (lines 648–684)

```
def validate_ext_tools(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None) -> None
```

**Purpose**: Performs early validation of extension tools and object kinds at boot. It catches name collisions and missing credential configuration before the system starts accepting work.

**Data flow**: It receives active manifests and an optional credential store. It collects built-in tools and extension-declared tools, checks whether credential-declaring tool extensions have a credential key, builds an object registry including extension and core object kinds, adds object-verb tools, and constructs a ToolRegistry to force validation.

**Call relations**: Startup code can call this as a safety check before per-workspace turn contexts exist. It reuses core_object_kinds, object_registry, ObjectVerbs, and ToolRegistry to validate the same shape later used by turn_tools.

*Call graph*: calls 1 internal fn (core_object_kinds); 4 external calls (__init__, __init__, __init__, object_registry).


##### `HookChain.__post_init__`  (lines 725–728)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that every hook in a HookChain belongs to the same audience as the chain. This prevents a hook prepared for one conversation audience from running in another.

**Data flow**: After a HookChain is created, it flattens all bound hooks and compares each hook's extension context audience with the chain audience. If any differ, it raises a ValueError; otherwise construction finishes normally.

**Call relations**: turn_hooks creates HookChain instances after binding hooks to contexts. __post_init__ is the final guard that confirms those bindings are internally consistent before hooks can fire.


##### `HookChain.fire`  (lines 730–806)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: Runs all hooks for one event and combines their results into one decision. Hooks can deny an action, edit tool input or output, inject extra context, or simply observe.

**Data flow**: It receives an event, event payload, optional turn and agent records, and an optional speaker member ID. It finds hooks for that event, skips tool-specific hooks that do not match, builds a HookContext for each one, runs it with a timeout, checks that the returned outcome is allowed for the event, and folds outcomes into a HookResolution. Gating events deny on hook failure; non-gating events log failures and continue.

**Call relations**: The turn engine calls fire at specific lifecycle points, such as before tool use or after tool use. It creates HookContext objects for extension handlers, uses HookResolution to report the final folded result, and logs swallowed non-gating failures for operators.

*Call graph*: 6 external calls (__init__, __init__, __init__, timeout, replace, log).


##### `turn_hooks`  (lines 809–840)

```
def turn_hooks(manifests: tuple[Manifest, ...], credential_store: CredentialStore | None, index: IndexBackend | None=None, embed: EmbedClient | None=None, *, audience: Audience) -> HookChain
```

**Purpose**: Builds the HookChain used during a conversation turn. It binds each extension hook to the extension context it should run under and groups hooks by event.

**Data flow**: It receives active manifests, optional credential, index, and embedding services, and the turn audience. It checks that hook-declaring extensions have credentials available, creates one context per such extension, ignores page_change hooks because they are run elsewhere, wraps each remaining hook as a BoundHook, and returns a HookChain.

**Call relations**: Turn setup calls this before the turn lifecycle begins. The returned HookChain is later driven by HookChain.fire whenever the engine reaches a hookable event.

*Call graph*: 3 external calls (__init__, __init__, context_for).


### Extension capability manifests
Installed extension manifests declare scheduled jobs, tools, surfaces, subagents, credentials, routes, object types, source backends, and hooks for the loader to register.

### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup for registration, then scheduled job execution`

This file is like a sign-up form for the self-improvement extension. It gives the extension a name and version, then declares one scheduled job: an evaluation tick that runs once per day on a cron-style schedule. A cron schedule is a clock-based rule, meaning the job runs because time passed, not because a proposal was created or data was written.

When the scheduled job fires, `_tick` is called with an `ExtensionContext`, which is the extension’s access point to shared services such as model access and workspace information. The first important check is whether a model is available. The self-improvement loop depends on a model to suggest prompt changes, replay behavior, and judge candidate results, so it stops with a clear error if no model has been wired in.

If model access exists, the file wraps it in `ModelAccessLeg`, then builds the main pieces of the self-improvement run: `PromptProposer` to suggest changes, `CandidateEvaluation` to test and judge them, and `ImproveCron` to coordinate the full scheduled pass. The `manifest` function then exposes this setup to the host system as a `Manifest`, including which workspaces are eligible candidates for the job.

#### Function details

##### `_tick`  (lines 21–29)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job body for the self-improvement extension. It runs one self-improvement pass, but only if the extension has access to a model, because proposing and judging improvements require model calls.

**Data flow**: It receives an `ExtensionContext`, which contains shared runtime services. It checks `ctx.model`; if no model is present, it raises an error and stops. If a model is present, it wraps that model in `ModelAccessLeg`, gives that wrapper to `PromptProposer` and `CandidateEvaluation`, builds an `ImproveCron`, and awaits its `run` method so the scheduled improvement pass actually happens.

**Call relations**: The scheduler calls `_tick` when the job declared by `manifest` reaches its scheduled time. Inside that moment, `_tick` creates the model-access wrapper with `ModelAccessLeg.__init__`, creates the proposal step with `PromptProposer.__init__`, creates the evaluation step with `CandidateEvaluation.__init__`, then hands all of those parts to `ImproveCron.__init__` so the cron runner can coordinate the full flow.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 32–44)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension declaration that the host system reads to discover the self-improvement extension. It names the extension, gives its version, and registers the daily evaluation job.

**Data flow**: It takes no input. It builds a `JobSpec` with the job name, the cron schedule, the `_tick` handler, and the set of candidate workspaces returned by `trajectory_workspaces()`. It then places that job inside a `Manifest` and returns it to the host system.

**Call relations**: The extension loading system calls `manifest` during setup to learn what this extension provides. `manifest` calls `trajectory_workspaces` to say which workspaces the job can run against, creates a `JobSpec` to describe the scheduled job, and wraps that job in a `Manifest` so the scheduler can later call `_tick` at the right time.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup / extension load`

This file is like the packing list for the sites extension. Without it, the rest of the system would not know that this extension can build websites, serve hosted site pages, or delegate website work to a specialized child agent.

At import time, it names the extension, gives it a version, reads a prompt section from a Markdown file, and points to the folder that contains the website-building skill. The prompt section is text added to the agent’s instructions so the agent knows how to use the site tools and return a hosted link to the user.

The main work happens in `manifest()`. It gathers several pieces defined elsewhere: normal site tools, delegation tools such as `build_website`, a `site` object type for chat, a website-building subagent profile, and a `sites` surface for displaying hosted pages. It then wraps them in a `Manifest`, which is the standard object the larger application reads when loading an extension.

In everyday terms, this file does not build the website itself. It puts all the right tools, instructions, and display hooks on the counter so the system can find and use them when needed.

#### Function details

##### `manifest`  (lines 35–45)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s declaration object. The system uses this declaration to learn which tools, prompts, skills, subagents, surfaces, and object types belong to the sites extension.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the site tools, delegation tools, site object type, subagent profile, surface, prompt text, and skill folder path. It packages those into a `Manifest` object. As part of that packaging, it creates a `PromptSection` for the sites instructions and a `SkillSpec` pointing at the website-building skill directory. The result is one complete manifest object that the extension loader can consume.

**Call relations**: This function is called when the system loads the sites extension and asks it what it provides. Inside, it hands the prompt text to `PromptSection.__init__`, the skill path to `SkillSpec.__init__`, and all declared extension pieces to `Manifest.__init__`, so the larger system can register them together.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension discovery`

This file is like the Slack extension’s registration card. Without it, the core system would not know that Slack exists, which web addresses should receive Slack events, which secrets must be stored, or which setup help to offer users.

The manifest describes two ways Slack can be installed. In the preferred OAuth path, the deployed service uses its own Slack app credentials from environment variables, and the OAuth callback creates a workspace-specific bot token. In the alternative “bring your own app” path, a user creates their own Slack app and privately provides two workspace secrets: the bot token and that app’s signing secret. A signing secret is Slack’s shared proof that a request really came from Slack.

The file also declares one Slack “surface,” meaning one place where the outside world can talk to this system. That surface has routes for incoming Slack events, interactive Slack actions, and the OAuth callback. It also points the system to helper functions that post messages, attach Slack context, identify the workspace, and identify the bot’s own Slack user. Finally, it exposes Slack tools and a setup skill directory so the agent can guide installation in chat.

#### Function details

##### `manifest`  (lines 37–69)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension manifest: the complete description of how Slack connects to the core system. The core uses this to learn what credentials, web routes, tools, and setup skill belong to Slack.

**Data flow**: It starts with constants and imported Slack helper functions, such as route handlers and credential slot names. It packages them into credential slot objects, route objects, a surface object, tool references, and a skill reference. The result is one Manifest object that the rest of the system can read to wire Slack into the application.

**Call relations**: When the extension is discovered, this function is the place that produces its registration data. To build that data, it creates CredentialSlot entries for Slack secrets, SurfaceRoute entries for Slack web endpoints, a SurfaceSpec that ties those routes to Slack behavior, a SkillSpec for the setup instructions, and finally the top-level Manifest that contains everything.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### `extensions/sources/ufo_ext_sources/manifest.py`

`config` · `startup / extension discovery`

This file is like the extension’s front desk. When the UFO system loads extensions, it asks each one, “What do you offer?” The answer from this file is a manifest: a structured list of the extension’s abilities.

The sources extension supports multiple external content providers, called connectors. A connector is a small adapter that knows how to talk to one outside service. For each registered connector, this file creates a matching source backend, which is the part the sync system can call to fetch content. It also declares a credential slot for each connector, so users can provide their own API key. That pattern is often called BYOK, meaning “bring your own key.”

The file also registers two object kinds: one for sources themselves and one for pages synced from those sources. It adds a page-change hook, which is a callback that runs when synced page content changes, so subscribers can be notified. Finally, it defines a built-in “direct” authentication proxy. An auth proxy is a small layer that supplies credentials to connectors without each connector needing to know where secrets are stored.

Without this file, the extension’s pieces would exist in code but would not be announced to the host system. The sync runner would not know which providers are available, where to read keys from, or what to do when pages change.

#### Function details

##### `ConnectorSourceFactory.__call__`  (lines 34–35)

```
def __call__(self, _credentials: CredentialAccess) -> ConnectorBackend
```

**Purpose**: This turns a connector class into a ready-to-use source backend. The system can call the factory whenever it needs a backend for a specific provider.

**Data flow**: It receives a credential access object, although this factory does not use it directly. It creates a fresh connector instance from the connector class stored on the factory, wraps that connector in a ConnectorBackend, and returns the backend. The result is a sync-ready object that knows how to fetch data through that connector.

**Call relations**: The manifest gives one of these factories to each SourceProvider it registers. Later, when the source system needs to build a backend for a registered connector, it calls this factory, which hands back a ConnectorBackend for the sync runner to use.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 38–64)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension manifest, which is the official list of features this extension contributes to the UFO system. It is the main declaration point for source providers, credentials, objects, hooks, and authentication support.

**Data flow**: It starts from constants such as the extension name, version, and direct backend name, and from the connector registry. For every registered connector, it creates a source provider and a matching credential slot. It also includes the source and page object definitions, the page-change hook, and a direct auth proxy builder. It returns one Manifest object containing all of that information.

**Call relations**: The host system calls this during extension loading. Inside it, the connector registry is read to discover available providers, each provider is paired with a ConnectorSourceFactory, and the direct auth proxy is made available so keyed accounts can sync even when no external broker backend is installed.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, items).

## 📊 State Registers Touched

- `reg-effective-configuration` — The final startup settings that decide how the service, security, sandbox, proxy, and enabled packs should behave.
- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-model-provider-catalog` — The shared list of AI models and providers, including how to call them, what keys they need, and what features they support.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-source-page-sync-state` — The source records, page bodies, cursors, revisions, deletion markers, and replay feed used to keep external content synchronized.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-scheduled-task-state` — The durable timers and recurring jobs that remember what should run later, whether it is paused, expired, claimed, or rescheduled.
- `reg-hosted-site-state` — The durable records for generated hosted sites, including names, ports, owners, conversations, sharing, and viewing permissions.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-background-job-state` — The durable and in-memory background job registry, candidate queue, claims, retries, and worker progress for non-turn jobs such as sync, billing, evaluation, and cleanup.
- `reg-extension-kv-store` — The generic per-workspace extension JSON store used by add-ons to persist small feature-specific state outside core tables.
- `reg-search-provider-registry` — The live registry of web-search, page-fetch, embedding/search provider backends and their capabilities used by research, recall, and indexing code.
- `reg-source-connector-registry` — The registered source backend implementations, credential requirements, sync hooks, and capability metadata used to instantiate external content synchronization.
- `reg-redis-coordination-state` — The live Redis coordination backend state, including clients, stream/consumer metadata, and cross-process fan-out or coordination wiring.
- `reg-extension-note-state` — Durable note records stored by note/sample extensions and reused by extension tools across startup and normal workspace operation.
