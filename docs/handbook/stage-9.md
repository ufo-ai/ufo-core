# Per-turn host environment assembly  `stage-9`

This stage happens at the start of every model turn, after the system has decided which agent is allowed to act. Its job is to prepare the “room” the model will work in: the instructions it reads, the tools it may use, the skills it can call, and any starter files in its private sandbox. The host package labels this as the environment layer.

The main builder, assemble.py, gathers all these pieces and enforces limits. It may reduce what the wider platform allows, but it cannot add hidden extra powers. environment.py defines saved environment documents, which are controlled change sets for prompts, tools, model choice, skills, and files; it checks and records them so runs can be repeated. spawn_catalog.py lists which helper agents may be started with spawn and explains their required inputs.

object_views.py converts internal action definitions into safe, simple descriptions for models or portal pages. The prompts package provides prompt support, and render.py turns templates into the final system prompt, checking for missing placeholders and recording a fingerprint of the exact text.

## Files in this stage

### Host Assembly Entrypoint
Introduces the host environment layer and the main per-turn assembler that gates prompts, tools, skills, sandbox files, and permissions.

### `core/src/ufo/host/__init__.py`

`other` · `import time and cross-cutting package organization`

This is a package introduction file. It does not contain working code, but it gives an important signpost to readers and to Python: everything under `ufo.host` belongs to the layer that shapes an agent’s working environment. In plain terms, this is the part of the project concerned with what is available to an agent when it takes an action: extra capabilities called extensions, callable helpers called tools, learned or configured abilities called skills, and the prompt text that guides behavior. Without this file, the surrounding directory would lose its clear package identity in Python, and readers would have less context about why these modules belong together. Think of it like the label on a toolbox drawer: it does not turn the screwdriver itself, but it tells you that this drawer contains the items an agent may reach for during its turn.


### `core/src/ufo/host/assemble.py`

`orchestration` · `turn assembly`

Before a model takes a turn, the system needs to decide what the model is allowed to see and do. This file is where that package is assembled. It starts from extension manifests, which are declarations from installed connectors or features, and combines their prompts, tools, hooks, workspace facts, and skills with the agent's own permissions. Then it applies an optional environment document, which is a pinned set of turn-specific changes. That document can rewrite prompt text, hide tools, change tool descriptions, add sandbox-backed command tools, seed files, or adjust skills, but it cannot widen the runtime's authority. In everyday terms, this file prepares a workstation for a worker: it lays out the instruction sheet, puts only approved tools on the desk, adds reference cards, and locks away anything feature flags or permissions say should not be available. It also treats main agents and subagents differently, because subagents get their own prompt shape and grants. Important safety behavior is built in: if a document names a scoped tool that was not actually offered, the turn fails clearly instead of pretending it worked; text edits must match exactly once; disabled feature flags remove both tools and action grants before the model sees them.

#### Function details

##### `HostEnvironment.assemble`  (lines 130–244)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Builds the full environment for a single turn. It decides the prompt, available tools, granted object actions, hooks, skills, preload material, seeded files, and member-skill view that will be handed to the runtime.

**Data flow**: It receives an assemble request containing the turn, agent, audience, optional subagent profile, requested skills, grants, and optional environment document name. It loads the document if present, asks extensions for tools and hooks, removes anything hidden by feature flags, gathers workspace facts and spawn targets, builds the right prompt for an agent or subagent, applies document overrides, loads any seeded files, and finally returns an AssembledTurn containing the finished prompt, tool registry, hooks, skills, files, and grant information.

**Call relations**: This is the main coordinator in the file. It calls the smaller helpers in a fixed order: first to load documents, tools, hooks, member skills, feature flags, object-kind prompt text, skill overrides, document overrides, and spawn payload descriptions. The rest of the runtime relies on its result and does not reach back into this host layer for more permissions.

*Call graph*: calls 10 internal fn (_document_blob, hooks, member_skills, tools, _applied_document, _object_kind_index, _skills_with_document, _with_spawn_payload, flags_reading_off, granted_without_flagged); 22 external calls (__init__, __init__, __init__, span, load_environment_document, load_environment_file, turn_workspace_facts, spawn_catalog_skill, spawn_targets, render_object_kinds (+12 more)).


##### `HostEnvironment.tools`  (lines 246–263)

```
def tools(self, *, audience: Audience, member_context_authority: ExecutionAuthority) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext], ObjectVerbs]
```

**Purpose**: Collects the tools that extensions offer for this turn. A tool is a callable ability the model may use, such as a connector operation or built-in action.

**Data flow**: It takes the audience and the authority to use for member-context actions. It reads the host's manifests, credentials, indexing clients, public URL settings, blob store, artifact secret, and optional turn invoker. It passes those into the extension loader and returns the tool definitions, their extension contexts, and the object-action registry.

**Call relations**: HostEnvironment.assemble calls this near the start of turn setup. This function delegates the actual extension discovery to turn_tools, then hands the assembled catalog back so assemble can filter it by flags and grants.

*Call graph*: called by 1 (assemble); 2 external calls (turn_tools, ws_current).


##### `HostEnvironment.hooks`  (lines 265–274)

```
def hooks(self, *, audience: Audience) -> HookChain
```

**Purpose**: Collects extension hooks for this turn. Hooks are extension callbacks that can run around turn activity, like observers or lifecycle add-ons.

