# Tool and spawn menu construction  `stage-5.2`

This stage prepares the menu of actions available on a single turn. Before the system asks the model what to do next, it must know exactly which tools are allowed, which object operations can be called, and which child agents or helpers can be started. This is like setting out the correct tools on a workbench before a job begins.

The tool registry is the rulebook for that workbench. It defines what counts as a tool, how its name and description are shown to the model, and how the system finds the real code when a tool is requested. It also blocks unsafe or confusing entries, such as duplicate names or names reserved for special system behavior.

The spawn catalog builds the separate list of things that may be spawned, meaning started as child tasks or agents, during this turn. It turns those allowed targets into clear instructions, including valid names and required input fields. Together, these files create a safe, precise action menu for the current turn.

## Files in this stage

### Tool and spawn availability
Defines the spawnable targets and runtime tool registry rules that determine what actions are available in the current turn.

### `core/src/ufo/host/spawn_catalog.py`

`domain_logic` · `per turn, before or during spawn request handling`

In this system, `spawn` means starting or dispatching work to another agent-like target. The tricky part is that the valid targets are not fixed forever. Some come from deployed subagent profiles, while others are workspace agents stored in the database. This file reads both sources fresh each turn so the instructions shown to the caller match what `spawn` will actually accept.

It produces small records called `SpawnTarget`s. Each one says: the exact name to use, whether it is a built-in profile or a workspace agent, and which payload keys it expects. This is like printing the current menu before someone orders, so they do not guess at dishes that are unavailable or order with the wrong details.

The file also protects against name clashes. If a workspace agent has the same name as a profile, the agent is shown with an `agent:` prefix, because that is the exact form `spawn` needs to distinguish it.

Finally, the same target list is used in two places: the description of the `payload` field on `spawn`, and a separate runtime skill named `spawn-catalog`. This matters because some callers may not load the catalog skill before trying to spawn something, so the payload description itself still needs to include the required keys.

#### Function details

##### `spawn_targets`  (lines 42–87)

```
async def spawn_targets(registry: SubagentRegistry, authority: ExecutionAuthority) -> tuple[SpawnTarget, ...]
```

**Purpose**: Builds the complete list of targets that can be spawned in the current turn. It combines deployed subagent profiles with workspace agent rows that the current member is allowed to see.

**Data flow**: It receives the live subagent registry and the current execution authority, which tells it who is acting. It reads the member id from that authority, checks the current workspace, asks the database for active agents visible to that member, and reads each target's expected input shape. It returns an ordered tuple of `SpawnTarget` records, each containing the exact spawn name, the source kind, and the payload keys needed.

**Call relations**: This is the source of truth for the other helpers in this file. It calls into the workspace transaction layer to read agents from the database, checks admin status before deciding which agent rows are visible, and uses contract helpers to turn input schemas into simple payload-key descriptions. The resulting list is then meant to be handed to `spawn_payload_description` and `spawn_catalog_skill` so their instructions match real dispatch behavior.

*Call graph*: 8 external calls (__init__, select, workspace_tx, authority_member_id, member_is_admin, input_contract, payload_keys, ws_current).


##### `spawn_payload_description`  (lines 90–99)

```
def spawn_payload_description(targets: tuple[SpawnTarget, ...]) -> str
```

**Purpose**: Creates the human-readable description for the `payload` field of a spawn call. Its job is to make the required payload keys visible right where a caller writes the spawn request.

**Data flow**: It takes the tuple of `SpawnTarget` records already discovered for this turn. If the list is empty, it returns a generic explanation. Otherwise, it joins each target name with its required keys and returns one sentence describing the payload shapes for all available targets.

**Call relations**: This function depends on `spawn_targets` having already gathered the current menu of spawn targets. It does not call other project functions; it simply formats that menu into wording that can be attached to the spawn interface, helping callers avoid wrong-payload attempts even if they never open the separate catalog skill.


##### `spawn_catalog_skill`  (lines 102–122)

```
def spawn_catalog_skill(targets: tuple[SpawnTarget, ...]) -> RuntimeSkill
```

**Purpose**: Builds a runtime skill document that lists every spawn target and the payload keys it requires. Callers can load this skill when they need to choose a target they have not used before.

**Data flow**: It receives the current tuple of `SpawnTarget` records. It turns them into a small markdown table with columns for target name, target kind, and payload keys. It wraps that table with a skill name, description, instructions, and raw markdown, then returns a `RuntimeSkill` object.

**Call relations**: Like `spawn_payload_description`, this function should be fed the list produced by `spawn_targets`. It hands the formatted catalog to `RuntimeSkill`, which makes the information available through the system's skill mechanism. Because it uses the same target list as spawn dispatch, the catalog stays aligned with what `spawn` will actually accept.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/tools/registry.py`

`domain_logic` · `startup validation and request-time tool lookup`

In this project, a tool is something the model can ask the runtime to do, such as reading data, calling an external service, or performing an action on an object. This file gives every tool a clear definition: its public name, its input shape, the function that runs it, and safety flags that tell the engine how carefully to treat the result or the side effects.

