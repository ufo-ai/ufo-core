# Per-turn host environment assembly  `stage-7`

This stage runs just before each model turn. Its job is to set the table: decide exactly what the model may read, what tools it may use, which helper agents it may start, and what files are placed in its working sandbox. The host package marks this as the system’s “outside world” layer, where prompts, tools, skills, extensions, and files are gathered.

The main builder, `assemble.py`, acts like a careful dispatcher. It combines the system prompt, selected skills, seeded files, model choice, extension context, and tool access into one complete turn environment. It also enforces safety rules: environment documents may narrow or reshape what was already allowed, but cannot quietly grant extra power.

Two supporting areas feed this builder. Prompt and skill resolution prepares the instructions, reusable skill cards, model catalog, and reproducible environment documents. Spawn-target preparation builds the menu of allowed child agents or pipeline steps. Together, these parts make each turn predictable, traceable, and limited to the powers chosen for that moment.

## Sub-stages

- [Prompt, skill, and environment-document resolution](stage-7.1.md) `stage-7.1` — 8 files
- [Spawn-target and subagent availability preparation](stage-7.2.md) `stage-7.2` — 6 files

## Files in this stage

### Host Environment Assembly
Defines the host environment layer and assembles the complete per-turn prompt, tool, skill, file, and permission context for the model.

### `core/src/ufo/host/__init__.py`

`other` · `cross-cutting`

This is a package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it contains only a short documentation string, but that sentence is useful: it defines the meaning of the `ufo.host` area of the codebase.

The host layer is described as “what an agent turn sees.” In plain terms, when an agent takes a step, it needs access to its working environment: available tools, optional extensions, reusable skills, and prompt text that guides behavior. This package is the named home for that environment-facing code.

There is no executable logic in this file, so nothing is calculated, loaded, or changed here. Its job is more like a label on a toolbox drawer: it does not operate the tools itself, but it tells future readers and importers what kind of tools belong inside. Without this file, depending on the Python setup, imports from this folder could be less clear or less reliable, and the package would lack this small but helpful piece of documentation.


### `core/src/ufo/host/assemble.py`

`orchestration` · `per-turn environment assembly`

Think of this file as packing a carefully checked toolbox and instruction folder before an AI worker starts a job. The host has discovered extensions, credentials, workspace facts, object actions, skills, subagents, and optional environment documents. `HostEnvironment` combines those pieces into an `AssembledTurn`, which is what the runtime gives to the model for one turn.

The key rule is that a turn-specific environment document can reduce or reshape access, but not expand the platform's grants. It may rewrite prompt text, hide tools, change tool descriptions, add sandbox command tools, edit deploy-provided skills, or seed files. But if it refers to a scoped tool that was not already offered, the turn fails loudly instead of guessing. This matters because tool descriptions and prompts influence what the model believes it can do, while tool definitions carry the real authorization and execution context.

The file also treats normal agents and subagents differently. Main agents get workspace facts and object-kind guidance; subagents get their profile prompt, preload skills, and their own grant set. Spawned turns get extra finish instructions when needed. The helper functions below do the small but important checks: exact text edits, safe schema description changes, spawn payload hints, and conversion of a document-defined `run` command into a real callable tool.

#### Function details

##### `HostEnvironment.assemble`  (lines 120–226)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Builds everything the model is allowed to see and call for a single turn. It combines discovered extension contributions, the agent or subagent profile, workspace facts, skills, spawn targets, hooks, and optional environment-document overrides into one `AssembledTurn`.

**Data flow**: It receives an `AssembleRequest` containing the turn, agent, profile, audience, requested skills, subagent information, and optional environment document name. It reads environment documents and seeded files from the blob store, asks extension loaders for tools, hooks, workspace facts, and member skills, chooses the allowed tools and actions, renders the prompt, applies document edits, adjusts the spawn tool, and returns an `AssembledTurn` containing the final prompt, tool registry, hooks, skills, files, cards, and visibility information.

**Call relations**: This is the central story for the file. It calls `HostEnvironment.tools`, `HostEnvironment.hooks`, and `HostEnvironment.member_skills` to collect platform contributions; uses `_object_kind_index`, `_skills_with_document`, `_with_spawn_payload`, and `_applied_document` to shape what survives; and calls `_document_blob` whenever it must load a pinned environment document or its files.

*Call graph*: calls 8 internal fn (_document_blob, hooks, member_skills, tools, _applied_document, _object_kind_index, _skills_with_document, _with_spawn_payload); 22 external calls (__init__, __init__, __init__, span, load_environment_document, load_environment_file, turn_workspace_facts, spawn_catalog_skill, spawn_targets, render_object_kinds (+12 more)).


##### `HostEnvironment.tools`  (lines 228–242)

