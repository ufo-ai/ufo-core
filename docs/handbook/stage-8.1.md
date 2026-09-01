# Prompt, Skill, and Tool Catalog Construction  `stage-8.1`

This stage prepares the “workspace desk” an agent uses for a single turn. Before the model starts working, the host environment is assembled: prompts, tools, skills, setup files, and any safe changes from environment documents. These documents are stored by hash, meaning the exact same contents can be replayed later.

The prompt renderer fills in the final system message and fingerprints it so changes are traceable. Delivery rules add shared writing guidance for replies and subagent reports. The tool registry checks that every callable tool has a clear name, safe description, and valid declaration, while object views decide which actions are safe to show.

The skill system reads reusable instruction folders, registers them, loads selected ones, and avoids duplicates. User-created skills are saved in a persistent skill store, while skill selection ranks or shortens saved skills so the prompt does not overflow. A model-catalog skill lists available AI models from live registry data. The spawn catalog creates up-to-date help text for delegating work to subagents. Package marker files simply make these areas importable by the rest of the system.

## Files in this stage

### Host Environment Foundations
Defines the host environment package and the persisted environment documents that can safely shape a model turn.

### `core/src/ufo/host/__init__.py`

`other` · `cross-cutting`

This file does not contain executable code. Its job is to label and introduce a package: a folder of related Python files. The short module comment explains the purpose of this part of the project. In plain terms, `ufo.host` is where the project gathers the outside-facing resources that shape what an agent can do during one step of its work. That includes extensions, which add extra abilities; tools, which let the agent take actions; skills, which are reusable capabilities; and prompts, which are instructions or text templates that guide behavior.

You can think of this package like the workbench prepared before someone starts a task. The agent does not act in an empty room; it is given certain instruments, instructions, and helper abilities. This package is where those surrounding pieces belong.

Because this file only contains a docstring, nothing would directly fail at runtime if its text changed. But it matters as a signpost for readers and maintainers. It tells them what kind of code should live under `ufo.host` and how to understand that folder’s role in the larger system.


### `core/src/ufo/host/environment.py`

`config` · `config load and turn setup`

An environment document is like a sealed instruction sheet for an experiment. It can say, for example, “use this prompt wording,” “hide this tool,” “rename this tool description,” “replace this skill text,” or “place this file in the workspace before the model runs.” This file describes what those instruction sheets are allowed to contain, checks that they are safe and well formed, and saves or reloads them from blob storage.

The safety rule is important: these documents may narrow what the platform already offers, but they must not silently grant extra power. The one special addition is a `run` tool, but even that runs only inside the turn’s existing sandbox, so it cannot escape the sandbox’s limits.

The file uses Pydantic models, which are Python classes that validate incoming data, to reject confusing or unsafe combinations. For example, a prompt override must be either a full replacement or a set of exact text edits, not both. A disabled tool cannot also have a new description. File destinations must be relative paths inside the workspace, not absolute paths or path tricks like escaping with `..`.

When a document is stored, it is first converted into canonical JSON: a stable byte-for-byte form. The code then computes a SHA-256 hash, which is a fingerprint of the content. That digest becomes the name used to pin and later verify the document. If stored bytes do not match the claimed digest, loading fails loudly instead of trusting corrupted or wrong content.

#### Function details

##### `PromptOverride._one_form`  (lines 59–62)

```
def _one_form(self) -> 'PromptOverride'
```

**Purpose**: This validator makes sure a prompt override has exactly one clear meaning. It must either provide a whole new prompt text or provide a list of find-and-replace edits, but it cannot do both and it cannot do neither.

**Data flow**: It reads the `text` field and the `replace` edits already parsed into the prompt override. If exactly one style is present, it leaves the object unchanged. If the object is ambiguous or empty, it raises an error so the document is rejected before a turn can use it.

**Call relations**: This runs automatically while Pydantic is building a `PromptOverride` from an environment document. It protects later prompt assembly code from having to guess whether the author meant a full replacement or small edits.


##### `ToolOverride._one_meaning`  (lines 91–104)

```
def _one_meaning(self) -> 'ToolOverride'
```

**Purpose**: This validator makes sure each tool override says one sensible thing. It prevents combinations such as disabling a tool while also trying to edit it, or defining input fields without a command to run.

**Data flow**: It reads the parsed tool override fields: description changes, parameter-description changes, enabled/disabled status, custom input fields, and any sandbox command. Valid combinations pass through unchanged. Invalid combinations become clear validation errors, so a bad tool experiment fails before it reaches the model.

**Call relations**: This runs automatically when an environment document contains a tool override. It prepares clean, unambiguous tool instructions for the code that later builds the tool list shown to the model.


##### `EnvironmentDocument._entries_parse`  (lines 150–171)

```
def _entries_parse(self) -> 'EnvironmentDocument'
```

**Purpose**: This validator checks the parts of an environment document that need deeper safety checks: replacement skills and files that will be copied into the workspace. It makes sure added skill text really parses as a skill and that file destinations cannot escape the workspace.

**Data flow**: It reads the document’s `skills` and `files` maps. For full skill replacements, it asks the skill parser to confirm the supplied `SKILL.md` text is valid. For files, it checks that each destination is a non-empty relative workspace path, asks the containment helper to catch unsafe paths, and checks that each referenced file value looks like a SHA-256 digest. If all checks pass, the document is returned unchanged; otherwise validation stops with an error.

**Call relations**: This runs as part of constructing an `EnvironmentDocument`, including when `parse_environment_document` validates uploaded YAML or JSON and when `load_environment_document` rebuilds a stored document. It hands off skill text to `parse_skill_content`, path safety to `contained_relative`, and digest-shape checking to `ENVIRONMENT_DOCUMENT_RE.fullmatch`.

*Call graph*: 3 external calls (contained_relative, parse_skill_content, fullmatch).


##### `parse_environment_document`  (lines 174–190)

```
def parse_environment_document(body: bytes) -> tuple[EnvironmentDocument, bytes, str]
```

**Purpose**: This function turns an uploaded YAML or JSON environment document into a validated `EnvironmentDocument`, plus the exact canonical bytes that should be stored. It also computes the digest that will identify those bytes forever.

**Data flow**: It receives raw document bytes. First it rejects documents over the size limit. Then it parses the bytes as YAML, which also covers JSON syntax, validates the loaded data against `EnvironmentDocument`, converts the validated document into stable JSON with sorted keys, and hashes those canonical bytes with SHA-256. It returns the parsed document, the canonical stored bytes, and the `sha256:...` digest.

**Call relations**: This is the front door for accepting authored environment documents. `store_environment_document` calls it before writing anything to blob storage, so only validated and canonicalized documents are stored.

*Call graph*: called by 1 (store_environment_document); 3 external calls (sha256, dumps, safe_load).


##### `store_environment_document`  (lines 193–196)

```
async def store_environment_document(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function saves an environment document into workspace blob storage under a name derived from its content. It returns the digest that future turns can use to pin and reload the exact same document.

**Data flow**: It receives a workspace blob store and raw document bytes. It calls `parse_environment_document`, which validates the document, creates canonical JSON bytes, and computes the digest. Then it writes those canonical bytes to the blob store using an `environment/` key based on the digest, and returns the digest to the caller.

**Call relations**: This is used when a client or host needs to persist an environment document. It relies on `parse_environment_document` for validation and hashing, then hands the finished bytes to `WorkspaceBlobStore.put` for storage.

*Call graph*: calls 2 internal fn (put, parse_environment_document).


##### `load_environment_document`  (lines 199–205)

```
async def load_environment_document(blob: WorkspaceBlobStore, digest: str) -> EnvironmentDocument
```

**Purpose**: This function retrieves a previously stored environment document by digest and proves that the stored bytes still match that digest. It prevents the system from accidentally using the wrong or corrupted document.

**Data flow**: It receives a blob store and a digest string. It first checks that the digest has the expected `sha256:...` shape. Then it reads the stored canonical bytes from the `environment/` blob key, hashes those bytes again, and compares the result with the requested digest. If they match, it parses the JSON bytes back into an `EnvironmentDocument`; if not, it raises an error.

**Call relations**: This is used when a turn refers to an already pinned environment document. It calls `WorkspaceBlobStore.get` to fetch the bytes, uses SHA-256 to verify them, and lets `EnvironmentDocument` validation run again when rebuilding the model object.

*Call graph*: calls 1 internal fn (get); 2 external calls (sha256, fullmatch).


##### `store_environment_file`  (lines 208–217)

```
async def store_environment_file(blob: WorkspaceBlobStore, body: bytes) -> str
```

**Purpose**: This function saves a raw file that an environment document can later place into a turn’s sandbox. Unlike environment documents, the file is not parsed; it is stored exactly as uploaded and addressed by its content hash.

**Data flow**: It receives a blob store and raw file bytes. It rejects files over the file-size limit, computes a SHA-256 digest of the bytes, writes the bytes to the blob store under an `environment/files/` key based on that digest, and returns the digest.

**Call relations**: This is used when a document refers to an external file that must be uploaded first. It hands the raw bytes to `WorkspaceBlobStore.put`, and the returned digest can then appear in the document’s `files` section.

*Call graph*: calls 1 internal fn (put); 1 external calls (sha256).


##### `load_environment_file`  (lines 220–224)

```
async def load_environment_file(blob: WorkspaceBlobStore, digest: str) -> bytes
```

**Purpose**: This function retrieves a stored environment file and verifies that its bytes still match the requested digest. It gives the caller the exact file content that should be written into the sandbox.

**Data flow**: It receives a blob store and a digest. It reads the stored bytes from the `environment/files/` blob key, hashes them with SHA-256, and compares the result with the digest. If the check passes, it returns the raw bytes; if not, it raises an error.

**Call relations**: This is used later when a turn is preparing sandbox files named by an environment document. It calls `WorkspaceBlobStore.get` to fetch the file and uses digest verification before handing the bytes back for use.

*Call graph*: calls 1 internal fn (get); 1 external calls (sha256).


### `core/src/ufo/host/ext/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `core/src/ufo/host/ext` directory part of the `ufo.host.ext` namespace, so nearby extension-related modules can be imported using normal Python import paths. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the system find the drawer reliably. Because this file is empty, it does not run setup code, expose shortcuts, or change how the extension modules behave. If it were missing in environments that still rely on package marker files, imports from this folder could fail or become less predictable.


### Prompt and Delivery Text
Provides prompt rendering, prompt package structure, and shared delivery-writing guidance for agents and subagents.

### `core/src/ufo/runtime/prompts/__init__.py`

`other` · `cross-cutting`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. Here, it means code elsewhere can refer to modules inside `core/src/ufo/runtime/prompts` using normal package-style imports.

There is no executable logic in this file: no functions, classes, settings, or startup work. Its value is structural. It is like a label on a drawer: the label does not contain the tools, but it lets the rest of the workshop find the drawer reliably.

