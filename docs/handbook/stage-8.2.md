# Subagent and objective context injection  `stage-8.2`

This stage happens during the main turn loop, before and while the agent does its work. Its job is to give the agent an up-to-date view of what help it can request, and what long-running goals are already in progress. Think of it like resetting the agent’s desk at the start of each turn: the right helper list is placed beside the current checklist.

`spawn_catalog.py` builds that helper list, called a spawn catalog. It is a short instruction page showing which subagents or agent profiles may be started, and what information each one needs. Because workspaces and rules can change, the catalog is rebuilt every turn instead of being reused blindly.

`subagents.py` is the machinery that actually starts and manages those helpers. It can launch a smaller named helper or a full child agent, check that requests and results are valid, cancel work when needed, follow up, and return results safely. Together with injected objective state, this lets the agent delegate, see unfinished steps, record progress, and test whether the goal is done.

## Files in this stage

### Delegation Context
Builds the available subagent catalog for the current turn and uses it to safely spawn, manage, and collect results from child agents.

### `core/src/ufo/loop/spawn_catalog.py`

`domain_logic` · `per turn, before or during delegation`

When an agent wants to delegate work, it needs to know the exact target names it is allowed to use and the shape of the payload it must send. This file creates that guide from two sources: fixed subagent profiles that come from the running system, and workspace agents stored in the database. Building it fresh matters because workspace agents can change over time, and because permissions depend on who the current member is. Without this file, an agent might guess an old target name, send the wrong fields, or try to spawn something it is not allowed to see.

The main function reads the live subagent registry, checks whether the current member is a workspace admin, then queries the workspace’s agent rows that member may spawn. It turns those records into a Markdown table with three columns: target name, kind, and payload fields. If a workspace agent has the same name as a built-in profile, the agent is listed with an `agent:` prefix so there is no confusion about which target spawn will choose. The finished table is wrapped as a `RuntimeSkill`, meaning it becomes a skill-like instruction document that an agent can load before calling spawn. In everyday terms, this file prints the current delegation menu, including who is on the menu and what each order form must contain.

#### Function details

##### `_profile_payload`  (lines 29–36)

```
def _profile_payload(profile: SubagentProfile) -> str
```

**Purpose**: This helper turns a subagent profile’s input model into a short, readable list of payload field names. It marks which fields are required and which are optional so the spawning agent knows what it must provide.

**Data flow**: It receives one subagent profile. It looks at the profile’s declared input fields; if there are none, it returns “(no fields)”. Otherwise, it sorts the field names and returns text such as required fields in backticks and optional fields marked “(optional)”. It does not change anything outside itself.

**Call relations**: The catalog builder uses this when it is adding profile rows to the spawn catalog table. It supplies the human-readable payload column for each built-in subagent profile.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `_schema_payload`  (lines 39–49)

```
def _schema_payload(schema: Mapping[str, object] | None) -> str
```

**Purpose**: This helper turns a workspace agent’s stored input schema into a readable list of payload field names. A schema is a machine-readable description of expected input; this function makes it understandable for the agent reading the catalog.

**Data flow**: It receives either a schema dictionary or no schema at all. If there is no schema, it falls back to the standard `TaskInput` fields. If the schema has no useful properties, it returns “(no fields)”. Otherwise, it reads the property names, checks which ones are listed as required, and returns a sorted text list that marks optional fields clearly.

**Call relations**: The catalog builder uses this while adding workspace agent rows to the table. It translates each database row’s input schema into the payload guidance shown in the final runtime skill.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `spawn_catalog_skill`  (lines 52–103)

```
async def spawn_catalog_skill(registry: SubagentRegistry, member_id: UUID | None) -> RuntimeSkill
```

**Purpose**: This is the main builder for the spawn catalog skill. It gathers the spawn targets available right now, formats them as instructions, and returns a `RuntimeSkill` that agents can load before deciding what to spawn.

