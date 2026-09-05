# Per-turn host environment assembly  `stage-5`

This stage happens just before each agent turn. Its job is to assemble the “world” the model is allowed to see and use for that one step. It is like setting up a desk before someone starts work: the instructions are placed on the desk, the right tools are laid out, and any needed reference papers are opened.

The prompt, skill, and environment document resolution part gathers the written guidance. It loads prompt fragments, fills in templates, applies environment documents, chooses relevant skills, records fingerprints for repeatability, and lists the models available in this deployment.

The tool and spawn menu construction part builds the action menu. It decides which tools the model may call and which child agents or helper tasks it may start, while blocking confusing or unsafe names.

The main assembly file, `assemble.py`, ties these pieces together. It produces the complete per-turn package: prompt, tools, selected model, spawn options, skills, seeded files, and routing information, while making sure environment documents can restrict or shape the setup but cannot grant extra power.

## Sub-stages

- [Prompt, skill, and environment document resolution](stage-5.1.md) `stage-5.1` — 6 files
- [Tool and spawn menu construction](stage-5.2.md) `stage-5.2` — 2 files

## Files in this stage

### Per-turn host environment assembly
### `core/src/ufo/host/assemble.py`

`orchestration` · `turn assembly`

Think of this file as packing a carefully checked backpack before an agent starts one turn. The backpack contains instructions, available tools, skill notes, workspace facts, and sometimes starter files. The key safety rule is that a turn-specific environment document may remove or rewrite what the agent sees, but it cannot secretly grant new platform powers. If it names a scoped tool the turn was not allowed to have, the turn fails loudly instead of continuing with a confusing or unsafe setup.

The main class, HostEnvironment, is given the deployed extension manifests, credentials, indexes, blob storage, and other runtime services. For each turn, it asks extension loaders for tools, hooks, skills, workspace facts, and command-line credentials. It then combines those with the agent profile, subagent profile if any, requested skills, spawn targets, and optional environment document.

The file also contains helper functions that apply document edits to prompts and skills, update tool descriptions and parameter descriptions, add safe per-turn command tools, and tailor the spawn tool so it describes the specific subagents available right now. The result is an AssembledTurn: a single bundle the runtime can hand to the model. Without this file, the runtime would not have one clear place to decide what the model can read, what it can call, and how turn-specific overrides are safely applied.

#### Function details

##### `HostEnvironment.assemble`  (lines 128–234)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Builds the full environment for a single agent or subagent turn. It decides the prompt, allowed tools, hooks, skills, preload material, seeded files, and visibility of member-written skills.

**Data flow**: It starts with an AssembleRequest containing the turn, agent, optional subagent profile, requested skills, audience, grants, and optional environment document. It loads the document if present, gathers tools and hooks from manifests, adds workspace and spawn information, chooses agent or subagent prompts and tool permissions, applies any document overrides, loads seeded files, and finally returns one AssembledTurn containing everything the runtime needs for that turn.

**Call relations**: This is the central story for the file. It calls HostEnvironment.tools, hooks, and member_skills to gather deploy-provided pieces, then uses helpers such as _skills_with_document, _object_kind_index, _with_spawn_payload, and _applied_document to shape those pieces before handing the finished bundle back to the runtime.

*Call graph*: calls 8 internal fn (_document_blob, hooks, member_skills, tools, _applied_document, _object_kind_index, _skills_with_document, _with_spawn_payload); 22 external calls (__init__, __init__, __init__, span, load_environment_document, load_environment_file, turn_workspace_facts, spawn_catalog_skill, spawn_targets, render_object_kinds (+12 more)).


##### `HostEnvironment.tools`  (lines 236–253)

```
def tools(self, *, audience: Audience, member_context_authority: ExecutionAuthority) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext], ObjectVerbs]
```

**Purpose**: Collects the tools that extensions make available for this turn. It also prepares the context those tools need, such as credentials, search/index services, blob storage, URLs, and the turn invoker used by tools that need to admit another turn.

**Data flow**: It receives the audience and the authority to use for member-context actions. It reads the HostEnvironment fields, resolves a workspace-bound invoker when one exists, and passes everything to the extension loader. It returns tool definitions, per-tool extension contexts, and object action information.

**Call relations**: HostEnvironment.assemble calls this early, before it narrows the tool list for the specific agent or subagent. The actual discovery work is delegated to turn_tools, while this method supplies the host-side services and current workspace binding.

*Call graph*: called by 1 (assemble); 2 external calls (turn_tools, ws_current).


##### `HostEnvironment.hooks`  (lines 255–264)

```
def hooks(self, *, audience: Audience) -> HookChain
```