```
def tools(self, *, audience: Audience, member_context_authority: ExecutionAuthority) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext], ObjectVerbs]
```

**Purpose**: Collects the tools available for a turn from installed manifests and runtime services. A tool here means a callable action the model may ask the system to run, with its authorization context already attached.

**Data flow**: It receives the audience and the execution authority to use for member-context tools. It passes manifests, credentials, indexing services, embedding services, URL settings, artifact settings, and blob access into the extension loader. It returns the raw tool definitions, per-tool extension context, and registered object verbs.

**Call relations**: `HostEnvironment.assemble` calls this early so it can later filter the full tool offer down to what the agent or subagent is actually granted. This function delegates the discovery work to `turn_tools` rather than building tools itself.

*Call graph*: called by 1 (assemble); 1 external calls (turn_tools).


##### `HostEnvironment.hooks`  (lines 244–253)

```
def hooks(self, *, audience: Audience) -> HookChain
```

**Purpose**: Collects turn hooks from extensions. Hooks are callbacks that can run at defined moments around a turn, like a checklist that extensions can add to.

**Data flow**: It receives the audience for the turn and reads host-level services such as credentials, index, embedding client, tailer, and public URL. It passes them to the extension loader and gets back a `HookChain`, which is an ordered set of callbacks.

**Call relations**: `HostEnvironment.assemble` calls this while preparing the turn and stores the returned hook chain in the assembled result. The actual hook collection is delegated to `turn_hooks`.

*Call graph*: called by 1 (assemble); 1 external calls (turn_hooks).


##### `HostEnvironment.member_skills`  (lines 255–264)

```
async def member_skills(self, *, agent_name: str) -> tuple[tuple[SkillCard, ...], SkillMaterializer]
```

**Purpose**: Loads skills authored or made visible for a specific agent member. These are extra pieces of reusable know-how that can be shown or materialized for the model.

**Data flow**: It receives an agent name, reads the host's manifests and runtime services, and asks the extension loader for member skill cards and a materializer. It returns the visible cards plus a function-like materializer that can later load the actual skill content.

**Call relations**: `HostEnvironment.assemble` calls this only when the agent is allowed to use workspace skills. The returned cards and materializer are folded into the broader skill registry for the turn.

*Call graph*: called by 1 (assemble); 1 external calls (turn_member_skills).


##### `HostEnvironment.environment_model`  (lines 266–270)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Looks up whether an environment document requests a specific model for the main agent or a named subagent profile. It returns only the model choice, not the rest of the environment.

**Data flow**: It receives the environment document identifier and an optional profile name. It loads the document from the blob store, selects either the main block or the named profile block, and returns that block's model field, or `None` if no such model is named.

**Call relations**: This is a focused lookup used outside the full assembly path when the runtime needs to resolve model choice. It uses `_document_blob` for storage access and `load_environment_document` for parsing.

*Call graph*: calls 1 internal fn (_document_blob); 1 external calls (load_environment_document).


##### `HostEnvironment.clis`  (lines 272–273)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Returns command-line connector credentials advertised by manifests. These are credentials meant to be used by connector command-line tools.

**Data flow**: It reads the host's manifests and passes them to the connector loader. The result is a dictionary of command-line credential descriptions keyed by name.

**Call relations**: This is a simple access point for other host setup code. It does not participate in `assemble`; it delegates directly to `connector_clis`.

*Call graph*: 1 external calls (connector_clis).


##### `HostEnvironment.slots`  (lines 275–276)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Returns credential slots declared by extensions. A credential slot is a named place where a secret or token can be injected when an extension runs.

**Data flow**: It reads the host's manifests, asks the extension loader for injectable slots, and returns them as a tuple.

**Call relations**: This gives surrounding host code a way to discover what credentials extensions need. It delegates to `injecting_slots` and is separate from per-turn assembly.

*Call graph*: 1 external calls (injecting_slots).


##### `HostEnvironment._document_blob`  (lines 278–281)

```
def _document_blob(self) -> WorkspaceBlobStore
```

**Purpose**: Provides the blob store used to load environment documents and files, and fails clearly if this host was not given one. A blob store is storage for named pieces of content, like documents or file bodies.

**Data flow**: It reads `self.blob`. If a blob store exists, it returns it. If not, it raises a runtime error explaining that environment documents cannot be loaded.

**Call relations**: `HostEnvironment.assemble` and `HostEnvironment.environment_model` call this before loading environment documents. It is the guard that prevents later code from failing in a vague way.

*Call graph*: called by 2 (assemble, environment_model).


##### `_object_kind_index`  (lines 284–301)

```
def _object_kind_index(verbs: ObjectVerbs, granted_actions: frozenset[str]) -> tuple[tuple[str, str, tuple[str, ...]], ...]
```