**Data flow**: It takes the audience, reads the host's manifests, credentials, index clients, optional tailer, and public URL, and asks the extension loader to create a HookChain. The result is a chain of callbacks ready to attach to the assembled turn.

**Call relations**: HostEnvironment.assemble calls this while preparing the turn. The extension loader supplies the hook chain, and assemble places it into the final AssembledTurn.

*Call graph*: called by 1 (assemble); 1 external calls (turn_hooks).


##### `HostEnvironment.member_skills`  (lines 276–285)

```
async def member_skills(self, *, agent_name: str) -> tuple[tuple[SkillCard, ...], SkillMaterializer]
```

**Purpose**: Loads skills contributed by workspace members for a specific agent. These are user- or member-authored reference abilities that may be included when the agent allows workspace skills.

**Data flow**: It receives the agent name, reads manifests and shared clients from the host, and asks the extension loader for member skill cards plus a materializer. It returns the cards that describe the skills and the function that can load their full contents later.

**Call relations**: HostEnvironment.assemble calls this only when the agent is configured to use workspace skills. The returned skill cards are merged into the skill registry before prompts and visibility are computed.

*Call graph*: called by 1 (assemble); 1 external calls (turn_member_skills).


##### `HostEnvironment.environment_model`  (lines 287–291)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Looks up whether an environment document asks for a specific model for the main agent or a named subagent profile. This lets the runtime choose a model before the full turn environment is assembled.

**Data flow**: It receives an environment document identifier and an optional profile name. It loads the document from the workspace blob store, selects either the main block or the named profile block, and returns that block's model name, or None if no model is specified.

**Call relations**: This is a targeted lookup path separate from full assembly. It uses the same document-loading storage check as assemble, so model selection and turn assembly read from the same source of truth.

*Call graph*: calls 1 internal fn (_document_blob); 1 external calls (load_environment_document).


##### `HostEnvironment.clis`  (lines 293–294)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Returns command-line interface credentials declared by connector manifests. These credentials are used when external connectors expose local command-line access.

**Data flow**: It reads the host's manifests and asks the extension loader to extract connector CLI credential information. The output is a dictionary keyed by CLI name.

**Call relations**: This is a small access point for code that needs connector CLI setup. It does not participate directly in assemble, but it exposes another part of the same manifest-discovered environment.

*Call graph*: 1 external calls (connector_clis).


##### `HostEnvironment.slots`  (lines 296–297)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Returns credential slots that extensions want injected. A credential slot is a named place where a secret or login token can be supplied to an extension.

**Data flow**: It reads the host's manifests and asks the extension loader for the declared credential slots. The output is a tuple of slot descriptions.

**Call relations**: This supports credential setup outside the main turn-assembly path. It uses the same manifest collection that tools, hooks, and skills use.

*Call graph*: 1 external calls (injecting_slots).


##### `HostEnvironment._document_blob`  (lines 299–302)

```
def _document_blob(self) -> WorkspaceBlobStore
```

**Purpose**: Provides the workspace blob store needed to load environment documents and seeded files. It fails clearly if this HostEnvironment was not created with blob storage.

**Data flow**: It reads the host's blob field. If a blob store exists, it returns it; if not, it raises a runtime error explaining that environment documents cannot be loaded.

**Call relations**: HostEnvironment.assemble and HostEnvironment.environment_model call this before loading environment documents. It is the safety check that prevents later code from trying to read document data from nowhere.

*Call graph*: called by 2 (assemble, environment_model).


##### `_object_kind_index`  (lines 305–322)

```
def _object_kind_index(verbs: ObjectVerbs, granted_actions: frozenset[str]) -> tuple[tuple[str, str, tuple[str, ...]], ...]
```

**Purpose**: Builds prompt-friendly information about object kinds and the actions this turn may use on them. This helps the model understand what kinds of workspace objects exist and which verbs are allowed.

**Data flow**: It receives the full object verb registry and the set of granted action IDs. It sorts object kinds by name, keeps each kind's description, and includes only the actions whose canonical IDs are actually granted. The output is a compact tuple ready to render into the prompt.

**Call relations**: HostEnvironment.assemble calls this for the main agent path after grants are computed. Its output is passed into prompt rendering so the model sees only actions it can really call.

*Call graph*: called by 1 (assemble).


##### `_skills_with_document`  (lines 325–354)

```
def _skills_with_document(skills: SkillRegistry, document: EnvironmentDocument | None) -> SkillRegistry
```

**Purpose**: Applies skill changes from an environment document to the deploy's skill registry. It can replace a skill's SKILL.md text, apply exact text edits, or add a new document-defined skill.

**Data flow**: It receives the current skill registry and an optional environment document. If there are no document skill changes, it returns the registry unchanged. Otherwise it copies the registry by name, validates that member-authored skills are not overridden and that edits target existing deploy skills, creates updated skill contents, removes replaced names from the bundled set, and returns a new SkillRegistry.

**Call relations**: HostEnvironment.assemble calls this before building prompts for both main agents and subagents. It relies on _edited for exact text replacements and parse_skill_content to turn skill files back into loaded skill records.

*Call graph*: calls 1 internal fn (_edited); called by 1 (assemble); 2 external calls (replace, parse_skill_content).