**Data flow**: It receives the live subagent registry and the current member’s ID, if there is one. It collects profile names from the registry, opens a workspace database transaction, checks whether the member is an admin, and reads the workspace agent rows the member is allowed to spawn. It then builds a Markdown table from profiles and agents, using helper functions to describe payload fields. The output is a `RuntimeSkill` containing the catalog name, description, instructions, and raw Markdown text.

**Call relations**: This function sits beside the actual spawn dispatch path so the catalog and spawn use the same underlying records. During a turn, higher-level loop code can call it to create the skill an agent reads when it needs to delegate. It calls the two local formatting helpers for payload descriptions, asks the workspace and permission helpers for current context, queries the database through SQLAlchemy, and finally hands the finished text to `RuntimeSkill`.

*Call graph*: calls 2 internal fn (_profile_payload, _schema_payload); 5 external calls (__init__, select, workspace_tx, member_is_admin, ws_current).


### `core/src/ufo/loop/subagents.py`

`orchestration` · `request handling and background result delivery`

This file is the project’s control room for subagents. A subagent is like sending a helper to do a smaller job while the main agent continues its own work. Without this file, agents could not reliably delegate work, wait for it, resume it, or receive finished answers without risking duplicate jobs, permission leaks, or unsafe model text being treated as trusted instructions.

There are two kinds of spawn targets. A profile is a predefined helper recipe: it has a prompt, a set of tools, and expected input and output shapes. A workspace agent is a real agent record in the database, with its own owner, prompt, tools, and security settings. The code resolves the target name, checks permissions, validates the payload before starting, creates a new conversation and turn in the database, and puts that turn on the work queue.

Foreground spawns wait until the child finishes and then validate its final answer. Background spawns return the child turn id immediately, and the result is later posted back into the parent conversation. The file is careful about retry safety: if a step is replayed after a crash, a deduplication key can reconnect to the same child instead of creating a second one. It is also careful about trust. Output from web-facing or unknown helpers can be wrapped in a protective “wall” so the parent sees it as content, not as instructions.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 116–120)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the configured subagent profiles do not reuse the same name. This matters because a spawn name must point to exactly one helper recipe.

**Data flow**: It reads the profile list stored in the registry, collects their names, looks for repeats, and either leaves the registry usable or raises an error naming the duplicates.

**Call relations**: This runs automatically when a registry is created. It protects later lookups, so calls that fetch a profile do not have to guess which duplicate should win.


##### `SubagentRegistry.get`  (lines 122–128)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Returns a registered subagent profile by name, or raises a clear project-specific error if it is missing. Use this when absence is a configuration problem, not a normal possibility.

**Data flow**: It receives a profile name, asks the registry to find it, and returns the profile if found. If not found, it builds an error that includes the available profile names.

**Call relations**: It relies on `SubagentRegistry.find` for the actual search. Other startup or queue code can call it when resolving a profile and wants failure to be loud and informative.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 130–131)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Looks up a subagent profile by name and returns nothing if it is not present. This is the quiet lookup used when missing is allowed and the caller will decide what to do.

**Data flow**: It receives a name, scans the registry’s tuple of profiles, and returns the first profile with that name. If no profile matches, it returns `None`.

**Call relations**: It is the shared search helper behind stricter profile lookup. It is also used by result and trust logic when an old child refers to a profile that may no longer be registered.

*Call graph*: called by 1 (get).


##### `subagent_system_prompt`  (lines 146–177)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the system prompt, meaning the main instruction text, for a profile-based subagent. It combines the profile’s own instructions with skill information and strict rules for how the child must finish.

**Data flow**: It takes a profile, an optional list of skill names and descriptions, and optional preloaded skill bodies. It fills the skill index placeholder, checks that no template slots were left unresolved, appends preloaded skill text if it is not too large, and returns the final prompt ending with the required finish-tool contract.

**Call relations**: This function prepares the instructions that the child model will later run under. It calls prompt-rendering helpers for the skill index and skill-loading helpers for preloaded content, while enforcing guardrails before the prompt reaches the model.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 191–192)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: Returns a copy of the `Subagents` controller that acts with a specific member’s authority. This is useful when the spawning turn must be tied to the human or member who requested it.

