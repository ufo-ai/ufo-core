# Subagents, delegated pipelines, and long-running objectives  `stage-12`

This stage is behind-the-scenes support for work the main agent should not do alone. It is used during the main work loop when the agent needs a helper, a specialized workflow, or a longer plan that must be checked over time. Think of it as a dispatch desk: it chooses the right helper, gives clear instructions, tracks the job, and brings back results.

The specialist profiles are the job descriptions. They define helpers for general tasks, web browsing, document writing, research, website building, coding-style sweeps, and a brief-writing pipeline that splits outlining, drafting, and critique. The contracts file defines the “forms” these helpers must receive and return, so confused or badly shaped messages are caught early.

The subagents loop is the machinery that starts helpers, validates their inputs and outputs, waits for them, cancels them, or lets them continue detached. Browser delegation adds tools for single or parallel web visits. Objectives and progress verification manage longer goals like a project notebook, storing steps, evidence, and checks so progress means “proved done,” not just “someone tried.”

## Sub-stages

- [Specialist subagent profiles](stage-12.1.md) `stage-12.1` — 6 files
- [Objectives and progress verification](stage-12.2.md) `stage-12.2` — 2 files

## Files in this stage

### Core subagent orchestration
Defines the handoff contracts and runtime machinery for starting, validating, waiting on, detaching, cancelling, and returning results from helper agents.

### `core/src/ufo/contracts.py`

`domain_logic` · `schema declaration, spawn, dispatch, and result delivery`

When the system starts or dispatches work to another agent, it needs a shared understanding of what the input and output data should look like. This file provides that shared rulebook. Some agents use normal Pydantic models, which are Python classes that validate data. Other agents can declare raw JSON Schema, which is a standard JSON-based way to describe allowed data shapes. This file makes both kinds look the same to the rest of the system: each can provide its schema, validate a Python value, and validate JSON text.

If no custom schema is supplied, the default input is an object with a freeform task string, and the default output is an object with a freeform result string. If a custom schema is supplied, JsonContract wraps it and checks payloads against it. Valid data is returned as ValidatedJson, a small wrapper that can be dumped back to a Python object or JSON text, much like a validated Pydantic model.

The file is also careful about safety. Declared schemas are limited in size, must describe a top-level object, must be valid JSON Schema, and may not use references or regular expressions. This matters because references could make the server fetch outside resources, and expensive regular expressions could slow down the single serving loop. In short, this file is the border guard for agent messages: it checks papers, rejects risky documents, and gives every caller the same kind of error report.

#### Function details

##### `ValidatedJson.model_dump`  (lines 47–48)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated payload as a normal Python value. This lets code treat a raw JSON-schema-validated value much like a Pydantic model that can be dumped after validation.

**Data flow**: It starts with a ValidatedJson object that contains some stored data. It does not change or re-check that data. It simply gives the stored value back to the caller.

**Call relations**: JsonContract.model_validate creates ValidatedJson after a payload passes schema validation. Later code can call this method when it wants the validated payload in ordinary Python form.


##### `ValidatedJson.model_dump_json`  (lines 50–51)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the validated payload into JSON text. This is useful when the system needs to send or store the value in the common text format used for messages.

**Data flow**: It takes the stored validated data, passes it to JSON encoding, and returns the resulting string. The wrapped data is not changed.

**Call relations**: After JsonContract.model_validate has accepted a payload and wrapped it in ValidatedJson, this method provides the JSON-text version of that same payload by handing it to json.dumps.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 60–61)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the raw JSON Schema represented by this contract. Callers use it to inspect or publish what shape of data the contract expects.

**Data flow**: It reads the schema stored inside the JsonContract and returns a plain dictionary copy. The original schema object inside the contract is left alone.

**Call relations**: This method lets JsonContract stand in the same place as a Pydantic model class, because Pydantic models also have a way to report their JSON schema.


##### `JsonContract.model_validate`  (lines 63–98)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks a Python value against this contract’s JSON Schema. If the value fits, it wraps it as ValidatedJson; if not, it raises a Pydantic-style ValidationError so the rest of the system sees the same error shape no matter what kind of contract was used.