##### `_applied_document`  (lines 357–413)

```
def _applied_document(prompt: RenderedPrompt, tools: ToolRegistry, scoped: EnvironmentOverrides | None, global_tools: dict[str, ToolOverride]) -> tuple[RenderedPrompt, ToolRegistry, bool]
```

**Purpose**: Applies prompt and tool overrides from an environment document to one target's already-assembled environment. It reshapes what the model sees without expanding the platform's original grants.

**Data flow**: It receives the current rendered prompt, tool registry, scoped overrides for the main agent or subagent, and top-level tool overrides. It builds a tool map by name, rejects scoped overrides for tools that are not offered, removes disabled tools, rewrites descriptions and parameter descriptions, adds sandbox-backed run tools when requested, applies prompt replacement or edits, and returns the new prompt, new tool registry, and a flag saying whether the prompt was fully replaced.

**Call relations**: HostEnvironment.assemble calls this after the normal prompt and tools are built. This helper delegates prompt changes to _applied_prompt, parameter-description changes to _described_model, and command-backed tool creation to _run_tool.

*Call graph*: calls 3 internal fn (_applied_prompt, _described_model, _run_tool); called by 1 (assemble); 2 external calls (__init__, replace).


##### `_applied_prompt`  (lines 416–419)

```
def _applied_prompt(content: str, override: PromptOverride) -> RenderedPrompt
```

**Purpose**: Applies a prompt override to existing prompt text. The override can either replace the whole prompt or edit exact pieces of it.

**Data flow**: It receives the current prompt content and a PromptOverride. If the override supplies full text, it wraps that text as a rendered prompt. Otherwise it runs the requested exact text edits and wraps the edited result.

**Call relations**: _applied_document calls this when a scoped environment block contains prompt changes. It uses _edited for safe find-and-replace behavior, then hands back a RenderedPrompt.

*Call graph*: calls 1 internal fn (_edited); called by 1 (_applied_document); 1 external calls (rendered_prompt).


##### `_edited`  (lines 422–430)

```
def _edited(content: str, edits: tuple[TextEdit, ...], subject: str) -> str
```

**Purpose**: Performs safe text edits where each old text must appear exactly once. This prevents accidental broad replacements or silent no-op edits.

**Data flow**: It receives original content, a sequence of edits, and a subject name used in error messages. For each edit, it counts occurrences of the old text; if the count is not exactly one, it raises an error. Otherwise it replaces that one occurrence and returns the final content after all edits.

**Call relations**: _applied_prompt uses this for prompt edits, and _skills_with_document uses it for skill edits. It is the shared safety rule that makes document-driven rewriting predictable.

*Call graph*: called by 2 (_applied_prompt, _skills_with_document).


##### `_with_spawn_payload`  (lines 433–450)