**Data flow**: It receives a member id or `None`, copies the current object, and changes only the requester member field. The original object is unchanged.

**Call relations**: Later permission checks read this chosen requester through `Subagents.acting_member_id`. It is a small setup step before spawn, message, cancel, or result operations need an authority identity.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 195–204)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Figures out which workspace member the spawn should be treated as acting for. This keeps ownership and permission checks consistent.

**Data flow**: It reads, in order, the explicitly authorized requester, the parent turn’s speaker, and the parent turn’s on-behalf-of member. It returns the first one that exists, or `None` if none are known.

**Call relations**: Permission checks and child-turn database stamps use this value. It is the common identity thread tying spawn admission, ownership checks, and later child access together.


##### `Subagents.spawn`  (lines 206–296)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='') -> SpawnResult
```

**Purpose**: Starts a child turn for either a subagent profile or a workspace agent. It can either wait for the child’s validated answer or return immediately with the child’s id.

**Data flow**: It receives a target name, input payload, and options such as background mode and deduplication key. It resolves the target, checks permissions for workspace agents, validates the input against the target’s input contract, creates a child conversation and turn, enqueues it, and then either returns immediately or waits for the terminal result and validates the output.

**Call relations**: This is the main public path for delegation. It hands target lookup to `_resolve`, permission checks to `_may_spawn`, database admission to `_admit`, queue dispatch to `_enqueue`, and foreground waiting to `_await_terminal`; it returns a `SpawnResult` for the caller to use.

*Call graph*: calls 5 internal fn (_admit, _await_terminal, _enqueue, _may_spawn, _resolve); 7 external calls (__init__, __init__, input_contract, output_contract, turn_id_for, uuid4, uuid5).


##### `Subagents.result`  (lines 298–335)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the final result of a child that this conversation spawned. It gives host code a safe, structured way to inspect a finished child without relying on the parent model’s wording.

**Data flow**: It receives a child turn id, verifies that the turn belongs to this spawning conversation, reads the child’s terminal record from the database, chooses the right output contract, and tries to validate the terminal text. It returns a `SpawnResult` with the parsed output when valid, or no output when the child failed, asked a question, or produced invalid data.

**Call relations**: It first calls `_require_child` to enforce ownership. For workspace-agent children it asks `_agent_output_schema` for the contract, and it uses `_untrusted_output` to mark whether the output should be treated cautiously.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, output_contract, workspace_tx).


##### `Subagents.wait`  (lines 337–357)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits until one or more child turns have finished and returns their final statuses and text. This is for tool calls that truly need to pause until a child reaches an end state.

**Data flow**: It receives child turn ids, verifies each belongs to this conversation, waits for each terminal record to appear, and returns a tuple of status objects containing the final status, text, and trust marker.

**Call relations**: It uses `_require_child` before waiting so a caller cannot wait on someone else’s child. It then uses `_await_terminal` for polling and `_untrusted_output` to label the returned text.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 359–376)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a running child turn that this conversation spawned. This gives the parent a controlled way to stop delegated work.

**Data flow**: It receives a child turn id, verifies ownership, asks the shared cancellation helper to cancel the durable workflow and commit a cancelled terminal, then reads the latest status and terminal text from the database. It returns a status object for the cancelled or already-finished child.

**Call relations**: It relies on `_require_child` for access control and on the system-wide `cancel_one_turn` helper for the actual cancellation. After that, it reports the database’s final view of the child.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 378–497)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child by creating the next turn in that child’s conversation. This lets a parent answer a child’s question or continue a child without starting over.

**Data flow**: It receives the original child turn id, follow-up text, a deduplication key, and a result-delivery option. It verifies the child, checks that profile children still have a known profile, locks the child conversation, finds or creates the next turn using the deduplication key, decides whether it should be dispatched now, enqueues it if appropriate, and returns the follow-up turn status.

**Call relations**: It uses `_require_child` to ensure the caller may address this child. If it creates a queued follow-up and no earlier queued turn blocks it, it hands dispatch to `_enqueue`.

*Call graph*: calls 2 internal fn (_enqueue, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._resolve`  (lines 499–522)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Turns a spawn target string into the actual thing to run: either a profile or a workspace agent. It also catches unclear names before they can launch the wrong target.