**Data flow**: It receives any Python value and the schema stored in the JsonContract. It builds a JSON Schema validator with an empty reference registry, checks the value, sorts any problems by where they occurred in the data, and either turns those problems into ValidationError details or returns a ValidatedJson containing the original value. If the schema tries to resolve a reference, that is converted into a validation error rather than fetching anything external.

**Call relations**: JsonContract.model_validate_json calls this after turning JSON text into a Python value. This function relies on Draft202012Validator to do the schema checking, uses Registry to prevent outside reference resolution, and creates Pydantic-compatible error details so callers can catch one familiar error type.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 100–116)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks JSON text against this contract. It first makes sure the text is valid JSON, then validates the decoded value against the schema.

**Data flow**: It receives a string. It tries to parse that string as JSON; if parsing fails, it raises a Pydantic-style ValidationError describing invalid JSON. If parsing succeeds, it passes the decoded Python value to JsonContract.model_validate and returns that method’s ValidatedJson result.

**Call relations**: This is the text-entry path for JsonContract. It uses json.loads to decode the message, reports parse failures in the same ValidationError style as schema failures, and then hands the real schema check to JsonContract.model_validate.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `input_contract`  (lines 122–123)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used for incoming task data. If no custom schema is declared, it uses the default TaskInput model; otherwise it wraps the declared schema in JsonContract.

**Data flow**: It receives either a mapping that describes a JSON Schema or None. None becomes the built-in TaskInput contract. A provided schema becomes a new JsonContract around that schema.

**Call relations**: This is the small adapter that lets later spawn or dispatch code ask for an input contract without caring whether the source was a built-in Pydantic model or a custom raw schema.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 126–127)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used for result data. If no custom schema is declared, it uses the default ResultOutput model; otherwise it wraps the declared schema in JsonContract.

**Data flow**: It receives either a mapping that describes a JSON Schema or None. None becomes the built-in ResultOutput contract. A provided schema becomes a new JsonContract around that schema.

**Call relations**: This mirrors input_contract for the output side. It gives delivery code one contract-like object to use whether the result shape is the default one or a custom declared schema.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 130–145)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Checks whether a user-declared JSON Schema is safe and acceptable before it is stored. This prevents risky or malformed schemas from being saved and causing trouble later during agent spawning or message validation.

**Data flow**: It receives a candidate schema and the name of the field being checked. It measures the schema’s JSON size, confirms the top-level type is object, searches for forbidden keywords such as references and regular-expression patterns, and asks the JSON Schema library to confirm the schema is valid. If any check fails, it raises ValueError with a clear message; if all checks pass, it returns nothing and leaves the schema unchanged.

**Call relations**: This function calls _refused_keyword to scan through the schema’s nested structure. It is meant to run at the write point, when a declared schema is being stored, so later readers like input_contract, output_contract, and JsonContract do not discover unsafe schema features only after work has already started.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 148–160)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches a schema-like structure for keywords this system does not allow, such as JSON Schema references or regular-expression patterns. It is a helper for keeping declared schemas self-contained and safe to evaluate.

**Data flow**: It receives any nested value. If the value is a mapping, it checks each key and then searches each value. If the value is a list, it searches each item. It returns the first forbidden keyword it finds, or None if the whole structure is clean.

**Call relations**: check_declared_schema calls this before accepting a declared schema. By doing the recursive search here, the main schema-checking function can give one simple refusal message when a risky keyword is found anywhere inside the schema.

*Call graph*: called by 1 (check_declared_schema).


### `core/src/ufo/loop/subagents.py`

`orchestration` · `request handling and background result delivery`

This file is the project’s “delegate this job” machinery. When an agent needs help, it can spawn either a named subagent profile, which is a preset prompt and tool set, or a workspace agent, which is a full separate agent owned by someone in the workspace. Without this file, a parent agent could not safely hand work to a child, avoid duplicate child jobs during retries, or know whether the child’s final answer is trustworthy and shaped correctly.

The main flow is like giving a task ticket to a specialist. First, the target name is resolved. Then the payload is checked against the target’s expected input contract, meaning a declared shape for the data. A new child conversation and first child turn are recorded in the database, balance is checked, and the turn is placed on the background work queue. The parent can either wait for the answer or let the child run in the background.