```
def _with_spawn_payload(selected: tuple[ToolDef, ...], targets: tuple[SpawnTarget, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: Updates the spawn tool's input description with the specific spawn targets available in this turn. The spawn tool starts subagents, so its payload field needs to describe the valid target payload keys for the current workspace state.

**Data flow**: It receives the selected tool definitions and the turn's spawn targets. It walks through the tools, and when it finds the spawn tool, it creates a copy whose input model has a better description for the payload parameter. All other tools pass through unchanged.

**Call relations**: HostEnvironment.assemble calls this just before creating the final ToolRegistry. It uses _described_model to alter only the schema description, not the underlying handler or permission binding.

*Call graph*: calls 1 internal fn (_described_model); called by 1 (assemble); 2 external calls (replace, spawn_payload_description).


##### `_described_model`  (lines 453–466)

```
def _described_model(model: type[BaseModel], tool: str, parameters: dict[str, str]) -> type[BaseModel]
```

**Purpose**: Creates a copy of a tool input model with updated descriptions for selected parameters. This changes what the model reads about the input fields without changing the real accepted fields.

**Data flow**: It receives a Pydantic model class, the tool name, and a mapping of parameter names to new description text. It rejects parameter names the model does not actually have, deep-copies the field metadata for each changed field, replaces the descriptions, and returns a newly created model class based on the original.

**Call relations**: _applied_document uses this when an environment document rewrites tool parameter descriptions. _with_spawn_payload uses it to explain the spawn payload field for this turn.

*Call graph*: called by 2 (_applied_document, _with_spawn_payload); 2 external calls (deepcopy, create_model).


##### `_run_tool`  (lines 469–504)

```
def _run_tool(name: str, description: str, inputs: dict[str, ToolInput], run: str) -> ToolDef
```

**Purpose**: Creates a new tool definition that runs a shell command inside the turn's sandbox. This is the one kind of tool an environment document can add, and it grants no more power than the sandbox shell already has.

**Data flow**: It receives the tool name, description, input field definitions, and command string. It builds a Pydantic input model from the declared fields, defines an async handler that will run the command, and returns a side-effecting ToolDef containing that model and handler.

**Call relations**: _applied_document calls this when a tool override includes a run command. The generated handler later calls _run_command and run_task when the model invokes the tool.

*Call graph*: called by 1 (_applied_document); 3 external calls (__init__, Field, create_model).


##### `_run_tool.handler`  (lines 481–496)

```
async def handler(ctx: ToolContext, payload: BaseModel) -> ToolResult
```

**Purpose**: Executes the command for an environment-defined run tool and turns the process result into a tool response. It reports timeouts and non-zero exit codes as errors.

**Data flow**: It receives the tool context and the model-supplied payload. It builds the shell command with _run_command, runs it through run_task, combines standard output and standard error, and returns a ToolResult. A timeout becomes an error message, a zero exit code returns normal output, and any other exit code returns output plus the exit code marked as an error.

**Call relations**: This function is created inside _run_tool and becomes the handler attached to the generated ToolDef. It is not called during assembly; it runs later only if the model chooses that environment-defined tool.

*Call graph*: calls 1 internal fn (_run_command); 3 external calls (__init__, __init__, run_task).


##### `_run_command`  (lines 507–515)

```
def _run_command(run: str, payload: BaseModel) -> str
```

**Purpose**: Builds the exact shell command string for an environment-defined run tool. It passes tool inputs as environment variables so the command can read them safely by name.

**Data flow**: It receives the configured command text and the validated payload model. It dumps payload fields, skips fields whose value is None, converts booleans through JSON so they become true or false, quotes values for the shell, prefixes them as INPUT_FIELDNAME variables, and returns a sh -c command string.

**Call relations**: _run_tool.handler calls this immediately before running the task. Its quoting step is important because the handler hands the resulting string to a shell.

*Call graph*: called by 1 (handler); 3 external calls (dumps, model_dump, quote).


##### `flags_reading_off`  (lines 518–536)

```
async def flags_reading_off(tools: tuple[ToolDef, ...], actions: Mapping[str, Mapping[str, BoundAction]]) -> frozenset[str]
```

**Purpose**: Finds feature flags that are declared by tools or actions but are currently off. A feature flag is a runtime switch that can hide a feature without changing the code.

**Data flow**: It receives the available tool definitions and object actions. It collects every non-empty flag name declared on them, reads all flag values asynchronously with a default of off, and returns the set of flags whose value is not enabled.

**Call relations**: HostEnvironment.assemble calls this after gathering all tools and verbs. The returned withheld flags are used to remove flagged tools and to trim granted actions before the prompt and tool registry are finalized.

*Call graph*: called by 1 (assemble); 2 external calls (gather, flag_enabled).


##### `granted_without_flagged`  (lines 539–549)

```
def granted_without_flagged(granted: frozenset[str], actions: Mapping[str, Mapping[str, BoundAction]], withheld: frozenset[str]) -> frozenset[str]
```

**Purpose**: Removes action grants whose feature flag is off. This keeps permission records aligned with what the model is actually allowed to see and use.

**Data flow**: It receives a set of granted action IDs, the full action registry, and the set of withheld flag names. It builds a lookup from action ID to flag, then returns only the granted actions whose flag is not withheld.

**Call relations**: HostEnvironment.assemble calls this for both main agents and subagents after feature flags are read. Its result is then used to choose tool action verbs and to render object-kind information in the prompt.

*Call graph*: called by 1 (assemble).


### Environment and Action Catalogs
Defines reusable environment overrides and prepares the safe action and spawn-target views exposed to the model or portal.

### `core/src/ufo/host/environment.py`

`config` · `config load and turn setup`

An environment document is like a sealed instruction sheet for an experiment. It can say, for example, “use this version of the prompt,” “hide this tool,” “change this tool’s description,” “replace this skill,” or “place this file in the sandbox before the model runs.” The important rule is that these changes can narrow what the system offers, but they cannot silently grant more power than the platform already allowed. The main exception is a custom `run` tool, but even that only runs inside the turn’s existing sandbox, so it does not escape the normal safety boundary.

The file uses Pydantic models, which are structured Python classes that validate incoming data, to describe exactly what is allowed. Invalid combinations fail early. For example, a prompt override must either replace the whole prompt or make specific text edits, not both. A disabled tool cannot also redefine itself.

The file also makes documents reproducible. A document written as YAML or JSON is parsed, converted into a standard JSON form, and hashed with SHA-256, which is a fingerprint of the bytes. That digest becomes the stable name used to store and later reload the document. This matters because crash recovery and repeated experiments need to get byte-for-byte the same instructions, not “whatever the latest file says now.”

#### Function details

##### `PromptOverride._one_form`  (lines 59–62)

```
def _one_form(self) -> 'PromptOverride'
```

**Purpose**: This validation check makes sure a prompt override has exactly one clear meaning. It must either provide a whole replacement prompt or a list of targeted text replacements, but not both and not neither.

**Data flow**: It reads the already-filled `PromptOverride` object, especially its `text` and `replace` fields. If the fields describe one valid style of override, it returns the same object unchanged. If the fields are ambiguous or empty, it raises an error before the document can be used.

**Call relations**: This runs automatically while Pydantic is building a `PromptOverride` from an environment document. It protects later prompt-building code from having to guess whether the author meant a full replacement or a patch-style edit.


##### `ToolOverride._one_meaning`  (lines 91–104)

```
def _one_meaning(self) -> 'ToolOverride'
```

**Purpose**: This validation check makes sure each tool override says one sensible thing. A tool can be disabled, rewritten, or defined as a sandbox command, but the document cannot mix incompatible meanings.

**Data flow**: It reads the fields of a `ToolOverride`, such as `enabled`, `description`, `parameters`, `input`, and `run`. It accepts combinations that have a clear effect and returns the same object. It rejects cases like disabling a tool while also changing its description, or giving input fields without defining a command to run.

**Call relations**: This runs automatically when an environment document is parsed. It keeps tool changes safe and understandable before any later code offers those tools to a model.


##### `EnvironmentDocument._entries_parse`  (lines 150–171)

```
def _entries_parse(self) -> 'EnvironmentDocument'
```

**Purpose**: This validation check looks inside the document’s skill and file entries to make sure they are usable and safe. It catches bad skill text, unsafe file destinations, and malformed file digests early.

**Data flow**: It reads the document’s `skills` and `files` maps. For full skill replacements, it asks the skill parser to confirm the text is a valid skill. For file destinations, it rejects empty paths, absolute paths, and paths that would escape the workspace. For file values, it checks that each one looks like a valid SHA-256 digest. If everything passes, it returns the same document; otherwise it raises an error.

**Call relations**: This runs automatically while an `EnvironmentDocument` is being validated. It calls the skill parser to verify replacement skills, uses the containment helper to prevent unsafe paths, and uses the environment digest pattern to check file references. This means later turn setup can trust that the document is pointing to valid skills and workspace-contained files.

*Call graph*: 3 external calls (contained_relative, parse_skill_content, fullmatch).


##### `parse_environment_document`  (lines 174–190)

```
def parse_environment_document(body: bytes) -> tuple[EnvironmentDocument, bytes, str]
```

**Purpose**: This function turns an uploaded YAML or JSON environment document into a validated object, a standard byte form for storage, and a digest that uniquely names it. It is the main front door for accepting an authored environment document.

**Data flow**: It receives raw bytes from a document. First it rejects documents over the size limit. Then it parses the bytes as YAML, which also covers JSON because JSON is valid YAML in this context. It validates the result as an `EnvironmentDocument`, converts that validated object into canonical JSON with stable key ordering, and computes a SHA-256 digest from those canonical bytes. It returns the parsed document, the canonical bytes, and the digest string.

**Call relations**: This is called by `store_environment_document` before saving a document. It uses YAML loading to read the author’s file, JSON dumping to make a stable stored form, and SHA-256 hashing to create the content-based name.

*Call graph*: called by 1 (store_environment_document); 3 external calls (sha256, dumps, safe_load).


##### `store_environment_document`  (lines 193–196)

```
async def store_environment_document(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function validates an environment document and saves its canonical form in the workspace blob store. It returns the digest that future turns can pin to.

**Data flow**: It receives a workspace blob store and raw document bytes. It passes the bytes to `parse_environment_document`, gets back canonical bytes and a digest, and writes those bytes under a key derived from the digest. The visible result is the digest string; the side effect is that the document is now stored in the blob store.

**Call relations**: This is the storage path after a client or caller provides an environment document. It relies on `parse_environment_document` for validation and fingerprinting, then hands the canonical bytes to `WorkspaceBlobStore.put` so later code can reload the exact same document by digest.

*Call graph*: calls 2 internal fn (put, parse_environment_document).


##### `load_environment_document`  (lines 199–205)

```
async def load_environment_document(blob: WorkspaceBlobStore, digest: str) -> EnvironmentDocument
```

**Purpose**: This function reloads a stored environment document by digest and verifies that the stored bytes still match that digest. It prevents corrupted or wrongly-addressed data from being used.

**Data flow**: It receives a workspace blob store and a digest string. It first checks that the digest has the expected shape. Then it reads the stored canonical document bytes from the blob store, recalculates their SHA-256 digest, and compares that value with the requested digest. If they match, it parses the JSON bytes into an `EnvironmentDocument` and returns it. If anything is malformed or mismatched, it raises an error.

**Call relations**: This is used when turn setup needs to resolve the environment digest pinned on a turn. It calls the blob store to fetch the bytes, uses SHA-256 to prove they are the requested content, and then rebuilds the validated document object for the rest of the host to apply.

*Call graph*: calls 1 internal fn (get); 2 external calls (sha256, fullmatch).


##### `store_environment_file`  (lines 208–217)

```
async def store_environment_file(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function stores a raw file that an environment document wants placed into a turn’s sandbox. It names the file content by its SHA-256 digest so identical uploads share the same stored object.

**Data flow**: It receives a workspace blob store and raw file bytes. It rejects files over the size limit, computes a digest from the bytes, and writes the unchanged bytes into the blob store under a key based on that digest. It returns the digest string that can be referenced from an environment document.

**Call relations**: This is the upload path for files mentioned by environment documents. Unlike document storage, it does not parse or canonicalize the content; it simply fingerprints the raw bytes with SHA-256 and stores them through `WorkspaceBlobStore.put`.

*Call graph*: calls 1 internal fn (put); 1 external calls (sha256).


##### `load_environment_file`  (lines 220–224)

```
async def load_environment_file(blob: WorkspaceBlobStore, digest: str) -> bytes
```

**Purpose**: This function reloads a raw file by digest and checks that the bytes still match the requested digest. It is used before writing environment-provided files into a sandbox.

**Data flow**: It receives a workspace blob store and a digest string. It fetches the stored bytes from the blob store, recalculates their SHA-256 digest, and compares it with the requested digest. If the check passes, it returns the raw bytes; if not, it raises an error.

**Call relations**: This is the retrieval side of `store_environment_file`. Turn setup can call it when an environment document lists files to place in the workspace, and the digest check ensures the sandbox receives exactly the file content that was pinned.

*Call graph*: calls 1 internal fn (get); 1 external calls (sha256).


### `core/src/ufo/host/spawn_catalog.py`

`domain_logic` · `per turn, while preparing spawn/tool instructions`

A `spawn` action can start two kinds of targets: fixed subagent profiles that come from the running system, and workspace agents that users have created or been allowed to use. This file makes sure the list shown to the caller is the same list that `spawn` will actually use. Without it, a caller might guess a target name or payload shape from stale instructions, then fail only after making a wrong call.

The central idea is a small record called `SpawnTarget`: it stores the target name, whether it is a profile or an agent, and the payload keys that target needs. Each turn, `spawn_targets` reads the live subagent registry and the current workspace database. It filters workspace agents by permission: regular members see their own active agents, while workspace admins can see all active agents. If a workspace agent has the same name as a profile, the agent is shown with an `agent:` prefix so there is no ambiguity.

The same target list is then reused in two places. `spawn_payload_description` writes the payload field description directly into the spawn tool, where every caller must see it. `spawn_catalog_skill` creates a readable skill page with a table of valid targets. This is like printing the current train timetable from the same system that dispatches the trains, instead of maintaining a separate poster by hand.

#### Function details

##### `spawn_targets`  (lines 42–87)

```
async def spawn_targets(registry: SubagentRegistry, authority: ExecutionAuthority) -> tuple[SpawnTarget, ...]
```

**Purpose**: This function builds the complete list of targets that `spawn` may dispatch during the current turn. It combines system-defined subagent profiles with active workspace agents the current member is allowed to use, and records the payload keys each target requires.

**Data flow**: It receives the live subagent registry and the current execution authority, which says who is acting. It finds the member ID, reads the current workspace, checks whether that member is an admin, and queries the workspace database for active agent rows they may use. It also reads each profile's input contract. The result is a tuple of `SpawnTarget` records, sorted for profiles and database-ordered for agents, with names adjusted when an agent would otherwise collide with a profile name.

**Call relations**: This is the source of truth for the rest of the file. While building its answer, it asks the authority code who the member is, opens a workspace transaction to read database rows, checks admin status, uses the current workspace ID, and turns profile or agent input schemas into simple payload-key text. The descriptions and catalog skill are meant to be built from this result so their wording cannot drift away from what spawning will really accept.

*Call graph*: 8 external calls (__init__, select, workspace_tx, authority_member_id, member_is_admin, input_contract, payload_keys, ws_current).


##### `spawn_payload_description`  (lines 90–99)

```
def spawn_payload_description(targets: tuple[SpawnTarget, ...]) -> str
```

**Purpose**: This function turns the current target list into a short description for the `payload` field of the spawn action. Its job is to put the required keys directly where the caller is writing the payload, instead of making them discover the keys by failing.

**Data flow**: It receives a tuple of `SpawnTarget` records. If the list is empty, it returns a generic sentence saying the arguments must match the target's input schema. If targets exist, it joins them into a compact sentence like “target takes keys,” and returns that sentence as the field description.

**Call relations**: This function depends on the target list produced earlier, usually by `spawn_targets`. It does not read the database or registry itself; it simply converts the already-known dispatch options into wording that can be placed into the spawn tool definition for the current turn.


##### `spawn_catalog_skill`  (lines 102–122)

```
def spawn_catalog_skill(targets: tuple[SpawnTarget, ...]) -> RuntimeSkill
```

**Purpose**: This function creates a runtime skill named `spawn-catalog`, which is a readable table of all spawn targets and the payload keys each one takes. A caller can load this skill when choosing a target it has not used before.

**Data flow**: It receives the same tuple of `SpawnTarget` records. It formats each target as a Markdown table row, wraps the table in explanatory text, and also builds a raw Markdown skill document with front matter containing the skill name and description. It returns a `RuntimeSkill` object containing the name, description, instructions, and raw skill text.

**Call relations**: This function is the presentation side of the spawn catalog. It does not decide which targets are allowed; that decision comes from `spawn_targets`. Once given that list, it hands the formatted instructions into `RuntimeSkill`, so the runtime can expose the catalog as a loadable skill for the current turn.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/object_views.py`

`domain_logic` · `model discovery and portal/request rendering`

The runtime has actions attached to objects, but those internal action records contain more detail than a model or web portal should need. This file creates a clean “view” of an action: its name, human description, expected input shape, and a pre-filled call template that says how to invoke it later. Think of it like turning a kitchen’s full recipe binder into a menu item with an order form attached.

The central data shape is ActionView, a frozen Pydantic model. Pydantic is a library that checks and structures data. “Frozen” means the view cannot be changed after it is built, and “extra forbidden” means surprise fields are rejected. That helps keep the public action description predictable.

The helper functions decide which actions are visible in different places. Some actions are only for model planning, while others have “presentation” information that lets them appear as portal controls, such as buttons with labels or confirmation text. The file also computes which action IDs are allowed to be called from an embedded app page, including both global tools and object-bound actions that explicitly permit frame use. Without this file, callers would either see too much internal detail or would not have a consistent way to discover and call object actions.

#### Function details

##### `action_view`  (lines 27–53)

```
def action_view(kind: str, bound: 'BoundAction', *, name: str | None=None, agent: str | None=None, generation: UUID | None=None, presented: bool=False) -> ActionView
```

**Purpose**: Builds one public-facing ActionView from an internal bound action. It packages the action’s description, input requirements, and a ready-to-use call template so another part of the system can show or invoke the action safely.

**Data flow**: It receives the action kind, a bound action, and optional context such as object name, agent name, generation ID, and whether portal presentation details should be included. It creates a call dictionary containing the action type, action name, optional context, and an empty input object. It then reads the action’s schema and description, optionally adds display label and confirmation text, and returns an immutable ActionView.

**Call relations**: This is the builder used when presented_action_views has decided an action should be shown. It hands the finished data to ActionView.__init__, which validates and stores the view in the fixed public shape.

*Call graph*: called by 1 (presented_action_views); 1 external calls (__init__).


##### `presented`  (lines 56–58)

```
def presented(bound: 'BoundAction') -> bool
```

**Purpose**: Answers a simple question: should this action be shown as a portal control? It returns true only when the action has presentation information and is not marked as profile-only.

**Data flow**: It receives a bound action and inspects two pieces of its internal action data: whether presentation details exist, and whether it is restricted to profile use only. From those flags it produces a true-or-false answer and does not change anything.

**Call relations**: presented_action_views calls this while filtering actions for a target, so only displayable actions become views. frame_admissible_ids also calls it before allowing an object-bound action to be callable from an embedded frame.

*Call graph*: called by 2 (frame_admissible_ids, presented_action_views).


##### `presented_action_views`  (lines 61–77)

```
def presented_action_views(actions: 'Mapping[str, Mapping[str, BoundAction]]', kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Collects all portal-presented actions for one target and turns them into ActionView objects. It is used when the system needs a sorted list of actions that should appear to a user or portal for a particular object or binding.

**Data flow**: It receives a nested action map, the target kind, a required binding, and optional object name and generation ID. It looks at actions for that kind, sorts them by their short name, keeps only actions that are bound to the requested binding and matching name, and also pass the presented check. Each surviving action is converted into an ActionView with presentation details included, and the function returns them as a tuple.

**Call relations**: This function is the main projection step for portal-visible object actions. During that step it asks presented whether an action is actually displayable, then calls action_view to build the clean public record for each accepted action.

*Call graph*: calls 2 internal fn (action_view, presented).


##### `frame_admissible_ids`  (lines 80–97)

```
def frame_admissible_ids(tools: 'Iterable[ToolDef]', actions: 'Mapping[str, Mapping[str, BoundAction]]') -> tuple[str, ...]
```

**Purpose**: Builds the list of action IDs that an embedded app page is allowed to call. This is a safety filter: only tools and object actions explicitly marked as usable from a frame are included.

**Data flow**: It receives global tool definitions and the nested map of object-bound actions. It gathers names of global tools that are not object-bound and have presentation settings allowing frame use. It also gathers canonical IDs from presented object-bound actions whose presentation settings allow frame use. It removes duplicates, sorts the combined set, and returns it as a tuple of strings.

**Call relations**: When deciding what an embedded page may call, this function checks object-bound actions with presented first, so hidden or profile-only actions are not admitted. It does not build full action views; it only returns the canonical IDs needed for permission or allow-list decisions.

*Call graph*: calls 1 internal fn (presented).


### Prompt Rendering
Provides the prompt package boundary and renders the final checked, fingerprinted system prompt for the model turn.

### `core/src/ufo/runtime/prompts/__init__.py`

`other` · `import time`

This is an empty Python package file. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Here, it makes `core/src/ufo/runtime/prompts` usable as a named place for prompt-related modules elsewhere in the system.

Think of it like a label on a drawer: the drawer may contain useful items in other files, but this label is what lets the rest of the code refer to the drawer by name. Without this file, depending on the Python version and packaging setup, imports from this folder might fail or behave less predictably.

Because the file is empty, it does not run setup code, define helper functions, or expose shortcut names. Its job is structural rather than active.


### `core/src/ufo/runtime/prompts/render.py`

`domain_logic` · `prompt construction before a model turn`

A language model prompt in this system is not written as one giant fixed string. It is assembled from a shell template plus pieces such as the agent’s instructions, available skills, workspace facts, capability sections, citation rules, and the model’s knowledge cutoff date. This file is the assembly station for those pieces.

Its main job is to make prompt building safe. Placeholders like {{agent-prompt}} or {{sections}} are expected to be filled before the prompt reaches the model. If anything is missing, extra, or still written in braces after rendering, this file raises an error instead of silently sending a broken instruction. That matters because a literal leftover placeholder could confuse the model or hide a configuration mistake.

The file also formats helper blocks. For example, it can turn a list of skills into an <available_skills> section, or turn known workspace capabilities into a compact block that tells the model what is already set up. After the final text is produced, it removes excessive blank lines and calculates a SHA-256 digest, which is a stable content fingerprint. Like a receipt number for the prompt, that digest lets logs and observability tools show exactly which prompt version was used.

#### Function details

##### `rendered_prompt`  (lines 61–62)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: Wraps finished prompt text together with a digest, which is a stable fingerprint of that exact text. This lets the system send the prompt while also recording a compact identifier for debugging and tracking.

**Data flow**: It receives the final prompt text as a string. It hashes that text with SHA-256, prefixes the hash with "sha256:", and returns a RenderedPrompt object containing both the digest and the original content. It does not change anything outside itself.

**Call relations**: After render_template has filled and cleaned the prompt, it calls rendered_prompt as the final step. rendered_prompt hands back the packaged result that the rest of the runtime can send to the model and record in logs.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 65–82)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: Builds the main agent’s system prompt from the standard shell template. It also turns the model’s machine-readable knowledge cutoff date, such as "2026-02", into a human-readable phrase, such as "February 2026".

**Data flow**: It receives the agent’s own prompt text, prompt sections from packs, an optional list of skills, and the model’s knowledge cutoff date. It formats the cutoff date, inserts it into the knowledge cutoff block, places that block into the shell template, and then passes everything to render_template. The output is a RenderedPrompt with final text and digest.

**Call relations**: This is the higher-level entry for building the normal system prompt. It prepares the special knowledge-cutoff part first, then delegates the careful template filling and validation work to render_template.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 85–103)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: Fills a prompt template with the agent prompt, skills, citation block, and contributed sections, while refusing to leave any unresolved placeholder behind. This is the safety gate that prevents broken prompt text from reaching the model.

**Data flow**: It receives a template, agent prompt text, a mapping of variable names to replacement text, a list of skills, and a list of sections. First it substitutes variables inside the agent prompt. Then it replaces the known template slots with rendered skill text, citation text, section text, and the filled agent prompt. It checks for any remaining {{name}}-style placeholders, collapses long blank gaps, trims the end, and returns a RenderedPrompt. If a required slot or variable is wrong, it raises an error instead.

**Call relations**: render_system_prompt calls this after preparing the shell template. Inside, render_template relies on _substitute_vars to safely fill variables, render_skill_index to format skills, and rendered_prompt to package the final result.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_workspace_facts`  (lines 115–128)

```
def render_workspace_facts(lines: Sequence[str]) -> str
```

**Purpose**: Turns a list of already-available workspace capabilities into one prompt block. This tells the model what is already set up so it does not suggest doing the same setup again.

**Data flow**: It receives a sequence of text lines, where each line describes a capability the workspace already has. If the list is empty, it returns an empty string. Otherwise it wraps the lines in a <workspace_capabilities> block and adds one shared closing instruction saying these things are already set up.

**Call relations**: This helper is used when other parts of the system need to contribute workspace capability facts to the prompt. It does not call other functions in this file; it simply formats the text block that can later be inserted as part of prompt sections.


##### `render_object_kinds`  (lines 134–151)

```
def render_object_kinds(kinds: Sequence[tuple[str, str, Sequence[str]]]) -> str
```

**Purpose**: Formats the types of workspace objects the model can talk about or act on during this turn. For each object kind, it can show a description and the actions available for that kind.

**Data flow**: It receives a sequence of object kind entries, each containing a name, a description, and a list of actions. If there are no entries, it returns an empty string. Otherwise it builds a <workspace_objects> block, adding one line per kind and an extra actions line when actions are present.

**Call relations**: This is a standalone formatting helper for prompt content contributed elsewhere. Its output can become part of the sections that render_template later inserts into the full prompt.


##### `render_skill_index`  (lines 154–163)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: Turns the list of loadable skills into the prompt’s <available_skills> block. This gives the model a clear menu of skills it may be able to use.

**Data flow**: It receives skill name and description pairs. If the list is empty, it returns an empty string. Otherwise it writes an opening <available_skills> tag, one bullet for each skill, and a closing tag.

**Call relations**: render_template calls this while filling the skill slot in a prompt template. It supplies the formatted skill block that becomes part of the final system prompt.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 166–173)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: Safely replaces {{variable}} placeholders inside an agent prompt. It is strict on purpose: every placeholder must have a supplied value, and every supplied value must match a declared placeholder.

**Data flow**: It receives prompt text and a mapping from variable names to replacement strings. It scans the text for placeholders, compares them with the supplied variable names, and raises an error if anything is missing or extra. If everything matches, it replaces each placeholder with its value and returns the filled text.

**Call relations**: render_template calls this before inserting the agent prompt into the larger shell. This keeps variable mistakes local and loud, so an unfinished {{variable}} does not accidentally reach the model.

*Call graph*: called by 1 (render_template).

## 📊 State Registers Touched

- `reg-model-catalog` — The live menu of AI models, their capabilities, providers, and calling rules.
- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-skill-library` — The stored and packaged reusable skill instructions and files available to agents.
- `reg-credential-connections` — The encrypted outside-account credentials, reusable connections, and grants that let agents use them.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
- `reg-environment-documents` — The saved per-agent run environment describing prompts, tools, skills, files, and model overrides.
- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-self-improvement-state` — Saved prompt-change proposals, replay/evaluation results, promotion gates, and corpus entries used by the self-improvement loop.
- `reg-turn-resource-budget` — Per-turn context-window, token, image, reasoning, and cost/resource budgets derived before execution and consumed by prompt assembly, model calls, tools, and accounting.