**Data flow**: It receives a target such as `profile:name`, `agent:name`, or a bare name. It checks the profile registry and the agent table as needed, returns the matching profile or agent target, raises an ambiguity error if both match a bare name, or raises an unknown-target error with useful choices.

**Call relations**: `Subagents.spawn` calls this before any child is admitted. It uses `_profile_names`, `_agent_names`, and `_agent_target` to build both the answer and clear failure messages.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._profile_names`  (lines 524–525)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of all registered subagent profiles in sorted order. This is mainly used to explain what profile targets are available.

**Data flow**: It reads the registry’s profiles, extracts their names, sorts them, and returns them as a tuple.

**Call relations**: `_resolve` uses this when it needs to report an unknown or qualified target. It helps make spawn errors understandable instead of vague.

*Call graph*: called by 1 (_resolve).


##### `Subagents._agent_names`  (lines 527–536)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of workspace agents that could be named as spawn targets. This helps produce helpful target-resolution errors.

**Data flow**: It opens a workspace database transaction, selects agent names for the parent turn’s workspace, orders them by name, and returns them as a tuple.

**Call relations**: `_resolve` calls this when it needs to tell the caller which agent names exist. It reads from the database because workspace agents are not part of the in-memory profile registry.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 538–562)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Looks up one workspace agent by name and packages the facts needed to spawn it. These facts include its id, owner, and input/output schemas.

**Data flow**: It receives an agent name, queries the workspace agent table for that name in the parent workspace, and returns an `AgentTarget` if found. If no matching row exists, it returns `None`.

**Call relations**: `_resolve` uses this to decide whether a spawn target names a workspace agent. The returned data later lets `spawn` validate input, choose the child agent identity, and check ownership.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 564–570)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Fetches the declared output schema for a workspace agent. This is needed when validating the result of an agent child after it has already run.

**Data flow**: It receives an agent id, reads the matching agent row from the database, and returns that agent’s output schema, which may be absent.

**Call relations**: `Subagents.result` calls this for agent children because their contract comes from the database, not from a registered profile.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._may_spawn`  (lines 572–584)

```
async def _may_spawn(self, owner_member_id: UUID | None) -> bool
```

**Purpose**: Checks whether the acting member is allowed to spawn a workspace agent. Owners can run their own agents, and workspace admins can run any agent.

**Data flow**: It receives the target agent’s owner member id. If the acting member is the owner, it returns true; if there is no acting member, it returns false; otherwise it asks the database-backed admin check and returns that answer.

**Call relations**: `Subagents.spawn` calls this only for workspace-agent targets. It keeps chat-time spawning aligned with the permissions used elsewhere in the workspace.

*Call graph*: called by 1 (spawn); 2 external calls (workspace_tx, member_is_admin).


##### `Subagents._untrusted_output`  (lines 586–595)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child’s output should be treated as untrusted content. Untrusted means the text should be fenced off so it cannot masquerade as instructions to the parent.

**Data flow**: It receives a profile name or `None` for workspace-agent children. Agent children are always marked untrusted; profile children are marked untrusted if the profile is missing or if the profile declares untrusted output.

**Call relations**: `result` and `wait` use this when reporting child output. Delivery formatting uses similar trust rules so parent conversations receive risky content safely.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 597–633)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Verifies that a turn id really belongs to a child spawned by this conversation and by the same acting member. It prevents one conversation or member from controlling another’s child work.

**Data flow**: It receives a turn id, reads the turn’s parent link, profile, and acting member stamp from the database, and checks whether it was spawned by this parent turn or a sibling turn in the same conversation. If the checks pass, it returns the child’s profile name or `None`; otherwise it raises an error.

**Call relations**: Public operations that inspect, wait for, cancel, or message a child call this first. It is the access-control gate for child-turn operations after spawn.

*Call graph*: called by 4 (cancel, message, result, wait); 2 external calls (select, workspace_tx).