The file is careful about races and failures. A retry with the same deduplication key reconnects to the same child instead of creating a duplicate. If a user message arrives while the parent is waiting, the child can be detached so the parent can respond now and receive the child’s result later. Final answers are validated against output contracts. Untrusted answers are wrapped so the parent treats them as quoted content, not as new instructions.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 131–135)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the configured subagent profiles do not reuse the same name. This prevents a spawn request from pointing to two different helpers at once.

**Data flow**: It reads the profile list stored on the registry, collects their names, and looks for duplicates. If every name is unique, nothing changes; if a name appears more than once, construction fails with a clear error.

**Call relations**: This runs automatically when a SubagentRegistry is created. It protects later lookups, such as SubagentRegistry.get and SubagentRegistry.find, from ambiguous profile names.


##### `SubagentRegistry.get`  (lines 137–143)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Returns the registered profile with a given name, or raises a helpful error if no such profile exists. Use this when missing profiles should stop the current operation immediately.

**Data flow**: It receives a profile name, asks SubagentRegistry.find to search the stored profiles, and either returns the matching profile or builds an UnknownSubagentProfile error that includes the available names.

**Call relations**: The queue profile resolver calls this when it must load a profile by name. It relies on SubagentRegistry.find for the actual search and turns a failed search into a loud, user-facing failure.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 145–146)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Looks for a profile by name and returns it if present. Unlike get, it quietly returns nothing when the profile is absent.

**Data flow**: It receives a name, scans the registry’s tuple of profiles, and returns the first profile whose name matches. If none match, it returns null.

**Call relations**: SubagentRegistry.get uses this as its search step. Other code in this file also uses the same idea when it needs to decide whether a profile still exists without immediately failing.

*Call graph*: called by 1 (get).


##### `_target_model`  (lines 161–169)

```
def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None
```

**Purpose**: Figures out which language model should be charged or selected for a spawned target when that can be known from the target itself. Profiles may pin a model; workspace agents use their own agent record instead.

**Data flow**: It receives either a subagent profile or an agent target. For a profile, it returns the profile’s model setting; for a workspace agent, it returns null so later code can use the spawned agent’s own model.

**Call relations**: Subagents.spawn calls this right before admitting the child turn. The result is passed into the balance check path so the platform weighs the new work against the model that will actually answer.

*Call graph*: called by 1 (spawn).


##### `subagent_system_prompt`  (lines 172–203)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the full system prompt for a profile-based subagent. It combines the profile’s own instructions, optional skill information, shared answer-formatting rules, and the final contract that tells the child how to finish.

**Data flow**: It receives a profile, a list of available skills, and optional preloaded skill bodies. It fills the skill-index slot, rejects any unfilled prompt slots, checks that preloaded skill text is not too large, appends shared discipline and finish instructions, and returns one complete prompt string.

**Call relations**: This is used by the subagent setup path outside the listed calls to give the child model its instructions. It calls render_skill_index to format available skills, loaded_context to add preloaded skill text, and a prompt-variable matcher to catch forgotten template slots before the prompt reaches the model.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 218–219)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: Returns a copy of the Subagents helper that acts with a specific member’s authority. This is how a tool call can say, “perform this spawn as this requester.”

**Data flow**: It receives a member id or null and creates a new Subagents value with that requester_member_id set. The original object is left unchanged.

**Call relations**: This uses dataclasses.replace to preserve all existing wiring while changing only the requester. Later checks, especially Subagents.acting_member_id and Subagents._may_spawn, read that requester to decide what is allowed.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 222–231)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Chooses which workspace member’s permissions apply to this spawn. It uses the explicitly authorized requester first, then falls back to the parent turn’s speaker, then to the member the parent turn represents.

**Data flow**: It reads requester_member_id, parent.speaker_member_id, and parent.on_behalf_of_member_id in order. It returns the first one that is present, or null if the turn has no member identity.

**Call relations**: Permission checks and child-turn admission use this property to stamp who the child is acting for. That keeps ownership checks, follow-up messages, and result delivery tied to the same person.