**Purpose**: Builds the prompt-friendly list of workspace object kinds and the actions this turn is allowed to use on them. This helps the model understand not only what kinds of objects exist, but which actions are actually granted.

**Data flow**: It receives all registered object verbs and the set of action IDs granted to this turn. It walks through object kinds in name order, keeps only actions whose canonical ID is granted, formats each action with whether it applies to an instance or a collection, and returns a tuple ready for prompt rendering.

**Call relations**: `HostEnvironment.assemble` calls this for main-agent turns after it knows the granted actions. The result is passed into prompt rendering so the model sees object guidance that matches its real permissions.

*Call graph*: called by 1 (assemble).


##### `_skills_with_document`  (lines 304–333)

```
def _skills_with_document(skills: SkillRegistry, document: EnvironmentDocument | None) -> SkillRegistry
```

**Purpose**: Applies skill changes from an environment document to the deploy-provided skill registry. It can replace a skill's `SKILL.md`, edit it by exact text replacement, or add a new document-provided skill, while refusing to override member-authored skills.

**Data flow**: It receives the current `SkillRegistry` and an optional environment document. If there are no document skill changes, it returns the original registry. Otherwise it copies skills by name, applies full replacement text or exact edits, parses the resulting skill content, removes changed skills from the bundled-image set, and returns a new registry.

**Call relations**: `HostEnvironment.assemble` calls this before rendering prompts for both main agents and subagents. It uses `_edited` for safe text replacement, `parse_skill_content` to turn files into a skill object, and `replace` to produce an updated immutable-style registry.

*Call graph*: calls 1 internal fn (_edited); called by 1 (assemble); 2 external calls (replace, parse_skill_content).


##### `_applied_document`  (lines 336–392)

```
def _applied_document(prompt: RenderedPrompt, tools: ToolRegistry, scoped: EnvironmentOverrides | None, global_tools: dict[str, ToolOverride]) -> tuple[RenderedPrompt, ToolRegistry, bool]
```

**Purpose**: Applies prompt and tool overrides from an environment document to one assembled target, such as the main agent or a subagent profile. It is the main enforcement point for the rule that scoped overrides cannot name tools the turn was not offered.

**Data flow**: It receives the rendered prompt, current tool registry, scoped overrides for the target, and top-level tool overrides. It builds a tool map by name, rejects unknown scoped tool names unless they define a new sandbox `run` command, merges global and scoped overrides, removes disabled tools, rewrites descriptions or parameter descriptions, creates document-defined run tools, applies prompt replacement or edits, and returns the new prompt, new tool registry, and a flag saying whether the prompt was fully replaced.

**Call relations**: `HostEnvironment.assemble` calls this after the normal prompt and tool offer are built. This function hands prompt changes to `_applied_prompt`, parameter-description changes to `_described_model`, and command-tool creation to `_run_tool`.

*Call graph*: calls 3 internal fn (_applied_prompt, _described_model, _run_tool); called by 1 (assemble); 2 external calls (__init__, replace).


##### `_applied_prompt`  (lines 395–398)

```
def _applied_prompt(content: str, override: PromptOverride) -> RenderedPrompt
```

**Purpose**: Turns a prompt override into a new rendered prompt. The override can either replace the entire prompt or edit selected text inside the existing prompt.

**Data flow**: It receives the existing prompt text and a `PromptOverride`. If the override has full replacement text, it wraps that text as a rendered prompt. Otherwise it applies exact text edits with `_edited` and wraps the edited result.

**Call relations**: `_applied_document` calls this when the selected environment block contains prompt changes. It uses `_edited` for the careful "match exactly once" behavior and `rendered_prompt` to return the standard prompt object.

*Call graph*: calls 1 internal fn (_edited); called by 1 (_applied_document); 1 external calls (rendered_prompt).


##### `_edited`  (lines 401–409)

```
def _edited(content: str, edits: tuple[TextEdit, ...], subject: str) -> str
```

**Purpose**: Applies safe text replacements where each old text must appear exactly once. This avoids quiet, accidental changes when a phrase is missing or appears multiple times.

**Data flow**: It receives source text, a tuple of text edits, and a human-readable subject name for error messages. For each edit it counts occurrences of the old text; if the count is not one, it raises a clear error. Otherwise it replaces the old text with the new text and returns the final content.

**Call relations**: `_applied_prompt` uses this for prompt edits, and `_skills_with_document` uses it for skill-file edits. It is the common safety rule behind document-driven text changes.

*Call graph*: called by 2 (_applied_prompt, _skills_with_document).


##### `_with_spawn_payload`  (lines 412–429)