##### `Subagents._admit`  (lines 635–722)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, delivers_result: bool=False, name: str='') -> bool
```

**Purpose**: Creates the database records for a child conversation and its first turn, or safely reconnects to existing records during a retry. This is the durable “admission desk” before queueing work.

**Data flow**: It receives ids, agent/profile information, sandbox choice, inbound JSON, delivery settings, and an optional display name. It inserts a conversation and queued turn if they do not already exist, checks that an existing deduplicated turn belongs to the same acting member, marks queued work as ready for dispatch, and returns whether it should be enqueued.

**Call relations**: `spawn` calls this after target resolution and input validation. If it returns true, `spawn` proceeds to `_enqueue`; if it returns false, the child was already admitted or no longer queued.

*Call graph*: called by 1 (spawn); 6 external calls (select, update, audience_member, workspace_tx, conversation_name, current_traceparent).


##### `Subagents._enqueue`  (lines 724–753)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Submits a queued child turn to the durable workflow queue. If enqueueing fails, it clears the dispatch marker so another process can try later.

**Data flow**: It receives a turn id and conversation id, builds queue options including workflow name, workflow id, and queue partition, and asks the DBOS client to enqueue the work. On cancellation or error, it updates the turn so it is no longer marked as already dispatched; ordinary errors are logged instead of crashing the caller.

**Call relations**: `spawn` uses this for the first child turn, and `message` uses it for follow-up turns. It is the bridge from database admission to actual background execution.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 755–765)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has written its terminal result. A terminal result is the final record saying whether the turn finished, failed, cancelled, or asked a question.

**Data flow**: It receives a turn id, repeatedly reads the turn’s terminal field from the database, and sleeps briefly when it is still empty. Once present, it validates the stored data as a `TerminalFrame` and returns it.

**Call relations**: Foreground `spawn` and explicit `wait` use this polling loop. It is intentionally simple: it watches the durable database record that the child workflow eventually commits.

*Call graph*: called by 2 (spawn, wait); 4 external calls (sleep, model_validate, select, workspace_tx).


##### `SubagentResult.deliver`  (lines 789–830)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished background child’s result back into the parent conversation. This lets the parent receive the child answer as a normal incoming message instead of holding a turn open.

**Data flow**: It receives a completed child turn. If the child is not pending delivery, it does nothing; if it is pending but has no terminal, it raises an error. Otherwise it reads the parent conversation and, for agent children, the agent row; builds a safe result body; invokes the parent conversation with a deduplicated arrival key; then marks the child result as delivered.

**Call relations**: This is the delivery path for background spawns. It delegates message formatting to `_body` and uses the turn invoker to admit the result into the parent conversation before stamping delivery complete.

*Call graph*: calls 1 internal fn (_body); 3 external calls (select, update, workspace_tx).


##### `SubagentResult._body`  (lines 832–861)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Builds the text envelope that represents a child’s delivered result. The envelope names the target, the spawn id, and the result status so the parent can understand what arrived.

**Data flow**: It receives the child turn and, for agent children, the agent row. It chooses the output contract, decides whether to wrap the payload as untrusted content, asks `_payload` for the actual status and body text, escapes any fake closing tag inside the payload, and returns the final `<spawn_result>` block.

**Call relations**: `deliver` calls this just before invoking the parent conversation. It calls `_payload` for contract-aware result extraction and uses the untrusted-content wall when the child’s output should not be treated as direct instructions.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (output_contract, wall).


##### `SubagentResult._payload`  (lines 863–882)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a child terminal record into the payload text and status used in a delivered result. It makes failures, questions, missing contracts, and invalid output explicit.

**Data flow**: It receives an optional output contract and a terminal frame. If the terminal is not done, it returns an error diagnostic and that status; if the child asked a question, it returns the structured question; if no contract is available, it returns an invalid-profile message; otherwise it validates the terminal text against the contract and returns clean JSON or an invalid-output message.

**Call relations**: `_body` calls this while assembling the result envelope. This function is where delivery gets the same contract-checking discipline as foreground spawn results.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).