##### `Subagents.spawn`  (lines 233–342)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnResult
```

**Purpose**: Starts a child turn for a named subagent profile or workspace agent. It can wait for the result, return immediately for background work, or detach if a user message arrives while waiting.

**Data flow**: It receives a target name, input payload, and options such as background mode and deduplication key. It resolves the target, checks permissions for workspace agents, validates the input payload, creates or reuses a child conversation and turn, enqueues the turn, optionally waits for a terminal result, validates the output, and returns a SpawnResult.

**Call relations**: This is the central public operation in the file. It calls Subagents._resolve to find the target, Subagents._may_spawn for agent ownership checks, Subagents._admit to record the child, Subagents._enqueue to schedule work, and either Subagents._await_terminal or Subagents._await_terminal_or_detach when the caller wants foreground behavior.

*Call graph*: calls 7 internal fn (_admit, _await_terminal, _await_terminal_or_detach, _enqueue, _may_spawn, _resolve, _target_model); 7 external calls (__init__, __init__, input_contract, output_contract, turn_id_for, uuid4, uuid5).


##### `Subagents.result`  (lines 344–381)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the finished result of a child turn that this conversation spawned. It gives host-side code a safe way to retrieve the child’s validated output without trusting a model to restate it correctly.

**Data flow**: It receives a child turn id, verifies that the turn belongs to this spawning conversation, loads the child’s conversation, agent, and terminal frame from the database, finds the correct output contract, tries to validate the terminal text, and returns a SpawnResult. If the child has not finished, it raises an error; if validation fails, the output is left empty.

**Call relations**: This calls Subagents._require_child to enforce ownership, Subagents._agent_output_schema when the child is a workspace agent, and Subagents._untrusted_output to mark whether the result should be treated as untrusted. It is the readback partner to background spawning and delivered child results.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, output_contract, workspace_tx).


##### `Subagents.wait`  (lines 383–403)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits until a set of child turns have finished and reports their final status and text. It is for short tool-level waits, not the normal way background results are collected.

**Data flow**: It receives a tuple of child turn ids. For each id, it verifies the child relationship, waits until the child has a terminal frame, then returns a tuple of SubagentStatus objects containing status, final text, and whether the text is untrusted.

**Call relations**: It calls Subagents._require_child before waiting so callers cannot wait on someone else’s child. It then uses Subagents._await_terminal and Subagents._untrusted_output to produce safe status summaries.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 405–422)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a child turn that this parent is allowed to control. It is used when a spawned helper should stop rather than continue spending work.

**Data flow**: It receives a child turn id, verifies that it belongs to this spawning conversation, asks the shared cancellation system to cancel it, reloads the child’s stored status and terminal text, and returns a SubagentStatus.

**Call relations**: This starts with Subagents._require_child for the safety check and then hands the actual cancellation to cancel_one_turn. After cancellation, it reads the database so the caller gets the child’s current recorded state.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 424–546)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Adds a follow-up message to a background child’s own conversation. This lets the parent answer a child’s question or continue a child task without starting over.

**Data flow**: It receives the original child turn id, message text, a deduplication key, and a result-delivery option. It verifies the child, checks that its profile still exists if needed, locks the child conversation, either reuses a previous follow-up with the same dedup key or inserts the next turn, checks balance for new work, enqueues the follow-up if it is next in line, and returns its status.

**Call relations**: This uses Subagents._require_child to ensure the caller owns the child, Subagents._profile_model and Subagents._require_balance before admitting fresh work, and Subagents._enqueue to put the follow-up on the queue. It mirrors cancel and result by refusing ids that are not children of this conversation.

*Call graph*: calls 4 internal fn (_enqueue, _profile_model, _require_balance, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._resolve`  (lines 548–571)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Turns a spawn target string into the exact thing to run: either a registered profile or a workspace agent. It supports explicit names like profile:name and agent:name, and protects against ambiguous bare names.

**Data flow**: It receives the target text, checks whether it has a prefix, searches the profile registry and/or workspace agent table, and returns the resolved target object. If nothing matches, it raises an UnknownSpawnTarget error with available names; if a bare name matches both kinds, it raises AmbiguousSpawnTarget.