The main idea is like a catalog at a service desk. Each catalog entry says what request forms are accepted, who does the work, and whether the work is risky, repeatable, or tied to a specific object. Global tools can be placed directly in the `ToolRegistry`, which is the frozen lookup table the engine uses. Object actions are different: they are attached to a kind of object or one visible instance, and are addressed through a special `action:<kind>:<name>` identity instead of being listed as ordinary wire tools.

The file also protects reserved names. For example, it reserves the `action:` prefix for object actions and the `requested_by` input field for the runtime’s own authority tracking. At startup, the registry checks for duplicate tool names, illegal bound actions in the global registry, bad presentation labels, and final-action models that the terminal frame cannot carry. Without these checks, the model could see confusing schemas, tools could collide by name, or authority information could be overwritten by user-defined inputs.

#### Function details

##### `ToolDef.canonical_id`  (lines 95–100)

```
def canonical_id(self) -> str
```

**Purpose**: Gives a tool its stable identity across the whole deployment. Ordinary tools use their name, while object-bound actions get a special identity that includes the object kind.

**Data flow**: It reads the tool definition. If the tool is not bound to an object, it returns the tool name unchanged. If it is bound, it combines the reserved `action:` prefix, the object kind, and the tool name into one canonical string.

**Call relations**: Other parts of the runtime can use this identity for allowlists, telemetry, hooks, and idempotency. It is the bridge between plain global tools and object actions, which are deliberately not placed in the normal wire registry.


##### `ToolDef.schema`  (lines 102–115)

```
def schema(self, *, include_requested_by: bool=True) -> ToolSchema
```

**Purpose**: Builds the tool description that can be sent to the model client, including the JSON-style input shape the model must follow. It can also add the runtime’s `requested_by` field, which records which message explicitly asked for the call.

**Data flow**: It starts from the tool’s Pydantic input model, which is a Python model that can produce a machine-readable schema. It ensures there is a properties section, optionally adds the reserved `requested_by` property, then returns a `ToolSchema` containing the tool name, description, and input schema.

**Call relations**: When tool schemas are being prepared for the wire, this function turns each internal `ToolDef` into the external `ToolSchema` object. Its direct handoff is to `ToolSchema.__init__`, which packages the name, description, and input schema.

*Call graph*: 1 external calls (__init__).


##### `validate_tool_declaration`  (lines 118–142)

```
def validate_tool_declaration(tool: ToolDef[Any], label: str) -> None
```

**Purpose**: Checks that one tool definition follows the project’s safety and consistency rules. It is used as a boot-time gate so invalid tools fail early instead of creating confusing behavior later.

**Data flow**: It receives a tool and a human-readable label for error messages. It inspects presentation settings, object binding settings, and final-action model settings. If everything is valid, it changes nothing and returns nothing; if something is wrong, it raises a `ValueError` explaining the problem.

**Call relations**: The registry calls this during `ToolRegistry.__post_init__` for every tool it contains. This keeps local declaration mistakes, such as an empty portal label or an impossible bound action, from reaching request-time dispatch.

*Call graph*: called by 1 (__post_init__).


##### `ToolRegistry.__post_init__`  (lines 149–169)

```
def __post_init__(self) -> None
```

**Purpose**: Runs the registry’s integrity checks immediately after the frozen registry object is created. It makes sure the registry is safe to use as the engine’s source of truth for global tools.

**Data flow**: It reads the tuple of tools stored in the registry. It checks for duplicate names, object-bound actions that should not be in this registry, names using the reserved `action:` prefix, and input models that try to define the reserved `requested_by` field. Then it sends each tool through `validate_tool_declaration`. It returns nothing, but it may stop startup by raising `ValueError`.

**Call relations**: This is the construction-time checkpoint for `ToolRegistry`. Its main helper is `validate_tool_declaration`, which performs per-tool checks after the registry-level name and reservation checks are complete.

*Call graph*: calls 1 internal fn (validate_tool_declaration).


##### `ToolRegistry.schemas`  (lines 171–172)

```
def schemas(self, *, include_requested_by: bool=True) -> tuple[ToolSchema, ...]
```

**Purpose**: Produces the complete set of wire-ready tool schemas for all tools in the registry. This is what lets the model client know which tools exist and what inputs each one accepts.

**Data flow**: It reads the registry’s stored tool definitions and the `include_requested_by` option. For each tool, it asks the tool to build its schema with the same option, then returns all schemas as an immutable tuple.

**Call relations**: This function is used when the runtime needs to advertise available global tools. It relies on each `ToolDef.schema` to convert an internal tool definition into the external schema format.


##### `ToolRegistry.get`  (lines 174–178)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds one registered tool by its public name. It gives dispatch code a simple way to turn a model-requested tool name into the actual tool definition and handler.

**Data flow**: It receives a name string and scans the registry’s tool tuple. If it finds a tool with that name, it returns the matching `ToolDef`. If no tool matches, it raises `KeyError` so the unknown name fails loudly.

**Call relations**: This is the request-time lookup path for ordinary global tools. It depends on `ToolRegistry.__post_init__` having already ensured names are unique, so a successful lookup can safely return one clear tool definition.