Without this file, depending on the Python version and packaging setup, imports from this folder could be less predictable or fail in some environments. Keeping it present makes the project layout explicit and helps prompt-related runtime code live under a clear namespace.


### `core/src/ufo/runtime/prompts/render.py`

`domain_logic` · `prompt construction before model calls`

A system prompt is the instruction sheet the project gives to the model before asking it to work. This file is the prompt assembly table: it takes fixed markdown templates shipped with the code, adds the agent’s own instructions, adds available skills, adds capability sections from packs, adds citation rules, and inserts the model’s knowledge cutoff date in a human-readable form.

The important safety feature is strict checking. Prompt text can contain placeholders such as {{some_var}}. This renderer makes sure every placeholder in the agent prompt has a supplied value, and every supplied value was actually declared. After all filling is done, it checks again for any leftover {{...}} text. If anything remains, it raises an error instead of sending a broken instruction to the model. That matters because an unfilled prompt hole could confuse the model or silently remove an important rule.

It also normalizes extra blank lines and trims the end, so the final prompt is tidy. Finally, it calculates a SHA-256 digest, which is a stable fingerprint of the exact prompt content. Like a receipt number for a document, this lets logs and observability tools show exactly which prompt version was used.

#### Function details

##### `rendered_prompt`  (lines 61–62)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: Wraps finished prompt text together with a digest, which is a fingerprint of the exact content. This lets the system send the text to the model while also recording a stable identifier for debugging and auditing.

**Data flow**: It receives a finished prompt string. It turns that text into bytes, computes a SHA-256 hash from it, prefixes the hash with "sha256:", and returns a RenderedPrompt object containing both the digest and the original content.

**Call relations**: This is the final packaging step used by render_template. After render_template has filled and checked the prompt, it calls rendered_prompt so the completed text leaves this file with its tracking fingerprint attached.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 65–82)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: Builds the main agent’s system prompt from the project’s standard shell template. It also converts the model’s machine-style knowledge cutoff, such as "2026-02", into readable text, such as "February 2026".

**Data flow**: It receives the agent’s prompt text, contributed sections, optional skills, and a required knowledge cutoff date. It parses the cutoff date, inserts the readable version into the knowledge-cutoff block, places that block into the shell template, and passes the result onward to render_template. It returns the final RenderedPrompt.

**Call relations**: This is the higher-level entry for building the normal agent prompt. It prepares the special knowledge-cutoff piece first, then hands the actual slot filling and validation to render_template.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 85–103)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: Fills a prompt template with all of its major pieces and refuses to return a prompt if any placeholder is left unresolved. This is the main guardrail that prevents half-rendered instructions from reaching the model.

**Data flow**: It receives a template, agent prompt text, variable values for that agent prompt, a skill list, and section bodies. First it substitutes variables inside the agent prompt. Then it checks that a non-empty agent prompt has a place to go. Next it replaces the skill, citation, section, and agent-prompt slots. It looks for any remaining {{...}} placeholders, raises an error if it finds any, cleans up long blank-line runs, trims the end, and returns a RenderedPrompt.