**Call relations**: Subagents.spawn calls this before doing any admission work. It calls Subagents._profile_names, Subagents._agent_names, and Subagents._agent_target to produce useful errors and to look up workspace agents.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._profile_names`  (lines 573–574)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of all registered subagent profiles in sorted order. This is mainly used to explain what targets are available when a spawn name is wrong.

**Data flow**: It reads the registry’s profile tuple, extracts each profile name, sorts the names, and returns them as a tuple.

**Call relations**: Subagents._resolve calls this when it needs to build an UnknownSpawnTarget error. It keeps error messages clear without exposing the full profile objects.

*Call graph*: called by 1 (_resolve).


##### `Subagents._agent_names`  (lines 576–585)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Lists the workspace agents that can be named as spawn targets. This helps make unknown-target errors useful.

**Data flow**: It opens a workspace database transaction, selects agent names for the parent turn’s workspace ordered by name, and returns them as a tuple.

**Call relations**: Subagents._resolve calls this only when it needs available agent names for an error. It uses the shared workspace transaction helper and SQL query layer to read the database.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 587–611)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Looks up one workspace agent by name and packages the facts needed to spawn it. These facts include its id, owner, and input and output schemas.

**Data flow**: It receives an agent name, queries the workspace agent table for a row with that name in the parent workspace, and returns an AgentTarget if found. If there is no matching row, it returns null.

**Call relations**: Subagents._resolve calls this while deciding whether a target name refers to an agent. Subagents.spawn then uses the returned AgentTarget to check ownership, choose the child agent id, and validate input and output.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 613–619)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Fetches the declared output schema for a workspace agent. This is needed to validate a finished agent child’s answer.

**Data flow**: It receives an agent id, queries the agent table for that row’s output_schema, and returns the schema dictionary or null if no schema is declared.

**Call relations**: Subagents.result calls this when reading the result of an agent child. The schema is converted into an output contract so the terminal text can be checked before being returned.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._may_spawn`  (lines 621–633)

```
async def _may_spawn(self, owner_member_id: UUID | None) -> bool
```

**Purpose**: Decides whether the acting member is allowed to spawn a workspace agent. Owners may spawn their own agents, and workspace admins may spawn any agent, including ownerless shared ones.

**Data flow**: It receives the target agent’s owner member id. It compares that owner to Subagents.acting_member_id, and if that is not enough, checks the database to see whether the acting member is a workspace admin. It returns true or false.

**Call relations**: Subagents.spawn calls this before admitting a workspace-agent child. It relies on member_is_admin for the admin check and prevents chat from opening agents the user would not be allowed to use elsewhere.

*Call graph*: called by 1 (spawn); 2 external calls (workspace_tx, member_is_admin).


##### `Subagents._untrusted_output`  (lines 635–644)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child’s text should be treated as untrusted content. Untrusted content is information the model may read, but should not be allowed to act like instructions.

**Data flow**: It receives a profile name or null. Agent children, represented by null, are always untrusted; profile children are untrusted if the profile is missing or if the profile declares untrusted output.

**Call relations**: Subagents.result and Subagents.wait call this when reporting child output or status. It keeps old or web-derived child answers from being silently treated as safe instructions.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 646–682)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Verifies that a given turn id belongs to a child spawned by this conversation and by the same acting member. It prevents one member or conversation from controlling another member’s spawned work.

**Data flow**: It receives a turn id, loads the child’s parent id, profile, and acting member stamp from the database, and checks whether the parent is this turn or a sibling turn in the same conversation. If the relationship and member match, it returns the child’s profile name or null; otherwise it raises an error.

**Call relations**: Subagents.result, Subagents.wait, Subagents.cancel, and Subagents.message all call this before touching a child. It is the common gate for reading, waiting on, cancelling, or continuing spawned turns.

*Call graph*: called by 4 (cancel, message, result, wait); 2 external calls (select, workspace_tx).


##### `Subagents._profile_model`  (lines 684–690)

```
def _profile_model(self, profile: str | None) -> str | None
```

**Purpose**: Finds the model pinned by a profile name, if that profile is still registered. It is used for billing or balance checks on follow-up work.

**Data flow**: It receives a profile name or null, searches the current registry, and returns the profile’s model if found. If no matching profile exists, it returns null rather than failing.

**Call relations**: Subagents.message calls this before checking balance for a follow-up turn. Returning null for missing profiles avoids turning a manifest change into a billing lookup failure for an already-existing child.

*Call graph*: called by 1 (message).


##### `Subagents._require_balance`  (lines 692–710)