**Purpose**: Collects extension hooks for this turn. A hook is extension code that can run at defined points around a turn, like a plug-in callback.

**Data flow**: It receives the audience, reads credentials and optional services from the HostEnvironment, and asks the extension loader to build a HookChain. The returned chain is included in the assembled turn.

**Call relations**: HostEnvironment.assemble calls this while preparing the turn bundle. The method hands off to turn_hooks, which does the manifest-level discovery and construction.

*Call graph*: called by 1 (assemble); 1 external calls (turn_hooks).


##### `HostEnvironment.member_skills`  (lines 266–275)

```
async def member_skills(self, *, agent_name: str) -> tuple[tuple[SkillCard, ...], SkillMaterializer]
```

**Purpose**: Loads skills written or contributed for a particular agent member. These are optional knowledge or instruction packages that can appear in the model's skill list.

**Data flow**: It takes an agent name, combines it with manifests, credentials, and optional index/embed services, and asks the extension loader for matching member skill cards and a materializer. The cards describe the skills; the materializer can later load their actual content.

**Call relations**: HostEnvironment.assemble calls this only when the agent is allowed to use workspace skills. The returned materializer is folded into the larger SkillRegistry used for the turn.

*Call graph*: called by 1 (assemble); 1 external calls (turn_member_skills).


##### `HostEnvironment.environment_model`  (lines 277–281)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Looks up whether an environment document requests a specific model for the main agent or a named subagent profile. This lets another part of the runtime decide which model to use before the full turn is assembled.

**Data flow**: It receives an environment document identifier and an optional profile name. It loads the document from the blob store, selects the main block or profile-specific block, and returns that block's model name, or null if none is set.

**Call relations**: This method uses _document_blob to get storage and load_environment_document to read the document. It is separate from assemble because model choice may be needed before building the full prompt and tool offer.

*Call graph*: calls 1 internal fn (_document_blob); 1 external calls (load_environment_document).


##### `HostEnvironment.clis`  (lines 283–284)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Lists command-line connector credentials declared by the loaded manifests. These are credentials intended for command-line tools rather than direct in-process calls.

**Data flow**: It reads the HostEnvironment manifests and passes them to connector_clis. The result is a mapping from connector names to CLI credential descriptions.

**Call relations**: This is a small lookup method for callers that need CLI credential information. It delegates the manifest inspection to connector_clis.

*Call graph*: 1 external calls (connector_clis).


##### `HostEnvironment.slots`  (lines 286–287)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Lists credential slots that extensions want injected. A credential slot is a named place where a secret or access token can be supplied.

**Data flow**: It reads the manifests stored on the HostEnvironment and asks injecting_slots to extract all declared slots. It returns those slot declarations as a tuple.

**Call relations**: This method supports setup or credential wiring outside the main turn assembly path. The actual extraction is delegated to injecting_slots.

*Call graph*: 1 external calls (injecting_slots).


##### `HostEnvironment._document_blob`  (lines 289–292)

```
def _document_blob(self) -> WorkspaceBlobStore
```

**Purpose**: Returns the blob store used to load environment documents and files. It fails clearly if this HostEnvironment was created without blob storage.

**Data flow**: It reads the HostEnvironment blob field. If a blob store exists, it returns it; if not, it raises a RuntimeError explaining that environment documents cannot be loaded.

**Call relations**: HostEnvironment.assemble and HostEnvironment.environment_model call this before loading documents or seeded files. It keeps the missing-storage failure in one clear place.

*Call graph*: called by 2 (assemble, environment_model).


##### `_object_kind_index`  (lines 295–312)

```
def _object_kind_index(verbs: ObjectVerbs, granted_actions: frozenset[str]) -> tuple[tuple[str, str, tuple[str, ...]], ...]
```

**Purpose**: Builds the prompt-friendly list of object kinds and the actions this turn is allowed to perform on them. This helps the model understand what kinds of workspace objects exist and which verbs are currently permitted.

**Data flow**: It receives all registered object verbs and the set of granted action IDs. It walks through object kinds in name order, keeps only actions whose canonical IDs are granted, and returns each kind with its description and allowed action labels.

**Call relations**: HostEnvironment.assemble calls this for main-agent turns when it is preparing extra prompt sections. The returned index is rendered into text by the prompt rendering layer.

*Call graph*: called by 1 (assemble).


##### `_skills_with_document`  (lines 315–344)

```
def _skills_with_document(skills: SkillRegistry, document: EnvironmentDocument | None) -> SkillRegistry
```