**Call relations**: render_system_prompt calls this after preparing the shell template. Inside, render_template asks _substitute_vars to safely fill agent-prompt variables, asks render_skill_index to format the available skills block, and finally asks rendered_prompt to attach the digest to the completed text.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_workspace_facts`  (lines 115–128)

```
def render_workspace_facts(lines: Sequence[str]) -> str
```

**Purpose**: Formats a short block telling the model which workspace capabilities are already set up. This helps stop the model from offering setup steps for things the workspace already has.

**Data flow**: It receives a list of capability lines. If the list is empty, it returns an empty string. Otherwise, it wraps the lines in a named workspace-capabilities block and adds one shared closing instruction: "Already set up — do not offer again."

**Call relations**: This helper stands on its own in this file. Other prompt-building code can use it to create a ready-to-insert section, avoiding repeated wording from every extension that contributes a capability line.


##### `render_skill_index`  (lines 131–140)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: Turns the available skills list into a simple prompt block the model can read. If there are no skills, it produces no block at all.

**Data flow**: It receives pairs of skill name and description. With no skills, it returns an empty string. With skills, it creates an <available_skills> block where each skill appears as a bullet with its description, then returns that text.

**Call relations**: render_template calls this when it reaches the skill slot in the template. The formatted block is inserted into the final prompt alongside the agent instructions, citation rules, and contributed sections.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 143–150)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: Safely fills small named variables inside the agent prompt. It is strict on purpose: missing variables and extra variables are both treated as mistakes.

**Data flow**: It receives prompt text that may contain {{variable_name}} placeholders and a mapping of variable names to replacement text. It scans the prompt to find declared variables, compares them with the supplied names, raises an error for anything missing or unexpected, and then replaces each placeholder with its matching value.

**Call relations**: render_template calls this before inserting the agent prompt into the larger shell. By doing this early, render_template can be sure the agent’s own instructions are complete before it checks the whole final prompt for unresolved slots.

*Call graph*: called by 1 (render_template).


### `core/src/ufo/runtime/turns/delivery_register.py`

`config` · `startup and prompt construction`

This file is like a house style card that every agent carries. The project has a separate Markdown document, `delivery_register.md`, that explains the accepted “registers,” meaning the allowed tone and format for delivered text. This Python file reads that document once and exposes it as `DELIVERY_REGISTER_BLOCK`, so other parts of the system can insert the same rules into prompts instead of copying them by hand.

It also sets two size limits. `DIRECT_PROSE_RESULT_MAX_CHARS` caps short direct prose results, and `SUBAGENT_RESULT_MAX_WORDS` caps what a subagent is allowed to send back to its parent. These limits help keep internal agent-to-agent messages focused and prevent a helper agent from dumping a long answer where only a brief handoff is wanted.

The longer `SUBAGENT_RESULT_DESCRIPTION` is instruction text for a subagent’s final visible result. It tells the subagent to choose the right writing style from the shared delivery register, call `finish` when done, avoid writing the result twice, and use a file path rather than restating a full artifact when an artifact is required. Without this file, different agents could drift into inconsistent wording, overly long handoffs, or duplicate final messages.


### Tool and Action Exposure
Defines safe model-facing descriptions for actions and validates the runtime tools available during a turn.

### `core/src/ufo/runtime/object_views.py`

`domain_logic` · `model discovery and portal/request handling`

The system has actions attached to objects, but the outside world should not see the raw internal objects directly. This file acts like a display card maker: it takes a real action and produces an `ActionView`, a frozen data record that says what the action is called, what it does, what input shape it expects, and a pre-filled call template for invoking it later.

This matters because model discovery and portal controls need the same clean view of actions. Without this layer, each caller would need to know the internal action layout, repeat filtering rules, and risk exposing actions that should stay hidden.

The file also separates ordinary actions from actions meant to be presented as portal controls. A “presented” action is one with presentation information, such as a label or confirmation message, and it must not be marked as profile-only. The helper functions use that rule to build ordered lists of visible controls for a target object.

Finally, the file computes which action IDs may be called from an embedded app page, also known as a frame. It combines suitable global tools with suitable object-bound actions and returns a sorted, duplicate-free list. In short, this file is the translator between internal action wiring and the carefully limited action menu exposed to models, portals, and frames.

#### Function details

##### `action_view`  (lines 27–53)

```
def action_view(kind: str, bound: 'BoundAction', *, name: str | None=None, agent: str | None=None, generation: UUID | None=None, presented: bool=False) -> ActionView
```

**Purpose**: Builds an `ActionView`, which is a plain description of an action plus a ready-to-use call template. Someone uses it when an internal bound action needs to be shown or offered safely to a model or portal.

**Data flow**: It receives the kind of target, a bound action, and optional details such as object name, agent, generation ID, and whether portal presentation details should be included. It copies the action name, description, input schema, and builds a call dictionary with the action target already filled in and an empty input area. It returns an immutable `ActionView` containing that public-facing action card.

**Call relations**: This is the construction step used by `presented_action_views`. After `presented_action_views` has filtered down to actions that are allowed to appear, it calls `action_view` to turn each chosen internal action into the clean view that other parts of the system can consume.

*Call graph*: called by 1 (presented_action_views); 1 external calls (__init__).


##### `presented`  (lines 56–58)

```
def presented(bound: 'BoundAction') -> bool
```

**Purpose**: Answers whether an action should be treated as a portal-visible control. It only says yes when the action has presentation information and is not limited to profile-only use.

**Data flow**: It receives a bound action and reads two pieces of information from the underlying action: whether presentation settings exist, and whether the action is marked profile-only. It returns `true` if the action can be presented as a control, otherwise `false`; it does not change anything.

**Call relations**: This is the shared gatekeeper for visibility. `presented_action_views` uses it before building portal action views, and `frame_admissible_ids` uses it before allowing object-bound actions to be called from an embedded frame.

*Call graph*: called by 2 (frame_admissible_ids, presented_action_views).


##### `presented_action_views`  (lines 61–77)

```
def presented_action_views(actions: 'Mapping[str, Mapping[str, BoundAction]]', kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Builds the list of portal-presentable action views for one target object or action kind. It applies the rules for the requested binding and optional object name, then returns the matching actions in a predictable order.

**Data flow**: It receives all known actions grouped by kind, the kind to look at, the required binding type, and optional target details such as name and generation ID. It looks only at actions for that kind, sorts them by their short name, rejects actions that are not properly bound, bound to the wrong place, bound to a different name, or not presentable, and converts the survivors with `action_view`. It returns a tuple of `ActionView` records ready for display or discovery.

**Call relations**: This function sits between the raw action registry and the portal/model-facing view. It relies on `presented` to enforce the visibility rule, then hands each accepted action to `action_view` so the rest of the system receives uniform, pre-bound action descriptions.

*Call graph*: calls 2 internal fn (action_view, presented).


##### `frame_admissible_ids`  (lines 80–97)

```
def frame_admissible_ids(tools: 'Iterable[ToolDef]', actions: 'Mapping[str, Mapping[str, BoundAction]]') -> tuple[str, ...]
```

**Purpose**: Creates the list of action IDs that an embedded app page is allowed to call. This is a safety and routing helper: it names only actions explicitly marked as usable from a frame.

**Data flow**: It receives global tools and object-bound actions. From the tools, it keeps only unbound tools that have presentation settings marked for frame use. From the object actions, it keeps only presented actions whose presentation also allows frame use, then takes their canonical IDs. It removes duplicates, sorts the result, and returns the allowed IDs as a tuple of strings.

**Call relations**: This function uses `presented` as its first visibility check for object-bound actions, then adds the extra rule that the action must be frame-enabled. It brings together both global tools and object-specific actions so embedded pages get one clear allow-list rather than needing to inspect all runtime action data themselves.

*Call graph*: calls 1 internal fn (presented).


### `core/src/ufo/runtime/tools/registry.py`

`domain_logic` · `startup validation and tool dispatch`

The system lets the model call named tools, such as reading data, acting on an object, or asking a final question. This file describes those tools in a structured way so the rest of the runtime can trust what each tool means. A `ToolDef` is like a labeled appliance in a workshop: it has a name, instructions, an input form, and the function that actually runs it. It also carries safety labels, such as whether its output may contain untrusted outside text, whether it changes the outside world, and whether calls can safely run at the same time.

Some tools are global, but others are actions attached to objects, such as an action for one visible item. Those bound object actions get a special canonical identity like `action:<kind>:<name>` and are deliberately kept out of the normal wire registry. This avoids confusing ordinary tool names with object-specific actions.

`ToolRegistry` is the frozen catalog used by the engine when dispatching tool calls. When it is created, it checks for duplicate names, forbidden prefixes, reserved input fields, invalid object bindings, and invalid presentation or final-act declarations. Without these checks, the model could be shown misleading tool schemas, dispatch could pick the wrong callable, or a dangerous action could be exposed through the wrong route.

#### Function details

##### `ToolDef.canonical_id`  (lines 95–100)

```
def canonical_id(self) -> str
```

**Purpose**: Gives a tool its stable system-wide identity. A normal tool is identified by its name, while an object-bound action is identified with an `action:` prefix that includes the object kind.

**Data flow**: It reads the tool definition, especially its name and optional object binding. If there is no binding, it returns the plain name. If there is a binding, it builds and returns a string in the form `action:<kind>:<tool name>`.

**Call relations**: Other parts of the runtime can use this identity for allowlists, logging, idempotency, and object-action dispatch. It keeps global tools and object actions in separate name spaces so they do not accidentally collide.


##### `ToolDef.schema`  (lines 102–115)

```
def schema(self, *, include_requested_by: bool=True) -> ToolSchema
```

**Purpose**: Builds the tool description that can be sent over the wire to the model client. This tells the client the tool name, what it does, and what input shape it expects.

**Data flow**: It starts with the Pydantic input model, which can produce a JSON schema, meaning a machine-readable description of expected fields. If requested, it adds a reserved `requested_by` field used to tie a call to the message that explicitly authorized it. It then packages the name, description, and input schema into a `ToolSchema` object.

**Call relations**: This function calls `ToolSchema.__init__` to create the final schema object. `ToolRegistry.schemas` relies on each tool’s `schema` method when it needs to expose the registered tools as client-facing tool definitions.

*Call graph*: 1 external calls (__init__).


##### `validate_tool_declaration`  (lines 118–142)

```
def validate_tool_declaration(tool: ToolDef[Any], label: str) -> None
```

**Purpose**: Checks that one tool declaration follows the system’s safety and consistency rules. It catches bad tool definitions early, before they can be shown to the model or used by dispatch.

**Data flow**: It receives a `ToolDef` and a human-readable label for error messages. It inspects optional presentation settings, object binding settings, and final-act settings. If something is inconsistent, such as an empty button label, a collection action pinned to a single item, or a final-act model the terminal frame cannot carry, it raises a `ValueError`; otherwise it changes nothing and returns nothing.

**Call relations**: ToolRegistry.__post_init__ calls this for every tool after doing registry-wide checks. This makes per-tool validation part of registry construction, so invalid declarations fail during setup rather than later during a model call.

*Call graph*: called by 1 (__post_init__).


##### `ToolRegistry.__post_init__`  (lines 149–169)

```
def __post_init__(self) -> None
```

**Purpose**: Runs the registry’s startup gatekeeping after the frozen `ToolRegistry` object is created. It refuses tool catalogs that would be ambiguous, unsafe, or routed through the wrong path.

**Data flow**: It reads the tuple of tools in the registry. It looks for duplicate names, bound object actions that were mistakenly placed in the wire registry, names using the reserved `action:` prefix, and input models that define the reserved `requested_by` field themselves. If any problem is found, it raises a clear `ValueError`; if the broad checks pass, it validates each individual tool declaration.

**Call relations**: This method calls `validate_tool_declaration` for each tool. Because it runs as part of dataclass construction, any code that creates a `ToolRegistry` automatically gets these checks before the registry can be used for schemas or lookups.

*Call graph*: calls 1 internal fn (validate_tool_declaration).


##### `ToolRegistry.schemas`  (lines 171–172)

```
def schemas(self, *, include_requested_by: bool=True) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the wire-ready schemas for every tool in the registry. This is how the registered catalog is turned into the form a model client can understand.

**Data flow**: It reads the registry’s tuple of tool definitions and the `include_requested_by` option. For each tool, it asks the tool to build its schema using the same option, then returns all those schemas as an immutable tuple.

**Call relations**: This function sits between the frozen registry and whatever part of the engine needs to advertise tools to the model client. It delegates the details of each schema to `ToolDef.schema`, keeping the registry focused on collecting the results.


##### `ToolRegistry.get`  (lines 174–178)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the registered tool definition for a given tool name. The dispatch path can use it when a model asks to call a named tool.

**Data flow**: It receives a name string and scans the registry’s tool tuple. If it finds a tool with that exact name, it returns the full `ToolDef`, including its input model, safety flags, and handler. If no tool matches, it raises a `KeyError` saying the tool is unknown.

**Call relations**: This is the registry’s lookup doorway for normal global tools. It depends on `ToolRegistry.__post_init__` having already rejected duplicate names, so a successful lookup can return one clear tool definition instead of choosing between conflicting entries.


### Skill Runtime and User Skills
Defines the skill system, persists user-created workspace skills, and selects saved skills for prompt inclusion.

### `core/src/ufo/runtime/skills/__init__.py`

`other` · `import time`

This is an empty `__init__.py` file. In Python, a file with this name tells the interpreter that the surrounding folder should be treated as a package, meaning its contents can be imported by name from elsewhere in the program. You can think of it like a label on a drawer: the drawer may contain many useful tools, and the label lets the rest of the system find that drawer reliably.

Because this file has no code, it does not run any setup, create any objects, or change program state. Its value is structural. Without it, depending on the Python version and packaging setup, imports involving `ufo.runtime.skills` might fail or behave less predictably. Keeping the file also makes the project layout clear to humans: `skills` is intended to be a named part of the runtime system.


### `core/src/ufo/runtime/skills/runtime.py`

`domain_logic` · `startup and skill loading during agent turns`

A skill is a small bundle of guidance for the agent: a folder with a `SKILL.md` file, plus optional extra files. The `SKILL.md` starts with YAML frontmatter, which is structured metadata such as the skill name, description, dependencies, and target agents, followed by the actual instructions the agent should read.

This file is the “library desk” for those bundles. It reads skill folders from disk or memory, checks that they are shaped correctly, and turns them into `RuntimeSkill` objects. It also builds a registry of all skills the system can load: built-in deploy skills, pack-provided skills, generated skills, and user/member-saved skills. When the agent asks for a skill, the registry expands that request to include any declared dependencies, like gathering a recipe plus the tools it says it needs.

The file also prepares loaded skills for two audiences. For the agent, it creates a prompt section containing each skill’s instructions and a compact tree of the files made available. For the sandbox, it packages the skill files so they appear under `$UFO_HOME/skills/<name>/`. It carefully tracks which skill instructions are already in the conversation context, so repeated loads do not waste space by printing the same workflow again.

#### Function details

##### `skill_root`  (lines 51–53)

```
def skill_root(name: str) -> str
```

**Purpose**: Builds the stable runtime path where a named skill should appear inside the agent’s environment. It gives the rest of the system one consistent place to refer to skill files.

**Data flow**: It receives a skill name, joins it onto the fixed skills root `$UFO_HOME/skills`, and returns that path as text.

**Call relations**: When a `RuntimeSkill` needs to report its root folder, `RuntimeSkill.root` calls this helper so every skill path is formed the same way.

*Call graph*: called by 1 (root).


##### `RuntimeSkill.all_files`  (lines 89–90)

```
def all_files(self) -> dict[str, bytes]
```

**Purpose**: Returns every file that belongs to a skill, including its original `SKILL.md`. This is used whenever the system needs the complete skill package, not just the instructions.

**Data flow**: It reads the skill’s stored raw `SKILL.md` text and its asset files, then returns a dictionary from file path to file bytes.

**Call relations**: Digesting and sandbox transfer both need the full file set. `RuntimeSkill.content_digest` uses it to fingerprint the skill, and `_wire_skill` uses it to prepare files for loading.

*Call graph*: called by 2 (content_digest, _wire_skill).


##### `RuntimeSkill.root`  (lines 92–93)

```
def root(self) -> str
```

**Purpose**: Reports where this skill should live inside `$UFO_HOME/skills`. Other code uses this path when preparing safe file locations.

**Data flow**: It reads the skill’s name, passes that name to `skill_root`, and returns the resulting path string.

**Call relations**: It is called by `_wire_skill` while turning a skill into the format expected by the sandbox loader.

*Call graph*: calls 1 internal fn (skill_root); called by 1 (_wire_skill).


##### `RuntimeSkill.card`  (lines 95–103)

```
def card(self) -> SkillCard
```

**Purpose**: Creates the lightweight “routing card” for a skill. The card contains enough information to choose and expand skills without carrying the full instruction body.

**Data flow**: It reads the skill’s name, description, dependencies, and target agents, then returns a `SkillCard` with those fields.

**Call relations**: Registries use these cards when listing or resolving deploy skills, so dependency lookup can happen without rereading full skill content.

*Call graph*: 1 external calls (__init__).


##### `RuntimeSkill.content_digest`  (lines 105–111)

```
def content_digest(self) -> str
```

**Purpose**: Creates a stable fingerprint for a skill’s full contents. This lets caches and sandboxes know whether two skill bundles are exactly the same.

**Data flow**: It gathers all files, sorts them by path, hashes each path and each file’s bytes with SHA-256, then returns a `sha256:` digest string.

**Call relations**: _wire_skill` uses this digest when sending a skill to the sandbox, and system bundles use the same idea to identify immutable skill archives.

*Call graph*: calls 1 internal fn (all_files); called by 1 (_wire_skill); 1 external calls (sha256).


##### `SystemSkillBundle.from_skills`  (lines 123–148)

```
def from_skills(cls, skills: Iterable[RuntimeSkill]) -> 'SystemSkillBundle'
```

**Purpose**: Builds a deterministic ZIP archive for the deploy-time system skills. Deterministic means the same skills produce the same bytes and the same cache identity.

**Data flow**: It receives runtime skills, checks that duplicate names do not hide different content, creates a manifest describing each skill and file, writes all files into a ZIP archive, and returns a bundle containing the digest, archive bytes, and manifest bytes.

**Call relations**: Startup and serving code call this when preparing shared system skills for the runtime, terminal cache, or sandbox image.

*Call graph*: called by 4 (init_runtime, _mount_shared_surfaces, run, system_skill_bundle); 4 external calls (sha256, BytesIO, dumps, ZipFile).


##### `SystemSkillBundle._write`  (lines 151–154)

```
def _write(archive: zipfile.ZipFile, path: str, content: bytes) -> None
```

**Purpose**: Writes one file into the system-skill ZIP archive with fixed metadata. Fixed timestamps and permissions help keep the archive reproducible.

**Data flow**: It receives a ZIP archive, a path, and bytes, creates a ZIP entry with a fixed date and file mode, and writes the content into the archive.

**Call relations**: It is the low-level helper used by `SystemSkillBundle.from_skills` for the manifest and every bundled skill file.

*Call graph*: 2 external calls (writestr, ZipInfo).


##### `LoadedSkill.prompt_body`  (lines 167–177)

```
def prompt_body(self) -> str
```

**Purpose**: Creates the text block that one loaded skill contributes to the agent’s context. It makes clear whether the agent directly asked for the skill or it arrived as a dependency.

**Data flow**: It reads the loaded skill name, instructions, and optional dependency source, then returns a markdown header followed by the skill instructions.

**Call relations**: `loaded_context` relies on this method when building the final prompt text shown to the agent.


##### `LoadedSkills.reseed`  (lines 203–222)

```
def reseed(self, loads: Iterable[tuple[LoadedRef, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the tracker of which skill instructions are already in the model’s context. This prevents the system from repeating the same skill text across turns or after transcript changes.

**Data flow**: It receives previously resolved loads and optionally preloaded skills, clears the old tracker, records every skill currently in context, and separately records which ones the agent directly asked for.

**Call relations**: It starts by calling `LoadedSkills.reset`. Later, `loaded_context` can use this tracked state to suppress repeated instruction blocks.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 224–229)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the skills the agent directly asked for and then clears the tracker. This is useful at a boundary where old skill bodies may be dropped but the system wants to remember what should be reloadable.

**Data flow**: It sorts the `asked_for` names, clears both tracking sets, and returns the saved names as a tuple.

**Call relations**: It calls `LoadedSkills.reset` after taking the snapshot, so the next context-tracking phase starts fresh.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 231–233)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered loaded-skill state. It is the shared cleanup step for reseeding or draining the tracker.

**Data flow**: It empties the set of skills in context and the set of skills directly requested by the agent. It returns nothing.

**Call relations**: `LoadedSkills.reseed` calls it before rebuilding state, and `LoadedSkills.drain` calls it after extracting requested skill names.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 236–242)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates the structured metadata at the top of `SKILL.md` from the instruction body below it. It also enforces that the file really starts and ends its metadata block correctly.

**Data flow**: It receives the full text of `SKILL.md`, checks for the opening `---` fence, finds the closing fence, and returns the metadata text and body text. If the fences are missing, it raises an error.

**Call relations**: `parse_skill_content` calls this before reading the YAML metadata and building a `RuntimeSkill`.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 245–250)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate child folders that are themselves skills. A child skill is identified by having its own `SKILL.md` file.

**Data flow**: It receives a directory path, scans its direct children, keeps only directories containing `SKILL.md`, sorts them, and returns the list.

**Call relations**: `parse_skill` uses this to keep child skill files out of the parent’s asset bundle, and `discover_skills` uses it to recurse into nested skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 253–290)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory set of files into a validated `RuntimeSkill`. This is used when skill files come from storage or another source instead of directly from disk.

**Data flow**: It receives a directory name, file bytes, and optional registry naming details. It reads `SKILL.md`, splits metadata from body, parses YAML, checks that the skill name matches the folder, validates agent targeting, gathers asset files, and returns a `RuntimeSkill`.

**Call relations**: `parse_skill` calls this after reading files from disk. It delegates frontmatter splitting to `_split_frontmatter`.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 293–302)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill directory from disk and turns it into a `RuntimeSkill`. It treats nested child-skill folders as separate skills rather than as ordinary parent assets.

**Data flow**: It receives a filesystem path, finds immediate child skill folders, reads all regular files except those inside child skill folders, and passes the collected bytes to `parse_skill_content`.

**Call relations**: `discover_skills` calls this for each skill directory it visits while building a flattened map of skills.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 305–323)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, returning them in one name-to-skill map. Child skills get path-like names such as `parent/child`.

**Data flow**: It receives a skill directory and optional parent naming details, parses the current skill, then scans child skill directories and recursively discovers each one.

**Call relations**: `_load_core_skills` calls this while collecting built-in skills from the source tree. It uses `parse_skill` for the current directory and `_child_skill_dirs` to find children.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 326–333)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the built-in core skills that ship with the project. These are the baseline skills available before packs or member-saved skills are added.

**Data flow**: It receives a root directory, scans visible child directories, discovers skills inside each one, and returns a dictionary keyed by skill name.

**Call relations**: This function is used at module import time to populate the core skill registry.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.__post_init__`  (lines 360–362)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the default set of bundled deploy skills after the registry is created. If no explicit bundle list is given, all deploy skills are considered bundled.

**Data flow**: It checks whether `bundled_names` is missing. If so, it sets it to the names currently present in `by_name`.

**Call relations**: This runs automatically when a `SkillRegistry` is constructed, including the core registry and registries made by merge methods.


##### `SkillRegistry.named`  (lines 364–368)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a deploy skill by exact name. If the name is unknown, it raises a helpful error instead of silently failing.

**Data flow**: It receives a name, tries to return the matching `RuntimeSkill` from the deploy-skill dictionary, and on failure asks `_unknown` to build an explanatory error.

**Call relations**: Callers use this when they need an actual deploy skill object rather than just a routing card.

*Call graph*: calls 1 internal fn (_unknown).


##### `SkillRegistry._unknown`  (lines 370–373)

```
def _unknown(self, name: str) -> ValueError
```

**Purpose**: Creates a clear error message for an unknown skill name, including close matches when possible. This helps users recover from typos.

**Data flow**: It receives a missing name, compares it with all known names, builds a short suggestion hint, and returns a `ValueError`.

**Call relations**: `SkillRegistry.named` and `SkillRegistry._card` use this whenever lookup fails.

*Call graph*: calls 1 internal fn (known_names); called by 2 (_card, named); 1 external calls (get_close_matches).


##### `SkillRegistry._card`  (lines 375–382)

```
def _card(self, name: str) -> SkillCard
```

**Purpose**: Finds the routing card for a skill, whether it is a deploy skill or a member-saved skill. A routing card is enough to resolve dependencies without loading full content.

**Data flow**: It receives a name, first checks deploy skills, then member cards, and returns the matching card. If neither tier contains the name, it raises the unknown-skill error.

**Call relations**: `SkillRegistry.closure` and its inner dependency walk call this whenever they need to expand a requested skill or dependency.

*Call graph*: calls 1 internal fn (_unknown); called by 2 (closure, add).


##### `SkillRegistry.known_names`  (lines 384–387)

```
def known_names(self) -> frozenset[str]
```

**Purpose**: Returns every skill name this registry can resolve. This combines deploy skills and member-saved skill cards.

**Data flow**: It reads the deploy-skill names and member-card names, combines them into one frozen set, and returns it.

**Call relations**: `SkillRegistry._unknown` uses this list to suggest close matches for a missing name.

*Call graph*: called by 1 (_unknown).


##### `SkillRegistry.all_cards`  (lines 389–394)

```
def all_cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: Returns routing cards for all loadable skills. Search and selection code can use these compact cards without reading every full skill body.

**Data flow**: It turns each deploy skill into a card, appends the stored member cards, and returns them as a tuple.

**Call relations**: This provides the registry-wide view used by skill search and selection flows.


##### `SkillRegistry.bundled_skills`  (lines 396–399)

```
def bundled_skills(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Returns the deploy skills that are included in the static terminal archive and sandbox image. These are the skills the sandbox can refer to by digest instead of receiving full file bytes every time.

**Data flow**: It reads the bundled-name set, filters the deploy skills to those names, and returns the matching `RuntimeSkill` objects.

**Call relations**: Serving setup calls this when mounting shared skill surfaces for the runtime.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.closure`  (lines 401–425)

```
def closure(self, *names: str) -> tuple[LoadedRef, ...]
```

**Purpose**: Expands requested skill names into the full set that must be loaded, including dependencies. It keeps the requested skills first and avoids loading any skill twice.

**Data flow**: It receives one or more skill names, creates direct `LoadedRef` entries for them, walks each skill’s dependency list, records who pulled each dependency, and returns the ordered tuple of references.

**Call relations**: The runtime engine calls this when determining what a load request really means. It uses `_card` for lookups and its inner `add` helper for dependency recursion.

*Call graph*: calls 1 internal fn (_card); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 415–420)

```
def add(card: SkillCard, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency and its own dependencies to a closure walk. It is careful not to revisit names already seen, which also prevents dependency cycles from causing endless recursion.

**Data flow**: It receives a skill card and the name of the skill that depended on it, skips it if already recorded, otherwise stores a `LoadedRef` and recursively processes its dependencies.

**Call relations**: This helper lives inside `SkillRegistry.closure` and is called while expanding each requested skill’s dependency chain.

*Call graph*: calls 1 internal fn (_card); 1 external calls (__init__).


##### `SkillRegistry.materialize`  (lines 427–449)

```
async def materialize(self, refs: Sequence[LoadedRef]) -> tuple[LoadedSkill, ...]
```

**Purpose**: Turns resolved skill references into full loaded skills with instruction bodies and files. This is the point where member-saved skills are actually read.

**Data flow**: It receives a sequence of `LoadedRef` objects, looks up deploy skills directly, asks the async materializer for member skills when needed, checks that returned names match, and returns `LoadedSkill` objects.

**Call relations**: It follows `SkillRegistry.closure`: closure decides what names are needed, then materialize fetches the real skill contents for loading.

*Call graph*: 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 451–460)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill index shown in the system prompt. It includes only top-level deploy skills, so the prompt stays stable and member-saved content does not alter it.

**Data flow**: It scans deploy skills in registration order, keeps only those without a parent, and returns pairs of skill name and description.

**Call relations**: Prompt-building code calls this to fill the `skill_index` area that tells the agent what built-in skills are available.

*Call graph*: called by 2 (_prompt_skill_index, prompt_index).


##### `SkillRegistry.merged_with`  (lines 462–483)

```
def merged_with(self, generated: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Returns a new registry with generated deploy-controlled skills added. It refuses name shadowing so existing deploy skills keep their meaning.

**Data flow**: It copies deploy skills, appends generated skills unless their names already exist, logs refused collisions, removes member cards that now collide with deploy names, and returns a new `SkillRegistry`.

**Call relations**: Turn setup can use this when generated skills, such as spawn or setup skills, need to join the base registry.

*Call graph*: 2 external calls (__init__, log).


##### `SkillRegistry.with_member`  (lines 485–504)

```
def with_member(self, cards: Sequence[SkillCard], materialize: SkillMaterializer) -> 'SkillRegistry'
```

**Purpose**: Returns a new registry that includes a bound agent’s saved member skills. Member skills are allowed to add choices but not replace deploy skills.

**Data flow**: It receives member skill cards and a materializer function, drops any member card whose name collides with a deploy skill while logging that refusal, and returns a registry with the remaining member cards.

**Call relations**: This is used when a turn is prepared for a specific agent, so that agent’s saved skills can be searched and loaded.

*Call graph*: 2 external calls (__init__, log).


##### `_loaded_tree`  (lines 510–528)

```
def _loaded_tree(loaded: Sequence[LoadedSkill]) -> str
```

**Purpose**: Creates a compact text tree of all files made available by a skill load. This shows the agent where files are located without printing every full path repeatedly.

**Data flow**: It receives loaded skills, gathers every file path under each skill name, sorts them, builds an indented directory tree under `$UFO_HOME/skills/`, and returns it as text.

**Call relations**: `loaded_context` calls this at the end of the prompt text so the agent can see the loaded file layout.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 531–544)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the full text that a skill load contributes to the agent’s context. It includes new skill instructions, notes any already-present instructions that were not repeated, and lists loaded files.

**Data flow**: It receives loaded skills and an optional set of names already in context. It collects prompt bodies for new skills, adds a short note for repeated ones, appends the loaded file tree, and returns one combined string.

**Call relations**: Both normal `load_skill` behavior and subagent preloading use this, so skills read the same way whether loaded by a tool result or preloaded into a prompt.

*Call graph*: calls 1 internal fn (_loaded_tree).


##### `_wire_skill`  (lines 547–554)

```
def _wire_skill(skill: RuntimeSkill) -> dict[str, object]
```

**Purpose**: Converts a runtime skill into the wire format expected by the sandbox loader. It safely names each file and base64-encodes its bytes so they can travel as text.

**Data flow**: It receives a `RuntimeSkill`, gathers all files, checks and normalizes each path relative to the skill root, encodes file bytes with URL-safe base64, adds the content digest, and returns a dictionary.

**Call relations**: `install_skill` uses it for one skill, and `load_skills` uses it for non-bundled loaded skills before calling the sandbox.

*Call graph*: calls 3 internal fn (all_files, content_digest, root); called by 2 (install_skill, load_skills); 2 external calls (urlsafe_b64encode, contained_relative).


##### `install_skill`  (lines 557–561)

```
async def install_skill(sandbox: Sandbox, skill: RuntimeSkill) -> None
```

**Purpose**: Installs one materialized skill into the sandbox under the runtime skills directory. It is a focused helper for loading a single user-style skill package.

**Data flow**: It receives a sandbox session and a skill, converts the skill with `_wire_skill`, asks the sandbox to load it, and raises an error if the sandbox does not report a path for that skill.

**Call relations**: It hands off the actual file placement to `Sandbox.load_skills`, using `_wire_skill` to prepare the payload.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


##### `load_skills`  (lines 564–571)

```
async def load_skills(sandbox: Sandbox, loaded: Sequence[LoadedSkill]) -> None
```

**Purpose**: Installs a whole resolved set of loaded skills into the sandbox. Bundled deploy skills are referenced by digest, while non-bundled skills are sent with their file contents.

**Data flow**: It receives a sandbox and loaded skills, separates bundled entries from user/file-backed entries, wires non-bundled skills, calls the sandbox loader, and raises an error if any requested skill path is missing afterward.

**Call relations**: After a registry has resolved and materialized a skill load, this function performs the sandbox-side installation through `Sandbox.load_skills`.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and skill loading`

A “skill” here is a small bundle of files, usually including a main SKILL.md file, that teaches an agent how to do something. This file stores those bundles in the database, one row per skill name per workspace. Without it, user-authored skills would either disappear after a run or risk overwriting each other when two edits happen at the same time.

The file also protects the shared workspace. Skill names must be safe lowercase slugs, so a name cannot act like a file path or collide with system-owned skills. Saved file bytes are turned into base64 text so they can live safely inside a database text column. The content is parsed when saved, and the parsed description, dependencies, agent routing rules, and pinned state are stored beside it. That means the quick “card” used for choosing skills stays in step with the actual files.

A key idea is the “generation,” which works like a version stamp on a document. When a caller edits a skill, it must provide the generation it previously read. If someone else changed or deleted the skill first, the save is refused instead of silently replacing their work. The store also enforces workspace limits, including the total number of user skills and the number of pinned skills. Loading paths are deliberately different: loading one named skill fails loudly if its stored bundle is corrupt, while loading all skills skips bad rows with a warning so one broken skill does not hide the rest.

#### Function details

##### `_save_lock_key`  (lines 54–56)

```
def _save_lock_key(workspace_id: UUID) -> int
```

**Purpose**: Creates a stable numeric lock key from a workspace ID. The save path uses this key to ask PostgreSQL to serialize skill writes for the same workspace, so two writers cannot race past limits or version checks.

**Data flow**: It receives a workspace UUID, turns it into text, hashes that text with SHA-256, takes the first eight bytes of the hash, and converts them into a signed integer. The output is a repeatable number: the same workspace always gets the same lock key, while different workspaces are very unlikely to share one.

**Call relations**: UserSkillStore.save calls this before writing to the database. The returned key is handed to PostgreSQL’s advisory transaction lock when that database is in use, making the rest of the save behave like a one-at-a-time checkout counter for that workspace.

*Call graph*: called by 1 (save); 1 external calls (sha256).


##### `UserSkillStore.save`  (lines 123–238)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str], pinned: bool=False, generation: UUID | None=None) -> RuntimeSkill
```

**Purpose**: Validates and saves one user-created workspace skill. It protects against unsafe names, overwriting someone else’s edit, shadowing built-in skills, and exceeding workspace skill or pinned-skill limits.

**Data flow**: It receives a skill name, a map of file paths to file bytes, the names already owned by core or pack skills, a pinned flag, and optionally the generation previously read by the caller. It checks the name, parses the skill files, encodes the files as base64 JSON, computes a content digest, and prepares the database columns used for later listing and routing. Inside a transaction, it checks the current database row and compares its generation with the caller’s generation. If everything is allowed, it inserts a new row or updates the existing row with a fresh generation. It returns the parsed RuntimeSkill object and changes the database.

**Call relations**: This is the main write doorway for the store. It calls _save_lock_key to serialize concurrent saves on PostgreSQL, uses _count when a new skill may exceed the workspace cap, and uses _pinned_count when pinning might exceed the pinned limit. It also relies on parse_skill_content and StoredSkill so the saved database row and the runtime skill view are based on the same files.

*Call graph*: calls 3 internal fn (_count, _pinned_count, _save_lock_key); 16 external calls (__init__, __init__, __init__, __init__, __init__, __init__, b64encode, sha256, dumps, cast (+6 more)).


##### `UserSkillStore.cards`  (lines 240–281)

```
async def cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: Returns lightweight routing cards for all saved skills in the current workspace. These cards let the system know which skills exist and when they may be useful without loading every full file bundle.

**Data flow**: It reads the current workspace ID, queries the database for each skill’s name, description, dependencies, allowed agents, and pinned flag, and sorts by name. For each row with a real description, it decodes the JSON dependency and agent lists and builds a SkillCard. Rows with an empty description are skipped with a warning. The output is a tuple of SkillCard objects, and the database is not changed.

**Call relations**: This is used when the system needs the catalog-style view of workspace skills. Unlike record, files, or materialize, it does not read or parse the stored file content, so a corrupt stored bundle does not stop the card list from being produced.

*Call graph*: 4 external calls (__init__, loads, select, agent_current).


##### `UserSkillStore.listing`  (lines 283–307)

```
async def listing(self) -> tuple[SkillListing, ...]
```

**Purpose**: Returns the simple list shown to users or tools: each saved skill’s name, description, and whether it is pinned. It is the human-facing summary view of the workspace skill set.

**Data flow**: It reads the current workspace ID, queries the database for name, description, and pinned state, and orders the rows by name. It turns rows with non-empty descriptions into SkillListing objects and skips empty-description rows. The result is a tuple of listings, with no database changes.

**Call relations**: This sits beside cards as another lightweight read path. cards prepares routing information for the agent, while listing prepares display information for object-list style views.

*Call graph*: 3 external calls (__init__, select, agent_current).


##### `UserSkillStore.record`  (lines 309–340)

```
async def record(self, name: str) -> SkillRecord | None
```

**Purpose**: Loads the full stored record for one named skill, including its files, version generation, pin state, and timestamps. This is the detailed read used before viewing or editing a specific skill.

**Data flow**: It receives a skill name and reads the current workspace ID. It queries the matching database row for stored content and metadata. If no row exists, it returns None. If a row exists, it validates the stored JSON, base64-decodes each file back into bytes, and returns a SkillRecord containing those files plus description, generation, pinned state, created time, and updated time.

**Call relations**: This is the full-detail counterpart to listing. Its generation value is especially important because UserSkillStore.save expects callers to pass that generation back when editing, which prevents silent overwrites.

*Call graph*: 4 external calls (__init__, b64decode, select, agent_current).


##### `UserSkillStore.materialize`  (lines 342–349)

```
async def materialize(self, name: str) -> RuntimeSkill | None
```

**Purpose**: Turns one saved skill back into a RuntimeSkill object that the agent can actually use. If the skill is not saved in this workspace, it returns None.

**Data flow**: It receives a skill name, asks files for that skill’s stored file bytes, and stops with None if no files are found. If files are found, it parses them with parse_skill_content and returns the resulting RuntimeSkill. It does not change the database.

**Call relations**: This function builds on UserSkillStore.files instead of repeating the database read. It is the named-skill load path, so corrupt content is allowed to raise an error rather than being treated as a missing skill.

*Call graph*: calls 1 internal fn (files); 1 external calls (parse_skill_content).


##### `UserSkillStore.materialize_all`  (lines 351–379)

```
async def materialize_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved skill in the current workspace as RuntimeSkill objects. It is designed to be tolerant: one broken stored skill is logged and skipped instead of stopping all the others from loading.

**Data flow**: It reads the current workspace ID, queries every saved skill’s name and stored content, and sorts by name. For each row, it validates the stored JSON, base64-decodes the files, and parses the files into a RuntimeSkill. If any row cannot be decoded or parsed, it logs a warning and continues. The output is a tuple of successfully loaded RuntimeSkill objects, with no database changes.

**Call relations**: This is the bulk load path used when the workspace’s saved skills need to be made available together. It differs from materialize: named loads fail loudly on corruption, while this all-skills path keeps going so one bad bundle does not remove the rest of the workspace’s skills.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 381–396)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: Returns the raw files for one saved skill as bytes. This is useful when another part of the system wants the stored bundle itself rather than the parsed RuntimeSkill or display metadata.

**Data flow**: It receives a skill name and reads the current workspace ID. It queries the database for the stored content for that name. If no row exists, it returns None. If a row exists, it validates the stored JSON and base64-decodes each saved file into bytes, returning a dictionary from relative path to file bytes.

**Call relations**: UserSkillStore.materialize calls this first, then parses the returned files into a runtime skill. record performs a similar decode but also returns metadata such as generation and timestamps.

*Call graph*: called by 1 (materialize); 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 398–420)

```
async def delete(self, name: str) -> None
```

**Purpose**: Deletes one saved skill from the workspace and cleans up its search index entries if an index is available. It carefully orders the steps so a crash does not leave behind unowned indexed chunks.

**Data flow**: It receives a skill name and reads the current workspace ID. If an index exists, it first marks the skill’s indexed digest as missing in the database, then asks the index to delete entries for that skill’s scope. After that, it deletes the database row for the workspace and name. The result is no returned value, but the database and possibly the index are changed.

**Call relations**: This is the removal path for saved skills. It uses IndexScope to tell the indexing system exactly which skill-owned entries to prune, then removes the stored row so future listing, loading, and card reads no longer see the skill.

*Call graph*: 4 external calls (__init__, delete, update, agent_current).


##### `UserSkillStore._count`  (lines 422–430)

```
async def _count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many user skills are currently saved in the workspace. It is a helper used to enforce the maximum number of saved user skills.

**Data flow**: It receives an open async database connection and reads the current workspace ID. It runs a count query over the user_skill table for that workspace and returns the number as an integer. It does not change the database.

**Call relations**: UserSkillStore.save calls this only when creating a new skill. If the count is already at the workspace limit, save refuses the insert with TooManyUserSkills.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


##### `UserSkillStore._pinned_count`  (lines 432–444)

```
async def _pinned_count(self, connection: AsyncConnection, excluding: str) -> int
```

**Purpose**: Counts the currently pinned user skills in the workspace, excluding one named skill. This lets the save path decide whether adding a new pin would exceed the pinned-skill limit.

**Data flow**: It receives an open async database connection and the skill name to exclude. It reads the current workspace ID, counts rows in that workspace where pinned is true and the name is not the excluded name, and returns that count. It does not change the database.

**Call relations**: UserSkillStore.save calls this when the requested save would pin a skill that was not already pinned. Excluding the current name means re-saving an already considered skill does not accidentally count itself as an extra pin.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


### `core/src/ufo/runtime/skills/selection.py`

`domain_logic` · `request handling`

Agents can use saved skills, but the model can only read a limited amount of text at once. This file is the rulebook for fitting those skill cards into that limited space. Think of it like packing a notice board: if there are only a few cards, pin all of them on the main board; if there are too many, make a separate section; if even that is too crowded, show the most relevant details and keep the rest as names so the agent still knows they exist.

The file works only with in-memory skill cards. It does no disk, network, or database work, so it is predictable and safe to run on every turn. Each skill can become a short line with its name and description. The code first checks whether all saved member skills are small enough to fold into the system prompt beside built-in skills. If not, it builds a `<saved_skills>` block for the current turn.

That block follows a clear order. Pinned skills, which are treated as especially important, come first. If the whole catalog fits, every skill gets a full line. If not, the file uses simple word matching against the current query to pick a small top set for full descriptions, while the remaining skills still appear by name. If the block is still too long, it removes items from the end and adds a note saying how many were omitted and that `skill_search` can find them.

#### Function details

##### `_query_terms`  (lines 35–42)

```
def _query_terms(query: str) -> tuple[str, ...]
```

**Purpose**: Turns a user query into a clean set of searchable words. It lowers the text, splits on punctuation or spaces, ignores very short words, removes duplicates, and caps how much query text is considered.

**Data flow**: It receives a query string. It looks only at the first fixed-size portion, breaks that text into terms, filters out terms shorter than the minimum length, and keeps the first occurrence of each term. It returns those terms as an ordered tuple.

**Call relations**: This is the shared preparation step for matching queries to skills. `lexical_score` uses it when scoring one card, and `select_top_k` uses it once before ranking many cards.

*Call graph*: called by 2 (lexical_score, select_top_k).


##### `_term_hits`  (lines 45–47)

```
def _term_hits(terms: Sequence[str], card: SkillCard) -> int
```

**Purpose**: Counts how many prepared query words appear in a skill card's name or description. This gives a simple relevance score based on plain text matching.

**Data flow**: It receives a sequence of query terms and one skill card. It combines the card name and description into one lowercase search area, checks each term against it, and returns the number of terms found.

**Call relations**: This is the actual matching step after `_query_terms` has cleaned the query. `lexical_score` calls it to turn prepared terms and a card into a score.

*Call graph*: called by 1 (lexical_score).


##### `lexical_score`  (lines 50–55)

```
def lexical_score(query: str, card: SkillCard) -> int
```

**Purpose**: Gives one skill card a simple relevance score for a query. Someone can use it to ask, 'How many meaningful words from this query show up in this skill?'

**Data flow**: It receives a raw query and a skill card. It first asks `_query_terms` to clean the query, then asks `_term_hits` to count matches inside the card's name and description. It returns that count as an integer score.

**Call relations**: This function combines the query-cleaning and card-matching helpers into a public scoring operation. It does not drive the main block rendering itself, but it uses the same matching logic that the ranking path relies on.

*Call graph*: calls 2 internal fn (_query_terms, _term_hits).


##### `select_top_k`  (lines 58–66)

```
def select_top_k(query: str, cards: Sequence[SkillCard]) -> tuple[SkillCard, ...]
```

**Purpose**: Chooses the most relevant unpinned skill cards for a query. It is used when there are too many saved skills to show every description, so only a small number get full detail.

**Data flow**: It receives the current query and a sequence of skill cards. It prepares the query terms once, removes pinned cards from consideration, ranks the remaining cards by how many query terms they match, and returns up to the configured top number. When scores tie, the original card order is preserved by the stable sort behavior.

**Call relations**: When `member_visibility` cannot fit the full catalog, it calls `select_top_k` to decide which unpinned cards deserve full lines. The result feeds directly into the saved-skills block, while other unpinned skills can still appear by name.

*Call graph*: calls 1 internal fn (_query_terms); called by 1 (member_visibility).


##### `skill_line`  (lines 69–71)

```
def skill_line(card: SkillCard) -> str
```

**Purpose**: Turns one skill card into the short display line used in prompts and saved-skill blocks. It also caps the line length so one long description cannot crowd out many other skills.

**Data flow**: It receives a skill card. It formats the card as `- name: description`, then cuts the result to the maximum allowed line length. It returns that single string.

**Call relations**: This is the common formatting step for nearly every size decision in the file. `folds_into_prompt`, `prompt_index`, `catalog_fits`, and `member_visibility` all depend on this exact rendering so measurement and display stay consistent.

*Call graph*: called by 4 (catalog_fits, folds_into_prompt, member_visibility, prompt_index).


##### `folds_into_prompt`  (lines 74–78)

```
def folds_into_prompt(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Answers whether all member skill cards are small enough to be included directly in the main system prompt. This keeps small saved-skill collections simple and visible in the same place as deployed skills.

**Data flow**: It receives a sequence of cards. It turns each card into a capped `skill_line`, measures the total joined size, and compares that size with the prompt-fold budget. It returns `true` if the cards fit and `false` otherwise.

**Call relations**: This function relies on `skill_line` for the exact text being measured and `_joined_size` for the total. `prompt_index` calls it before deciding whether member skills join the normal prompt index or must be shown elsewhere.

*Call graph*: calls 2 internal fn (_joined_size, skill_line); called by 1 (prompt_index).


##### `prompt_index`  (lines 81–93)

```
def prompt_index(registry: SkillRegistry) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the list of skills that should appear in the prompt's normal skill index for a turn. It includes deployed skills, and also includes member saved skills when that member set is small enough to fit.

**Data flow**: It receives a skill registry, reads its member cards, and checks whether those cards fold into the prompt. If they do not fit, it returns only the registry's normal deployed-skill index. If they do fit, it returns that deployed index plus each member skill as a name and capped description pair.

**Call relations**: This function is the bridge between the registry and the prompt-building code. It calls the registry's `index()` for deployed skills, uses `folds_into_prompt` to make the placement decision, and uses `skill_line` so member descriptions are capped the same way they were measured.

*Call graph*: calls 3 internal fn (index, folds_into_prompt, skill_line).


##### `catalog_fits`  (lines 96–99)

```
def catalog_fits(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Answers whether every saved skill can be shown as a full line inside the separate saved-skills block. It is the check for the 'show everything in detail' case.

**Data flow**: It receives a sequence of cards. It formats every card with `skill_line`, measures the size those lines would take inside the block wrapper, and compares that size with the block budget. It returns a boolean.

**Call relations**: This is a standalone version of a decision also made inside `member_visibility`. It uses `_block_size` to measure the wrapped block and `skill_line` for the exact displayed lines.

*Call graph*: calls 2 internal fn (_block_size, skill_line).


##### `member_visibility`  (lines 113–146)

```
def member_visibility(query: str, cards: Sequence[SkillCard]) -> MemberVisibility
```

**Purpose**: Makes the complete saved-skill visibility decision for one turn. It decides whether member skills fold into the prompt, whether the full catalog fits in a block, and what block text should be sent if needed.

**Data flow**: It receives the current query and the member skill cards. It renders each card line once, measures whether the set fits the system prompt and then the saved-skills block, and returns a `MemberVisibility` object with those decisions plus the final block text. If the catalog is too large, it keeps pinned cards first, asks `select_top_k` for relevant unpinned cards, shows remaining unpinned skills as names, trims from the end if needed, and adds a dropped-count note when anything was removed.

**Call relations**: This is the central flow of the file. `member_block` calls it when it only needs the rendered block, and internally it uses the sizing helpers, `skill_line`, `select_top_k`, and `_render` to keep measuring, ranking, and final text generation in sync.

*Call graph*: calls 5 internal fn (_block_size, _joined_size, _render, select_top_k, skill_line); called by 1 (member_block); 1 external calls (__init__).


##### `member_block`  (lines 149–157)

```
def member_block(query: str, cards: Sequence[SkillCard]) -> str
```

**Purpose**: Returns just the saved-skills text block for a turn. It is a convenient wrapper for callers that do not need the extra visibility details.

**Data flow**: It receives the query and member cards. It delegates the full decision to `member_visibility`, then extracts and returns only the `block` field. The result is either an empty string, when no separate block is needed, or a formatted `<saved_skills>` block.

**Call relations**: This function sits at the edge of the file's main logic. It calls `member_visibility` and hands its rendered block to whatever prompt or turn-message builder asked for saved-skill text.

*Call graph*: calls 1 internal fn (member_visibility).


##### `_joined_size`  (lines 163–164)

```
def _joined_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how many characters a list of lines would use when joined with newline characters. This lets the file compare displayed text against strict size budgets.

**Data flow**: It receives a sequence of already-rendered lines. It adds each line's length plus the newline space between lines, while treating an empty list as size zero. It returns the total character count.

**Call relations**: This is a low-level measuring helper. `folds_into_prompt`, `_block_size`, and `member_visibility` call it so all budget checks count joined lines the same way.

*Call graph*: called by 3 (_block_size, folds_into_prompt, member_visibility).


##### `_block_size`  (lines 167–168)

```
def _block_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how large a saved-skills block would be after adding its opening and closing tags. It answers, 'Will these lines fit once wrapped as a block?'

**Data flow**: It receives a sequence of rendered lines. It asks `_joined_size` for the body size, then adds the fixed wrapper text and the extra newline used when the block has content. It returns the total character count.

**Call relations**: This helper builds on `_joined_size`. `catalog_fits` and `member_visibility` use it before deciding whether the whole catalog can be displayed as full lines.

*Call graph*: calls 1 internal fn (_joined_size); called by 2 (catalog_fits, member_visibility).


##### `_render`  (lines 171–172)

```
def _render(lines: tuple[str, ...]) -> str
```

**Purpose**: Creates the final saved-skills block text from already-chosen lines. It wraps the lines between the fixed opening and closing tags.

**Data flow**: It receives a tuple of line strings. It places the opening tag first, then all lines, then the closing tag, joins them with newlines, and returns the finished string.

**Call relations**: This is the last formatting step in the main path. `member_visibility` calls it after deciding exactly which lines fit and in what order.

*Call graph*: called by 1 (member_visibility).


### Generated Catalog Skills
Builds live, model-readable catalog skills for available models and spawnable agents.

### `core/src/ufo/harness/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a documentation trust problem. A system may support many AI models, and each model has details that matter: who provides it, how much text it can read at once, what it costs, whether it supports reasoning, and which API style it uses. If those facts were written by hand in a separate guide, they could easily drift away from what the program actually uses. This file avoids that by generating the catalog directly from the same live registry the runtime uses to route requests and calculate prices.

The main function, `model_catalog_skill`, takes a `ModelRegistry`, which is the system’s current list of known model specifications. It sorts those models by id, turns each one into a row in a Markdown table, and wraps that table in a `RuntimeSkill`. A runtime skill is a chunk of instructions or reference material the system can load and show to the AI or user when needed.

A small helper, `_per_mtok`, formats stored price numbers into readable dollars per million tokens. A token is a small piece of text used by AI models for counting input and output size. The result is like a restaurant menu generated straight from the kitchen’s inventory system: users see what is truly available, not what someone remembered to update.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns an internal price value into a readable dollar amount per million tokens. It exists so the catalog table shows prices in a form humans can quickly compare, such as `$1.25`, instead of a tiny accounting unit.

**Data flow**: It receives a price stored as an integer count of micro-dollars per million tokens. It divides that by the constant number of micro-dollars in one dollar, formats the result with two decimal places, and returns a string with a dollar sign. It does not change any outside state.

**Call relations**: `model_catalog_skill` calls this helper while building each model’s table row. It uses it once for the input price and once for the output price, so the final catalog can show both costs clearly.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function builds the complete model catalog as a runtime skill. Someone would use it during startup to create a trustworthy, automatically generated reference page for all models available in the current deployment.

**Data flow**: It receives a `ModelRegistry`, which contains model records. It reads those records, sorts them by model id, formats their facts into a Markdown table, and combines that table with a name and description. It then creates and returns a `RuntimeSkill` containing both the display instructions and the raw Markdown form of the skill.

**Call relations**: This is the main builder in the file. As it turns registry records into table rows, it calls `_per_mtok` to make prices readable. At the end, it hands the finished content to `RuntimeSkill.__init__`, producing the object that the wider runtime can load as the `model-catalog` skill.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `core/src/ufo/host/spawn_catalog.py`

`domain_logic` · `per-turn skill assembly`

When an agent wants to delegate a task, it needs to know two things: what can I spawn, and what information must I send? This file creates that answer fresh for each turn. That matters because some targets are fixed profiles from the live subagent registry, while others are agents stored in the current workspace database. Workspace agents can change, be archived, or be visible only to certain members.

Think of it like printing a current restaurant menu right before someone orders, rather than relying on an old menu taped to the wall. The catalog lists each available target, labels it as either a built-in profile or a workspace agent, and shows the payload keys the target accepts.

The file also applies visibility rules. A normal member sees their own workspace agents. An admin sees all active agents in the workspace, including ownerless ones. If a workspace agent has the same name as a built-in profile, the agent is listed with an `agent:` prefix, because that is the exact name spawn must use to avoid confusion.

The final result is returned as a `RuntimeSkill`, which is a skill-like document the running agent can load and read before choosing a spawn target.

#### Function details

##### `_profile_payload`  (lines 29–36)

```
def _profile_payload(profile: SubagentProfile) -> str
```

**Purpose**: This helper turns a built-in subagent profile’s input model into a short human-readable list of payload fields. It marks which fields are required and which are optional, so the catalog can tell the agent what to send.

**Data flow**: It receives one subagent profile. It reads the profile’s input model fields. If there are no fields, it returns “(no fields)”. Otherwise, it sorts the field names and returns a comma-separated text list, marking optional fields with “(optional)”. It does not change anything outside itself.

**Call relations**: The main catalog builder, `spawn_catalog_skill`, calls this while writing the profile rows of the table. Its output becomes the payload column for each built-in profile.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `_schema_payload`  (lines 39–49)

```
def _schema_payload(schema: Mapping[str, object] | None) -> str
```

**Purpose**: This helper turns a workspace agent’s stored input schema into a short list of payload keys. It is used for agents whose expected input is described by a schema saved in the database.

**Data flow**: It receives either a schema-like mapping or no schema. If there is no schema, it falls back to the default task input fields. If the schema has no useful `properties`, it returns “(no fields)”. Otherwise, it reads the property names and the schema’s `required` list, then returns sorted field names with optional ones marked. It only produces text; it does not edit the schema.

**Call relations**: `spawn_catalog_skill` calls this for each workspace agent row it read from the database. The returned text becomes the payload column for that agent in the generated catalog.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `spawn_catalog_skill`  (lines 52–112)

```
async def spawn_catalog_skill(registry: SubagentRegistry, member_id: UUID | None) -> RuntimeSkill
```

**Purpose**: This is the main builder for the spawn catalog skill. It gathers the current built-in subagent profiles and the workspace agents this member is allowed to spawn, then turns them into a markdown table wrapped as a `RuntimeSkill`.

**Data flow**: It receives the live subagent registry and the current member’s ID, if there is one. It reads profile names from the registry, opens a workspace database transaction, checks whether the member is an admin, and queries active agent rows for the current workspace. Admins get all active rows; non-admins get only their own rows. It then formats profile rows and agent rows, using `_profile_payload` and `_schema_payload` to describe the expected payload. The output is a `RuntimeSkill` containing the catalog name, description, instructions, and raw markdown.

**Call relations**: This function is called when the runtime needs to make the spawn catalog available to an agent for the current turn. Inside, it asks the workspace context for the current workspace, uses the database transaction helper to read agent rows safely, calls `member_is_admin` to apply visibility rules, uses SQLAlchemy to build the database query, and finally constructs a `RuntimeSkill` that the agent can load before using spawn.

*Call graph*: calls 2 internal fn (_profile_payload, _schema_payload); 5 external calls (__init__, select, workspace_tx, member_is_admin, ws_current).


### Turn Environment Assembly
Combines prompts, tools, skills, environment documents, and setup files into the complete per-turn host environment.

### `core/src/ufo/host/assemble.py`

`orchestration` · `per-turn environment assembly before the model runs`

Before an agent or subagent can answer, the runtime needs a carefully prepared “room” for it to work in. This file sets up that room. It gathers contributions from installed manifests, such as prompt sections, tools, hooks, credentials, workspace facts, and skills. Then it filters them through the agent’s grants and the audience for the turn, so the model only sees and can use what it is allowed to use.

An optional environment document can reshape the room. It can edit or replace prompt text, remove tools, change tool descriptions, adjust parameter descriptions, add seed files, or define a special `run` tool that executes a shell command inside the turn’s sandbox. Importantly, this document cannot widen permissions. If it tries to refer to a scoped tool that was not already offered, the turn fails clearly instead of silently doing something unsafe.

The main class, `HostEnvironment`, is the composition point. Think of it like a stage manager: it collects props from many departments, checks the rules, applies last-minute script notes, and hands the final stage setup to the actor. The helper functions do the focused work of editing prompts and skills, describing tool inputs, and creating sandboxed command tools.

#### Function details

##### `HostEnvironment.assemble`  (lines 113–223)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Builds the full environment for one turn: prompt, tools, hooks, skills, preload material, seeded files, and visibility information. This is the central function that decides what the model will read and what it may call.

**Data flow**: It starts with an assemble request containing the turn, agent, profile, audience, skills, grants, and optional environment document. It loads the document if present, gathers tools and hooks from manifests, adds workspace and member skills when allowed, builds the right prompt for either the main agent or a subagent, applies document edits, loads any requested files, and finally returns an `AssembledTurn` containing the finished environment.

**Call relations**: This function calls the smaller collection methods on `HostEnvironment` to fetch tools, hooks, member skills, and environment-document storage. It then hands specific rewrite jobs to `_skills_with_document` and `_applied_document`, and packages everything into the object the runtime uses for the turn.

*Call graph*: calls 6 internal fn (_document_blob, hooks, member_skills, tools, _applied_document, _skills_with_document); 21 external calls (__init__, __init__, __init__, span, load_environment_document, load_environment_file, turn_workspace_facts, spawn_catalog_skill, setup_skill, render_system_prompt (+11 more)).


##### `HostEnvironment.tools`  (lines 225–239)

```
def tools(self, *, audience: Audience, scheduled_member_id: UUID | None) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext], ObjectVerbs]
```

**Purpose**: Collects the tools available for this turn from extension manifests. A tool is an action the model may call, such as querying an index or using a connector.

**Data flow**: It receives the audience and, for scheduled turns, an optional member identity. It combines the host’s manifests, credentials, index, embedding client, URLs, artifact secret, and blob store, then returns tool definitions, extension context for those tools, and the allowed object actions.

**Call relations**: It is called during `HostEnvironment.assemble` near the start of turn setup. It delegates the actual manifest reading and tool construction to the extension loader, then the assembled result is later filtered by agent or subagent grants.

*Call graph*: called by 1 (assemble); 1 external calls (turn_tools).


##### `HostEnvironment.hooks`  (lines 241–250)

```
def hooks(self, *, audience: Audience) -> HookChain
```

**Purpose**: Collects turn hooks from extension manifests. Hooks are extension callbacks that can run around parts of a turn, like observers or extra behavior attached to the runtime.

**Data flow**: It takes the turn audience and reads the host’s manifests, credentials, index, embedding client, tailer, and public URL. It returns a `HookChain`, which is the ordered set of hooks the runtime can use for this turn.

**Call relations**: It is called by `HostEnvironment.assemble` while building the turn package. The heavy lifting is delegated to the extension loader, and the resulting hook chain is placed into the final `AssembledTurn`.

*Call graph*: called by 1 (assemble); 1 external calls (turn_hooks).


##### `HostEnvironment.member_skills`  (lines 252–261)

```
async def member_skills(self, *, agent_name: str) -> tuple[tuple[SkillCard, ...], SkillMaterializer]
```

**Purpose**: Loads skills contributed by workspace members for a named agent. These are user- or member-authored instructions and materials that can become part of the agent’s skill set when workspace skills are enabled.

**Data flow**: It receives an agent name and reads manifests plus optional credentials, index, and embedding client. It returns visible skill cards and a materializer, which is the piece that can later load the full skill contents when needed.

**Call relations**: It is called by `HostEnvironment.assemble` only when the agent is allowed to use workspace skills. The returned skill cards are merged into the turn’s skill registry and later used to decide what member-authored skills the current inbound message can see.

*Call graph*: called by 1 (assemble); 1 external calls (turn_member_skills).


##### `HostEnvironment.environment_model`  (lines 263–267)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Looks up which model an environment document requests for one target. This lets the runtime ask, before full assembly, whether the main agent or a named profile should use a specific model.

**Data flow**: It receives an environment document identifier and an optional profile name. It loads the document from the blob store, chooses either the main block or the named profile block, and returns that block’s model name, or `None` if no model is specified.

**Call relations**: It uses `_document_blob` to find the storage for environment documents and then calls the environment loader. Unlike `assemble`, it only reads the model choice; it does not build prompts, tools, or skills.

*Call graph*: calls 1 internal fn (_document_blob); 1 external calls (load_environment_document).


##### `HostEnvironment.clis`  (lines 269–270)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Returns command-line connector credentials advertised by manifests. These are credentials intended for connector command-line tools rather than direct model tools.

**Data flow**: It reads the host’s manifests and extracts a mapping of connector CLI names to their credential descriptions. The returned dictionary tells other setup code which connector CLIs can receive credentials.

**Call relations**: It delegates to the extension loader’s connector CLI reader. It stands apart from turn assembly, but uses the same manifest source as the rest of this file.

*Call graph*: 1 external calls (connector_clis).


##### `HostEnvironment.slots`  (lines 272–273)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Returns the credential slots that extensions want filled. A credential slot is a named place where a secret or login token may be injected if the deployment provides it.

**Data flow**: It reads the host’s manifests and returns the tuple of requested credential slots. It does not fetch the credentials themselves; it only reports what slots exist.

**Call relations**: It delegates to the extension loader’s slot discovery function. Other parts of the host can use this information during credential setup before turns are assembled.

*Call graph*: 1 external calls (injecting_slots).


##### `HostEnvironment._document_blob`  (lines 275–278)

```
def _document_blob(self) -> WorkspaceBlobStore
```

**Purpose**: Provides the blob store used to load environment documents and environment files. A blob store is shared storage for content addressed by an identifier or digest.

**Data flow**: It reads `self.blob`. If a blob store exists, it returns it. If not, it raises an error saying the host cannot load environment documents.

**Call relations**: It is used by `HostEnvironment.assemble` and `HostEnvironment.environment_model` before they load document content. It keeps the failure clear and early when document loading is requested but no storage was configured.

*Call graph*: called by 2 (assemble, environment_model).


##### `_skills_with_document`  (lines 281–310)

```
def _skills_with_document(skills: SkillRegistry, document: EnvironmentDocument | None) -> SkillRegistry
```

**Purpose**: Applies skill changes from an environment document to the existing skill registry. It can replace a known skill’s `SKILL.md`, apply exact text edits to it, or add a new document-defined skill.

**Data flow**: It receives the current skill registry and an optional environment document. If there are no document skills, it returns the registry unchanged. Otherwise it copies the registry’s named skills, applies replacements or exact edits, parses the resulting skill files back into loaded skill objects, removes document-touched skills from the bundled set, and returns an updated registry.

**Call relations**: It is called by `HostEnvironment.assemble` before prompts and tools are finalized. When it needs to edit existing skill text, it relies on `_edited`; when it has new skill files, it hands them to the skill parser so the rest of the runtime sees normal skill objects.

*Call graph*: calls 1 internal fn (_edited); called by 1 (assemble); 2 external calls (replace, parse_skill_content).


##### `_applied_document`  (lines 313–369)

```
def _applied_document(prompt: RenderedPrompt, tools: ToolRegistry, scoped: EnvironmentOverrides | None, global_tools: dict[str, ToolOverride]) -> tuple[RenderedPrompt, ToolRegistry, bool]
```

**Purpose**: Applies one environment document block to the already assembled prompt and tool offer. It can remove tools, rewrite tool descriptions, adjust parameter help text, add sandboxed `run` tools, and edit or replace the prompt.

**Data flow**: It receives the current rendered prompt, current tool registry, optional scoped overrides for the main agent or profile, and top-level tool overrides. It checks that scoped tool names are valid, merges global and scoped tool changes, rewrites the tool map, applies prompt changes if present, and returns the new prompt, new registry, and a flag saying whether the prompt was fully replaced.

**Call relations**: It is called by `HostEnvironment.assemble` after the base prompt and tools have been selected under platform grants. It uses `_run_tool` to create command tools, `_described_model` to alter parameter descriptions, and `_applied_prompt` to rewrite the prompt.

*Call graph*: calls 3 internal fn (_applied_prompt, _described_model, _run_tool); called by 1 (assemble); 2 external calls (__init__, replace).


##### `_applied_prompt`  (lines 372–375)

```
def _applied_prompt(content: str, override: PromptOverride) -> RenderedPrompt
```

**Purpose**: Turns a prompt override into a new rendered prompt. It either replaces the whole prompt or applies exact text edits to the existing prompt.

**Data flow**: It receives the current prompt text and a prompt override. If the override contains full replacement text, that text becomes the prompt. Otherwise each requested edit is applied to the current content, and the resulting text is wrapped as a rendered prompt.

**Call relations**: It is called only by `_applied_document` when an environment block includes prompt changes. For edit-style changes, it delegates the safety check and replacement work to `_edited`.

*Call graph*: calls 1 internal fn (_edited); called by 1 (_applied_document); 1 external calls (rendered_prompt).


##### `_edited`  (lines 378–386)

```
def _edited(content: str, edits: tuple[TextEdit, ...], subject: str) -> str
```

**Purpose**: Applies exact find-and-replace edits while preventing ambiguous changes. Each old text fragment must appear exactly once, so the document cannot accidentally change the wrong copy.

**Data flow**: It receives source text, a list of edits, and a subject name used in error messages. For each edit, it counts occurrences of the old text. If the count is not exactly one, it raises an error; otherwise it replaces that one occurrence. It returns the final edited text.

**Call relations**: It is used by `_applied_prompt` for prompt edits and by `_skills_with_document` for skill edits. This shared helper is what makes document text rewrites fail loudly instead of guessing.

*Call graph*: called by 2 (_applied_prompt, _skills_with_document).


##### `_described_model`  (lines 389–402)

```
def _described_model(model: type[BaseModel], tool: str, parameters: dict[str, str]) -> type[BaseModel]
```

**Purpose**: Creates a copy-like version of a tool’s input model with new descriptions for selected parameters. This changes what the model reads about the parameters, not what the tool is allowed to do.

**Data flow**: It receives a Pydantic model, which is a Python class describing valid input fields, the tool name, and a mapping of parameter names to new description text. It rejects unknown parameter names, deep-copies the matching field definitions, updates their descriptions, and returns a new model class based on the old one.

**Call relations**: It is called by `_applied_document` when an environment document changes parameter descriptions for an existing tool. The resulting model is put back into the tool definition so the model sees clearer or different parameter guidance.

*Call graph*: called by 1 (_applied_document); 2 external calls (deepcopy, create_model).


##### `_run_tool`  (lines 405–440)

```
def _run_tool(name: str, description: str, inputs: dict[str, ToolInput], run: str) -> ToolDef
```

**Purpose**: Builds a tool definition for an environment-provided shell command. This is the one kind of tool an environment document may add, and it runs inside the turn’s sandbox rather than granting new host permissions.

**Data flow**: It receives a tool name, description, input specifications, and a shell command string. It creates a Pydantic input model from the declared inputs, defines an async handler that runs the command with inputs passed as environment variables, and returns a `ToolDef` marked as side-effecting because it may change files or external state inside the sandbox.

**Call relations**: It is called by `_applied_document` when a tool override includes a `run` command. The nested handler later runs when the model calls that generated tool.

*Call graph*: called by 1 (_applied_document); 3 external calls (__init__, Field, create_model).


##### `_run_tool.handler`  (lines 417–432)

```
async def handler(ctx: ToolContext, payload: BaseModel) -> ToolResult
```

**Purpose**: Executes the generated `run` tool and converts the process result into model-readable tool output. It reports success, failure, and timeout in a consistent format.

**Data flow**: It receives a tool context and a validated payload object. It turns the payload into a shell command through `_run_command`, asks the task runner to execute it, combines standard output and standard error, and returns a `ToolResult`. A timeout or nonzero exit code becomes an error result; exit code zero becomes a normal result.

**Call relations**: This handler is created inside `_run_tool` and is attached to the returned `ToolDef`. It is not used during assembly itself; it runs later if the model chooses to call that generated tool.

*Call graph*: calls 1 internal fn (_run_command); 3 external calls (__init__, __init__, run_task).


##### `_run_command`  (lines 443–451)

```
def _run_command(run: str, payload: BaseModel) -> str
```

**Purpose**: Builds the actual shell command string used by a generated `run` tool. It safely turns tool inputs into environment variables before running the configured command.

**Data flow**: It receives the configured command text and a Pydantic payload. It dumps the payload to plain JSON-like values, skips inputs whose value is `None`, formats the rest as `INPUT_NAME=value` environment variables with shell quoting, quotes the command itself, and returns a `sh -c ...` command string.

**Call relations**: It is called by the generated `_run_tool.handler` immediately before execution. Its job is to bridge structured tool input into the plain text world of a shell command while quoting values to avoid accidental shell syntax problems.

*Call graph*: called by 1 (handler); 3 external calls (dumps, model_dump, quote).