```
async def _require_balance(self, connection: AsyncConnection, model: str | None, agent_id: UUID | None=None) -> None
```

**Purpose**: Stops new child work from starting when the workspace balance cannot cover it. This avoids giving spawned helper turns a free round of model use.

**Data flow**: It receives an open database connection, an optional model name, and optionally the child agent id. It asks BalanceGate whether the workspace is allowed to admit work for that agent and model. If admitted, it returns normally; if rejected, it raises BalanceExhausted with the gate’s message.

**Call relations**: Subagents._admit calls this before first dispatch of a child, and Subagents.message calls it before a new follow-up. It is deliberately placed only on genuinely new work, not on retry paths that reconnect to already-created turns.

*Call graph*: called by 2 (_admit, message); 2 external calls (__init__, __init__).


##### `Subagents._admit`  (lines 712–801)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, delivers_result: bool=False, name: str='', model: str | None=
```

**Purpose**: Creates, or safely reuses, the database rows for a child conversation and its first turn. It also stamps the child with the parent link, acting member, trace information, sandbox choice, and result-delivery setting.

**Data flow**: It receives the chosen conversation id, turn id, child agent id, profile name, input text, and options. Inside a transaction, it inserts the conversation and turn if they do not already exist, checks that an existing deduplicated turn belongs to the same acting member, verifies balance if the turn is still queued, marks it ready for dispatch, and returns true if it should be enqueued.

**Call relations**: Subagents.spawn calls this after resolving and validating the target. It calls Subagents._require_balance and database helpers, and its boolean result tells Subagents.spawn whether Subagents._enqueue needs to run.

*Call graph*: calls 1 internal fn (_require_balance); called by 1 (spawn); 6 external calls (select, update, audience_member, workspace_tx, conversation_name, current_traceparent).


##### `Subagents._enqueue`  (lines 803–832)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Places a queued child or follow-up turn onto the durable work queue. The queue partition is the child conversation, so turns in one child conversation run in order without blocking the parent conversation.

**Data flow**: It receives a turn id and conversation id, builds queue options, and asks DBOS to enqueue the workflow. If enqueueing is cancelled or fails, it clears the dispatch timestamp in the database so another dispatcher can try later; unexpected failures are logged instead of losing the turn.

**Call relations**: Subagents.spawn calls this for a new child, and Subagents.message calls it for a follow-up that is ready to run. It is the bridge from database admission to actual background execution.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 834–850)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Polls until a child turn has a terminal result, meaning it has finished, failed, cancelled, or otherwise ended. If the child is parked on a spend limit, it cancels the child and reports that special failure.

**Data flow**: It receives a child turn id, repeatedly calls Subagents._terminal_or_park, and sleeps briefly between checks. Once a TerminalFrame is available, it returns it; if the child parks, the lower helper raises SubagentParked.

**Call relations**: Subagents.spawn uses this for foreground spawns, and Subagents.wait uses it when waiting for several children. It delegates the actual database inspection and parked-turn handling to Subagents._terminal_or_park.

*Call graph*: calls 1 internal fn (_terminal_or_park); called by 2 (spawn, wait); 1 external calls (sleep).


##### `Subagents._await_terminal_or_detach`  (lines 852–878)

```
async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Waits for a child result, but stops waiting if a member message arrives for the parent conversation. In that case the child is marked for background delivery and keeps running.

**Data flow**: It receives a child turn id, repeatedly checks for a terminal result, then checks whether a member message is waiting and whether the child can be detached. It returns a TerminalFrame if the child finishes first, or null if the child was successfully moved to background delivery.

**Call relations**: Subagents.spawn calls this when detach_on_arrival is requested. It uses Subagents._terminal_or_park to detect completion, Subagents._member_waiting to notice new user input, and Subagents._detach to mark the child so SubagentResult delivery can later wake the parent conversation.

*Call graph*: calls 3 internal fn (_detach, _member_waiting, _terminal_or_park); called by 1 (spawn); 2 external calls (sleep, log).


##### `Subagents._terminal_or_park`  (lines 880–896)