```
def _with_spawn_payload(selected: tuple[ToolDef, ...], targets: tuple[SpawnTarget, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: Adds turn-specific spawn payload guidance to the `spawn` tool's input description. Spawn targets depend on current workspace state, so this information is placed into the description instead of a fixed static schema.

**Data flow**: It receives the selected tool definitions and the available spawn targets. It walks through the tools, and when it finds the spawn tool, it replaces that tool's input model with one whose `payload` field description explains the current target payload keys. Other tools pass through unchanged.

**Call relations**: `HostEnvironment.assemble` calls this just before wrapping selected tools in a `ToolRegistry`. It uses `_described_model` to alter the field description and `spawn_payload_description` to generate the human-readable payload guidance.

*Call graph*: calls 1 internal fn (_described_model); called by 1 (assemble); 2 external calls (replace, spawn_payload_description).


##### `_described_model`  (lines 432–445)

```
def _described_model(model: type[BaseModel], tool: str, parameters: dict[str, str]) -> type[BaseModel]
```

**Purpose**: Creates a new version of a tool input model with updated field descriptions. It changes what the model reads about parameters, not the actual parameter names or types.

**Data flow**: It receives a Pydantic model class, the tool name, and a mapping from parameter names to new descriptions. It checks that every named parameter really exists, copies each field definition, updates the description, and returns a newly created model class based on the original.

**Call relations**: `_applied_document` uses this when environment overrides change tool parameter descriptions. `_with_spawn_payload` uses it to describe the spawn payload field. It raises a clear error when an override names a parameter the tool does not take.

*Call graph*: called by 2 (_applied_document, _with_spawn_payload); 2 external calls (deepcopy, create_model).


##### `_run_tool`  (lines 448–483)

```
def _run_tool(name: str, description: str, inputs: dict[str, ToolInput], run: str) -> ToolDef
```

**Purpose**: Creates a new tool definition backed by a shell command declared in an environment document. This is the one kind of tool an environment document can add, and it runs inside the turn's sandbox rather than granting outside authority.

**Data flow**: It receives the tool name, description, declared inputs, and command string. It builds a Pydantic input model from the declared input types and descriptions, defines an async handler that will run the command, and returns a side-effecting `ToolDef` using that handler.

**Call relations**: `_applied_document` calls this when a tool override includes a `run` command. The nested `_run_tool.handler` later performs the actual command execution when the model calls the generated tool.

*Call graph*: called by 1 (_applied_document); 3 external calls (__init__, Field, create_model).


##### `_run_tool.handler`  (lines 460–475)

```
async def handler(ctx: ToolContext, payload: BaseModel) -> ToolResult
```

**Purpose**: Runs the sandbox command for a document-defined `run` tool and converts the process result into tool output the model can read. It reports timeouts and non-zero exit codes as errors.

**Data flow**: It receives the tool context and the validated input payload from the model. It turns the payload into environment variables and a shell command using `_run_command`, starts the task with `run_task`, combines standard output and standard error, and returns a `ToolResult` containing either normal text or an error message.

**Call relations**: This handler is created inside `_run_tool` and is called by the tool runtime when the generated tool is invoked. It delegates command-line construction to `_run_command` and execution to `run_task`.

*Call graph*: calls 1 internal fn (_run_command); 3 external calls (__init__, __init__, run_task).


##### `_run_command`  (lines 486–494)

```
def _run_command(run: str, payload: BaseModel) -> str
```

**Purpose**: Builds the shell command string used by a document-defined `run` tool. It passes model-provided inputs as `INPUT_NAME` environment variables and quotes values so they are treated as data, not shell syntax.

**Data flow**: It receives the command text from the environment document and the validated payload model. It dumps the payload to simple JSON-style values, skips fields set to `None`, converts booleans with JSON spelling, shell-quotes each value and the command itself, and returns a command like `env INPUT_X=value sh -c 'command'` or just `sh -c 'command'` when there are no inputs.

**Call relations**: `_run_tool.handler` calls this immediately before starting the task. It is the small safety-focused bridge between structured tool inputs and the shell command run inside the sandbox.

*Call graph*: called by 1 (handler); 3 external calls (dumps, model_dump, quote).

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-feature-flags` — The shared on/off switches and rollout choices that let operators change behavior without redeploying.
- `reg-pack-composition` — The selected bundle of built-in extensions, prompts, skills, jobs, and setup steps for this deployment.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-model-catalog` — The shared list of available AI models, their abilities, providers, prices, and credential needs.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-egress-policy` — The network access rules that decide which outside hosts sandboxed or connector code may contact.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-turn-runtime-config` — The per-turn saved runtime settings that must survive retries and keep a turn using the same execution choices.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-subagent-state` — The parent-child turn links, delegation contracts, spawn identities, and pending result deliveries for helper agents.
- `reg-agent-provisioning` — The saved provenance, setup needs, policies, and ownership for agents that are shipped by extensions or created in workspaces.
- `reg-skill-library-cache` — Cached skill-package metadata, community skill listings, fetched descriptions, and probe results used when resolving skills for agents.