**Purpose**: Applies environment-document skill changes to the current skill registry. It can replace a deploy skill, edit its SKILL.md text, or add a new document-provided skill, while refusing to overwrite member-authored skills.

**Data flow**: It receives a SkillRegistry and an optional EnvironmentDocument. If there is no document skill section, it returns the registry unchanged. Otherwise it copies the deploy skills by name, applies full replacements or exact text edits, parses the updated skill content, removes changed names from the bundled-skill set, and returns an updated registry.

**Call relations**: HostEnvironment.assemble calls this before rendering prompts for both main agents and subagents. It uses _edited for safe exact text replacement, parse_skill_content to turn files back into a skill object, and dataclasses.replace to produce a modified registry.

*Call graph*: calls 1 internal fn (_edited); called by 1 (assemble); 2 external calls (replace, parse_skill_content).


##### `_applied_document`  (lines 347–403)

```
def _applied_document(prompt: RenderedPrompt, tools: ToolRegistry, scoped: EnvironmentOverrides | None, global_tools: dict[str, ToolOverride]) -> tuple[RenderedPrompt, ToolRegistry, bool]
```

**Purpose**: Applies the prompt and tool overrides from an environment document to one assembled turn target. It can remove tools, rewrite tool descriptions, rewrite parameter descriptions, add per-turn command tools, and change the prompt.

**Data flow**: It receives the current rendered prompt, current ToolRegistry, target-specific overrides, and top-level tool overrides. It indexes tools by name, rejects scoped overrides for tools the turn does not offer, merges global and scoped tool changes, applies each change, applies prompt replacement or edits if present, and returns the new prompt, new registry, and a flag saying whether the prompt was fully replaced.

**Call relations**: HostEnvironment.assemble calls this after the ordinary platform offer has already been narrowed. This helper then reshapes that offer without widening authority. It calls _run_tool when a document adds a command-backed tool, _described_model when parameter descriptions change, and _applied_prompt when prompt text changes.

*Call graph*: calls 3 internal fn (_applied_prompt, _described_model, _run_tool); called by 1 (assemble); 2 external calls (__init__, replace).


##### `_applied_prompt`  (lines 406–409)

```
def _applied_prompt(content: str, override: PromptOverride) -> RenderedPrompt
```

**Purpose**: Turns a prompt override into a new rendered prompt. It either replaces the prompt completely or applies exact text edits to the existing prompt.

**Data flow**: It receives the current prompt text and a PromptOverride. If the override contains full replacement text, that text becomes the prompt. Otherwise it calls _edited to apply replacement edits, then wraps the result as a RenderedPrompt.

**Call relations**: _applied_document calls this when a scoped environment block includes prompt changes. It uses rendered_prompt to convert plain text back into the prompt object the runtime expects.

*Call graph*: calls 1 internal fn (_edited); called by 1 (_applied_document); 1 external calls (rendered_prompt).


##### `_edited`  (lines 412–420)

```
def _edited(content: str, edits: tuple[TextEdit, ...], subject: str) -> str
```

**Purpose**: Safely applies exact text replacements. Each requested old text must appear exactly once, which prevents accidental broad edits or silently missed edits.

**Data flow**: It receives the original content, a list of text edits, and a subject label used in error messages. For each edit, it counts occurrences of the old text; if the count is not exactly one, it raises ValueError. Otherwise it replaces that one occurrence and returns the final text.

**Call relations**: _applied_prompt uses this for prompt edits, and _skills_with_document uses it for skill edits. It is the small safety latch that makes document text rewrites predictable.

*Call graph*: called by 2 (_applied_prompt, _skills_with_document).


##### `_with_spawn_payload`  (lines 423–440)