```
async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Checks one child turn for a finished terminal frame or for the special parked state. A parked state means the child stopped without a terminal, so the parent should not wait forever.

**Data flow**: It receives a turn id, reads the turn’s terminal and status from the database, and returns a TerminalFrame if one is stored. If the status is parked, it cancels the child and raises SubagentParked; otherwise it returns null to mean the child is still running.

**Call relations**: Subagents._await_terminal and Subagents._await_terminal_or_detach call this on each polling loop. It centralizes the rule that parked subagents are cancelled instead of being left to rerun into a caller that is no longer waiting.

*Call graph*: called by 2 (_await_terminal, _await_terminal_or_detach); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents._member_waiting`  (lines 898–917)

```
async def _member_waiting(self) -> bool
```

**Purpose**: Checks whether a real workspace member has sent an unread message to the parent conversation. System-generated arrivals, such as child results, do not count.

**Data flow**: It queries inbound messages for the parent workspace and conversation, looking for an unconsumed row admitted from a member. It returns true if such a row exists, otherwise false.

**Call relations**: Subagents._await_terminal_or_detach calls this while deciding whether to stop waiting on a foreground child. This makes detachment happen because of actual user input, not because of the child’s own result delivery or other internal work.

*Call graph*: called by 1 (_await_terminal_or_detach); 2 external calls (select, workspace_tx).


##### `Subagents._detach`  (lines 919–939)

```
async def _detach(self, turn_id: UUID) -> bool
```

**Purpose**: Marks a still-running child so its result will be delivered later instead of returned inline to the waiting parent. It is the handoff from foreground waiting to background delivery.

**Data flow**: It receives a child turn id and updates the row only if the child has no terminal yet and no existing result-delivery marker. It returns true if that marker was added, or false if the child finished first or was already marked.

**Call relations**: Subagents._await_terminal_or_detach calls this after noticing a member message. The update is guarded so exactly one path wins: either the current spawn call returns the child’s terminal inline, or SubagentResult.deliver later posts the result to the parent conversation.

*Call graph*: called by 1 (_await_terminal_or_detach); 2 external calls (update, workspace_tx).


##### `SubagentResult.deliver`  (lines 963–1004)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished background child’s result back into the conversation that spawned it. This lets a parent receive child output as a normal later arrival instead of holding a turn open.

**Data flow**: It receives a completed child Turn. If the child is not marked for pending delivery, it does nothing; if delivery is pending, it loads the parent conversation and agent, loads the child agent row when needed, builds the delivery body, invokes a new arrival with a stable deduplication key, and then marks the child result as delivered.

**Call relations**: This is the delivery side of background spawning and detachment. It calls SubagentResult._body to format the safe message, then uses the TurnInvoker to admit that message into the parent conversation before updating the child’s delivery state.

*Call graph*: calls 1 internal fn (_body); 3 external calls (select, update, workspace_tx).


##### `SubagentResult._body`  (lines 1006–1035)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Builds the text envelope that tells the parent which child answered and what status it ended with. It also wraps untrusted payloads so they are read as content rather than instructions.

**Data flow**: It receives the child turn and, for agent children, the child agent row. It chooses a target label, finds the right output contract, asks SubagentResult._payload for the validated payload and status, optionally wraps the payload with an untrusted-content wall, escapes closing tags inside the payload, and returns the final spawn_result block.

**Call relations**: SubagentResult.deliver calls this immediately before invoking the parent conversation. It calls output_contract for agent schemas, SubagentResult._payload for validation and status shaping, and wall when the child’s answer must be treated as untrusted.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (output_contract, wall).


##### `SubagentResult._payload`  (lines 1037–1056)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a child’s terminal frame into the payload and status that should be delivered to the parent. It refuses to pass along invalid final answers as if they were valid answers.

**Data flow**: It receives an output contract or null and a terminal frame. If the child did not finish successfully, it returns a diagnostic and the terminal status; if the child ended with a question, it returns the question JSON and a question status; if no contract is available, it returns an invalid-profile message; otherwise it validates the terminal text and returns normalized JSON or an invalid-output message.

**Call relations**: SubagentResult._body calls this while assembling the delivered result block. Its contract validation mirrors the foreground spawn path, so background results and awaited results follow the same safety rule.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).


### Browser delegation tools
Adds browser-specific delegation tools for running one or many web-browsing subagent sessions and collecting their results.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file is the bridge between the main agent and the browser automation agent. Instead of giving the main agent direct control of a browser, it asks a specialized “browser” subagent to do the web work. That keeps browser sessions isolated and gives each delegated job clear limits.

The first tool, `browser_task`, is for one full web session, such as searching a site, filling a form, or collecting information across several pages. It starts a browser subagent, waits for it to finish, and cancels it if it runs too long. This matters because websites can hang, loop, or take too long; without the timeout, the parent agent could be stuck waiting forever.

The second tool, `wide_browse`, is for batch work. It reads a workspace file containing URLs or site names, removes blank lines and duplicates, then sends each item to a browser subagent. It limits how many run at once, like opening only a safe number of checkout lanes instead of flooding the store. It then writes all results to `wide_browse.json`.

Both tools use deterministic keys for spawned work, so if a run is retried after a crash, it can reconnect to already-started browser jobs instead of accidentally starting duplicates.

#### Function details

##### `_browser_task`  (lines 98–127)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation job through the browser subagent. It is used when the caller needs a single isolated browser session with a clear time limit.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the starting URL, task instructions, task name, timeout, and user-facing description. It starts a browser subagent with the URL and task details, then waits for that subagent to report back. If the job takes too long or is cancelled, it returns an error-style tool result saying the browser task timed out; if it succeeds, it validates the browser agent’s JSON summary and returns that summary as text.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the main agent calls that tool, this function spawns the browser child turn through `ctx.spawn`, waits through the subagent control interface, and uses `BrowserResult` to turn the child’s final text into a clean result for the parent.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 130–143)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique non-empty lines. It is used by batch browsing to get the list of sites or entities to visit.

**Data flow**: It receives the tool context and a file path. It asks the sandbox to run `cat` on the safely shell-quoted path, checks whether reading succeeded, then trims each line. Blank lines are ignored, duplicates are skipped, and the remaining lines come out as an ordered list.

**Call relations**: This helper is called by `_wide_browse` before any browser jobs are started. It supplies the batch input list that `_wide_browse` later fans out across browser subagents.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 146–173)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs the same kind of browser extraction task across many entities, such as many URLs or company names. It collects the individual browser results and writes one combined JSON output file.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing the entities file, prompt template, output schema file, and user-facing description. It reads and deduplicates the entity list, rejects the request if there are too many items, reads the optional JSON schema text, and creates a limit on how many browser jobs may run at the same time. It then launches one visit per entity, gathers their results, writes the combined rows to `wide_browse.json`, and returns a tool result containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It first relies on `_read_lines` to prepare the work list, then uses its nested `_wide_browse.visit` helper for each entity, and finally gathers all visits together before saving and returning the batch result.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 154–167)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs one browser subagent job for one entity inside a larger batch. It turns a single URL or site name into a browser task and returns one row for the final combined output.

**Data flow**: It receives one entity string from the surrounding `_wide_browse` function. It waits for a slot in the semaphore, fills the prompt template by replacing `{entity}`, appends the output schema if one was provided, and spawns the browser subagent with that task. It returns a dictionary containing the original entity and the browser result as JSON text, or an empty string if there was no output.

**Call relations**: This helper lives inside `_wide_browse` because it depends on that function’s prompt, schema, semaphore, and context. `_wide_browse` runs many of these visits with `asyncio.gather`, while the semaphore keeps the number of simultaneous browser subagents within the configured fan-out limit.

## 📊 State Registers Touched

- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-transcript-history` — The saved conversation transcript and compaction snapshots that all surfaces, workers, prompts, and recovery logic read and update.
- `reg-live-stream-state` — The live update stream that broadcasts turn progress, tool activity, subagent activity, cancellations, and final replies to connected clients.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-browser-site-runtime` — The shared browser sessions, hosted preview servers, public site links, and ownership records used to browse, test, and publish websites.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-extension-workflow-state` — Extension-owned durable workflow records that are not just UI slots, such as code-review inboxes, evaluation runs, objectives, pauses, briefs, notes, monitors, triggers, and web-chat state.
- `reg-active-cancellation-handles` — Process-local abort tokens and cancellation handles that bridge durable cancel requests to currently running turns, tools, sandboxes, and child turns.