```
def _with_spawn_payload(selected: tuple[ToolDef, ...], targets: tuple[SpawnTarget, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: Updates the spawn tool so its payload field describes the subagent targets available in this specific turn. This gives the model current guidance about what payload keys it can send when spawning another agent.

**Data flow**: It receives the selected tools and the current spawn targets. It walks through the tools, and when it finds the spawn tool, it creates a copy whose input model has an updated payload description. Other tools pass through unchanged.

**Call relations**: HostEnvironment.assemble calls this just before creating the final ToolRegistry. It uses spawn_payload_description to describe the current targets and _described_model to patch that description into the tool's input schema.

*Call graph*: calls 1 internal fn (_described_model); called by 1 (assemble); 2 external calls (replace, spawn_payload_description).


##### `_described_model`  (lines 443–456)

```
def _described_model(model: type[BaseModel], tool: str, parameters: dict[str, str]) -> type[BaseModel]
```

**Purpose**: Creates a copy of a tool input model with updated field descriptions. This changes what the model reads about parameters without changing the actual allowed fields or their types.

**Data flow**: It receives a Pydantic model, the tool name, and a mapping of parameter names to new descriptions. It checks that every named parameter really exists, deep-copies the field definitions that are being changed, replaces their descriptions, and returns a new model class based on the original.

**Call relations**: _applied_document calls this for document-provided parameter descriptions, and _with_spawn_payload calls it for the spawn payload description. It relies on Pydantic's create_model to make the adjusted model class.

*Call graph*: called by 2 (_applied_document, _with_spawn_payload); 2 external calls (deepcopy, create_model).


##### `_run_tool`  (lines 459–494)

```
def _run_tool(name: str, description: str, inputs: dict[str, ToolInput], run: str) -> ToolDef
```

**Purpose**: Builds a new tool whose implementation runs a shell command inside the turn's sandbox. This is the one kind of tool an environment document can add, and it only has the permissions already available to that sandbox.

**Data flow**: It receives a tool name, description, input field definitions, and a command string. It builds a Pydantic input model from the declared fields, defines an async handler that runs the command and converts the result into tool output, and returns a ToolDef marked as side-effecting.

**Call relations**: _applied_document calls this when an environment tool override includes a run command. The ToolDef it returns is later included in the turn's ToolRegistry, and its nested handler is what runs when the model calls the tool.

*Call graph*: called by 1 (_applied_document); 3 external calls (__init__, Field, create_model).


##### `_run_tool.handler`  (lines 471–486)

```
async def handler(ctx: ToolContext, payload: BaseModel) -> ToolResult
```

**Purpose**: Runs the command behind an environment-created run tool and turns the process result into a model-readable tool result. It reports timeouts and nonzero exit codes as errors.

**Data flow**: It receives a ToolContext and the tool input payload. It converts the payload into environment variable assignments through _run_command, asks run_task to execute the shell command in the sandbox, combines standard output and standard error, and returns a ToolResult. A timeout or nonzero exit code sets the result as an error.

**Call relations**: This handler is created inside _run_tool and is later invoked by the tool runtime when the model calls that generated tool. It hands execution to run_task and uses _run_command to prepare the shell command safely.

*Call graph*: calls 1 internal fn (_run_command); 3 external calls (__init__, __init__, run_task).


##### `_run_command`  (lines 497–505)

```
def _run_command(run: str, payload: BaseModel) -> str
```

**Purpose**: Builds the shell command string used by an environment-created run tool. It passes tool inputs to the command as INPUT_... environment variables.

**Data flow**: It receives the configured command text and a Pydantic payload. It dumps the payload to simple JSON-like values, skips null values, quotes each value for the shell, prefixes each with an INPUT_FIELDNAME variable name, quotes the command itself, and returns a final sh -c command string.

**Call relations**: _run_tool.handler calls this immediately before running the task. It uses json.dumps for booleans and shlex.quote for shell quoting so user-provided values are passed as data rather than accidentally becoming shell syntax.

*Call graph*: called by 1 (handler); 3 external calls (dumps, model_dump, quote).

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-tool-catalog` — The shared menu of tools the agent may call, including built-in tools, extension tools, and guarded bridge tools.
- `reg-model-provider-catalog` — The shared list of available AI models and providers, including limits, prices, credentials, and adapter rules.
- `reg-prompt-skill-environment` — The saved instructions, skills, environment documents, and fingerprints that shape what an agent sees for a turn.
- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-transcript-history` — The saved conversation timeline, including messages, compacted summaries, final answers, costs, and readable history.
- `reg-memory-index-profiles` — The searchable memory layer made from synced pages, chunks, embeddings, summaries, facts, and member or workspace profiles.
- `reg-object-artifact-site-store` — The shared store of workspace objects, files, artifacts, previews, reports, websites, todos, and objective records.
- `reg-delegation-state` — The parent-child work state that tracks subagent turns, their contracts, trace links, pending results, and delivery back to the parent.
- `reg-blob-storage-state` — The raw byte/blob storage namespaces and content-addressed stored files that back artifacts, previews, environment files, workspace files, and deploy-wide assets.
- `reg-improvement-proposals` — Durable proposed changes and offline-improvement candidates, including their pending, approved, or rejected review state.
- `reg-turn-assembly-snapshot` — The resolved per-turn host package handed into execution, including selected agent/model, effective prompts, allowed tools/spawn menu, seeded file digests, skills, and routing choices.
- `reg-turn-token-budget-state` — Per-turn context and token budget state used to trim history, set completion limits, manage prompt-cache assumptions, and reconcile model usage with billing.
