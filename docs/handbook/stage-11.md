# Delegation, subagents, and multi-agent pipelines  `stage-11`

This stage is the system’s way of handing off work during the main conversation. When the main agent sees a task that needs a specialist, it can start a child agent, or subagent, much like asking a coworker to handle one part of a project. The contracts file defines the agreed “forms” for inputs and outputs, so each helper receives and returns data in the expected shape. The core subagents file checks those forms, decides which helpers are allowed, starts the child work, waits when needed, and returns the result safely. The core profiles file supplies a default helper when no extension provides one.

The extensions add specialized workers. Browser files define browser-focused helpers and tools for one or many web sessions. Document files define a prose-writing helper. Research files define normal, deep, and wide research flows, including saving partial results if interrupted. Site files define a website-building helper that leaves finished files in the original workspace. The brief pipeline is a three-step assembly line: outline, draft, then critique.

## Files in this stage

### Core subagent framework
Defines the default helper profile, the runtime machinery for spawning child agents, and the shared contract layer for validating delegated inputs and outputs.

### `core/src/ufo/runtime/profiles.py`

`config` · `subagent setup and spawn dispatch`

This file is the default recipe for creating a general-purpose helper agent. Think of it like a job description handed to a temporary assistant: it says what the assistant is allowed to do, what it must not do, what kind of task it receives, and what kind of answer it should return.

The profile is called `general_purpose`. It is meant for focused, delegated work inside the same shared workspace as the parent agent. The helper can read and edit files, run shell commands, search, fetch web pages, load skills, share files, and use some optional extension tools if those extensions are installed. At the same time, it is deliberately restricted from actions that would make it act like a coordinator: it cannot ask the user questions, spawn more agents, message or cancel sibling agents, or approve account connections.

The prompt gives the subagent practical rules: work independently, make reasonable assumptions, avoid repeating failed actions, load relevant skills first, use proper Office file formats for formal document deliverables, and save useful artifacts in `/workspace` with clear names.

Finally, the file packages all of this into a `SubagentProfile` object, using `TaskInput` as the expected task format and `ResultOutput` as the expected result format. Other profiles can come from extensions, but this one is always available as the fallback.


### `core/src/ufo/runtime/subagents.py`

`orchestration` · `request handling`

This file is the runtime machinery behind the `spawn` idea: an agent can ask a helper agent or a named helper profile to do work. Without it, the system would have no safe way to start helper turns, avoid duplicate helper runs after retries, enforce ownership and billing rules, or return the helper’s answer in a form the parent can trust.

There are two kinds of targets. A profile is a predeclared helper recipe: it has a prompt, allowed tools, and input/output contracts. A workspace agent is a real agent row from the database with its own identity and settings. The file resolves a requested name across those two worlds, validates the payload against the target’s expected input, creates a child conversation and first turn, and puts that turn onto the fast queue.

Foreground spawns wait for the child to finish and then validate its final answer. Background spawns return the child turn id immediately and later deliver a structured result back to the parent conversation. If a user message arrives while the parent is waiting, the wait can detach, like sending a shell command to the background: the child keeps running, and the parent can respond to the user.

The file also guards important boundaries: who is allowed to spawn an agent, whether prepaid balance is enough, whether a requested model is allowed, and whether returned text should be treated as untrusted content.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 146–150)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the list of registered subagent profiles has no duplicate names. This prevents one spawn name from secretly meaning two different helper recipes.

**Data flow**: It reads the profile names stored in the registry → looks for names that appear more than once → either leaves the registry usable or raises an error before the system can run with an unclear setup.

**Call relations**: This runs automatically when a `SubagentRegistry` is created. Later lookups depend on this guarantee so `get` and `find` can treat a name as pointing to at most one profile.


##### `SubagentRegistry.get`  (lines 152–158)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Returns the profile with a given name, and raises a clear error if there is no such profile. Use this when missing profiles should stop the operation immediately.

**Data flow**: It receives a profile name → asks `SubagentRegistry.find` to search the registry → returns the profile if found, or raises `UnknownSubagentProfile` with the available profile names if not.

**Call relations**: The queue profile resolver calls this when it must turn a stored profile name into the actual profile definition. It builds on `find`, but adds the user-facing failure path.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 160–161)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Looks up a profile by name and returns nothing if it is absent. Use this when the caller wants to decide what to do about a missing profile.

**Data flow**: It receives a name → scans the registry’s tuple of profiles → returns the matching profile or `None`.

**Call relations**: `SubagentRegistry.get` uses this as its quiet lookup step before deciding whether to raise an error.

*Call graph*: called by 1 (get).


##### `_target_model`  (lines 175–183)

```
def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None
```

**Purpose**: Finds the model pinned by a spawn target, if the target itself names one. A model is the AI engine choice used to run the child.

**Data flow**: It receives either a profile target or an agent target → if it is a profile, it returns that profile’s model setting → if it is an agent target, it returns `None` because agent model choice is read elsewhere from the agent row.

**Call relations**: `Subagents.spawn` uses this when admitting a child so billing and later execution are weighed against the model that will actually answer.

*Call graph*: called by 1 (spawn).


##### `subagent_system_prompt`  (lines 186–222)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the full instruction text given to a profile-based subagent. It combines the profile’s prompt, optional skill information, safety/output rules, and the final-answer contract.

**Data flow**: It receives a profile plus optional skill listings and preloaded skill bodies → fills the skill index slot, checks that no template slots were left unresolved, optionally appends preloaded skill text within a size limit, and appends strict finishing instructions → returns the complete system prompt string.

**Call relations**: It relies on prompt rendering helpers to make the skill index and on skill runtime helpers to include preloaded skill text. The resulting prompt is what profile children use so they know both what to do and how to return a machine-checkable answer.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 248–257)

```
def authorize(self, authority: ExecutionAuthority) -> 'Subagents'
```

**Purpose**: Creates a copy of the spawner bound to the authority of a specific tool call. Authority means whose permissions and ownership are being used.

**Data flow**: It receives an authority object → if that authority is a member authority, it uses it; otherwise it falls back to the parent turn’s authority → returns a new `Subagents` object with that authority set.

**Call relations**: Tool calls use this before spawning so the child is stamped with the same requester that the ownership checks, catalog access, and audit trail expect.

*Call graph*: 1 external calls (replace).


##### `Subagents.spawn`  (lines 259–413)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False, model: str | N
```

**Purpose**: Starts a child agent turn. It can run the child in the foreground and return its validated answer, or run it in the background and return the child id right away.

**Data flow**: It receives a target name, input payload, and options such as background mode, deduplication key, delivery behavior, and model pin → prepares child runtime settings, resolves the target, validates the payload, creates or reuses the child turn, enqueues it, then either returns immediately or waits for a terminal result → returns a `SpawnResult` containing ids, output when available, and terminal details when relevant.

**Call relations**: This is the main public path. It calls helpers to resolve targets, validate input, admit rows, enqueue the workflow, wait for completion, detach on user arrival, and validate final output. If the child fails while being awaited, it cancels the child turn so the parent is not left tied to abandoned work.

*Call graph*: calls 11 internal fn (_admit, _await_terminal, _await_terminal_or_detach, _child_runtime_config, _enqueue, _existing_agent_spawn, _resolve, _validated, _target_model, own_account (+1 more)); 11 external calls (__init__, __init__, sha256, dumps, cancel_one_turn, input_contract, output_contract, ws_current, turn_id_for, uuid4 (+1 more)).


##### `Subagents.result`  (lines 415–452)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the final result of a child that this conversation spawned. It is for callers that already have a child turn id and want the stored, validated result.

**Data flow**: It receives a child turn id → verifies the turn belongs to this spawning conversation, loads the child terminal record from the database, finds the right output contract, and tries to validate the stored final text → returns a `SpawnResult` with output if validation succeeds, otherwise with the terminal but no trusted output.

**Call relations**: It uses `_require_child` to enforce ownership, `_agent_output_schema` for agent-child contracts, and `_untrusted_output` to mark whether the answer should be treated as potentially unsafe. It does not rerun the child; it only reads durable state.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, workspace_tx, output_contract).


##### `Subagents.wait`  (lines 454–474)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits until one or more child turns finish and reports their statuses and final text. This is for tools that need to block briefly for helper work rather than receive a later delivery.

**Data flow**: It receives child turn ids → confirms each one is a child of this conversation → waits for each terminal record → returns a tuple of `SubagentStatus` values with status, text, and trust marking.

**Call relations**: It uses `_require_child` before waiting and `_await_terminal` for the actual wait. It also uses `_untrusted_output` so consumers know whether a child’s text should be treated as untrusted content.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 476–493)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a running child turn that belongs to this parent conversation. It lets the parent stop work it no longer wants.

**Data flow**: It receives a child turn id → checks that the id is really an allowed child → asks the shared cancellation helper to cancel it → reads the child’s latest status and terminal text from the database → returns a `SubagentStatus`.

**Call relations**: It uses `_require_child` as the access gate and the shared `cancel_one_turn` primitive for the actual cancellation. The returned status lets the caller report what happened after the cancel request.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents.message`  (lines 495–610)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child so it can continue its own conversation. This is how a parent answers a child that asked a question or gives more instructions to a still-running helper.

**Data flow**: It receives the original child turn id, text, a deduplication key, and delivery options → verifies the child, checks the profile still exists when needed, locks the child conversation, reuses an existing follow-up if the dedup key was already used, or creates the next turn with the given text → enqueues the follow-up if it is newly queued → returns its status.

**Call relations**: It calls `_require_child` to verify access, `_profile_model` and `_require_balance` before admitting new paid work, and then hands queued work to the dispatcher. Its deduplication behavior lets crash recovery reconnect to the same follow-up instead of creating a duplicate.

*Call graph*: calls 3 internal fn (_profile_model, _require_balance, _require_child); 9 external calls (__init__, model_validate, insert, select, workspace_tx, current_traceparent, authority_member_id, dispatch_next_turn, turn_id_for).


##### `Subagents._resolve`  (lines 612–635)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Turns a spawn target string into either a registered profile or a workspace agent. It also catches unclear names before the wrong helper starts.

**Data flow**: It receives a target such as `name`, `profile:name`, or `agent:name` → checks the profile registry and the workspace agents table as appropriate → returns the resolved target object or raises a clear unknown or ambiguous target error.

**Call relations**: `Subagents.spawn` calls this before admitting a child. It uses `_profile_names`, `_agent_names`, and `_agent_target` so error messages can tell the caller what names are actually available.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._validated`  (lines 637–650)

```
def _validated(self, target: str, contract: Contract, payload: dict[str, Any]) -> str
```

**Purpose**: Checks that the payload being sent to a child matches the child’s declared input contract. A contract is a schema that says what fields and types are allowed.

**Data flow**: It receives the target name, the input contract, and the raw payload dictionary → validates and serializes the payload to JSON if correct → raises `SpawnPayloadRejected` with readable field-level problems if not.

**Call relations**: `Subagents.spawn` uses this after resolving the target and before admitting the child turn. This keeps bad inputs from becoming queued work that would fail later in a harder-to-explain way.

*Call graph*: calls 1 internal fn (__init__); called by 1 (spawn); 2 external calls (model_validate, payload_keys).


##### `Subagents._existing_agent_spawn`  (lines 652–691)

```
async def _existing_agent_spawn(self, turn_id: UUID, target: str) -> tuple[AgentTarget, str] | None
```

**Purpose**: Detects whether a deterministic agent spawn was already admitted by an earlier run. This supports safe retries after crashes or workflow replay.

**Data flow**: It receives the child turn id that would be produced by the deduplication key and the requested target → looks up any existing turn and joined agent row → checks that it belongs to the same parent, same requester, and same agent target → returns the existing `AgentTarget` plus stored inbound text, or `None` if there is no matching agent spawn.

**Call relations**: `Subagents.spawn` calls this only when a deduplication key is present. If it returns a replay record, spawn reuses the original child rather than validating and admitting a fresh one.

*Call graph*: called by 1 (spawn); 4 external calls (__init__, select, workspace_tx, authority_member_id).


##### `Subagents._profile_names`  (lines 693–694)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the sorted names of all registered subagent profiles. It is mainly used to make errors helpful.

**Data flow**: It reads the registry’s profiles → sorts their names → returns them as a tuple.

**Call relations**: `_resolve` and `_require_agent_spawn` use this when reporting unknown targets, so callers can see which profile names are valid.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `Subagents._agent_names`  (lines 696–708)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the sorted names of active workspace agents that can be considered as spawn targets.

**Data flow**: It opens a workspace database transaction → selects non-archived agents in the parent’s workspace ordered by name → returns their names as a tuple.

**Call relations**: `_resolve` uses this when it needs to explain that a requested target was unknown and show what agent names are available.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 710–733)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Looks up an active workspace agent by name and packages the facts needed to spawn it.

**Data flow**: It receives an agent name → queries the workspace database for a non-archived agent with that name → returns an `AgentTarget` with id, name, input schema, and output schema, or `None` if absent.

**Call relations**: `_resolve` calls this while deciding whether a target name refers to an agent, a profile, both, or neither.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 735–741)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Fetches the output schema for a workspace agent. This is needed to validate the stored result of an agent child.

**Data flow**: It receives an agent id → reads that agent’s output schema from the database → returns the schema dictionary or `None` if the agent uses the default contract.

**Call relations**: `Subagents.result` calls this when the child was an agent rather than a profile, because agent children do not have profile output contracts.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._untrusted_output`  (lines 743–752)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child’s output should be treated as untrusted content. Untrusted content is walled off so the parent does not accidentally treat arbitrary text as instructions.

**Data flow**: It receives a profile name or `None` for an agent child → returns `True` for agent children, missing profiles, or profiles marked as untrusted → returns `False` only for a known profile that declares trusted output.

**Call relations**: `Subagents.result` and `Subagents.wait` call this before returning status or result information. This keeps trust decisions consistent for stored results and waits.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 754–790)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Verifies that a given turn id is a child spawned by this conversation and by the same requester. It prevents one member or conversation from controlling another’s child work.

**Data flow**: It receives a turn id → reads the turn’s parent id, profile name, and requester from the database → allows either the exact parent turn or a sibling turn in the same conversation where the original parent spawned it → returns the child profile name, or `None` for an agent child, if allowed; otherwise raises an error.

**Call relations**: `cancel`, `message`, `result`, and `wait` all call this before touching a child. It is the shared access gate for later operations on spawned work.

*Call graph*: called by 4 (cancel, message, result, wait); 3 external calls (select, workspace_tx, authority_member_id).


##### `Subagents._profile_model`  (lines 792–798)

```
def _profile_model(self, profile: str | None) -> str | None
```

**Purpose**: Finds the model pinned by a named profile, if that profile still exists. It is used for billing follow-up work on profile children.

**Data flow**: It receives a profile name or `None` → searches the current registry → returns the profile’s model setting if found, otherwise `None`.

**Call relations**: `Subagents.message` calls this before creating a follow-up turn, so balance checks can be based on the model the continued child is expected to use.

*Call graph*: called by 1 (message).


##### `Subagents._child_runtime_config`  (lines 800–822)

```
def _child_runtime_config(self, model: str | None) -> TurnRuntimeConfig | None
```

**Purpose**: Builds the runtime configuration for a child turn, especially when the caller asks to pin the child to a particular model. It rejects model choices that would conflict with the parent’s already pinned model or with the deployment’s known models.

**Data flow**: It receives an optional model id → if no model is requested, returns the parent’s runtime config unchanged → if the parent tree already has a model pin, raises a rejection → if the requested model is not served by this deployment, raises a rejection → otherwise returns a runtime config with that model set.

**Call relations**: `Subagents.spawn` calls this before writing any child rows. That early check lets the caller fix a bad model argument instead of leaving a child turn that can never start correctly.

*Call graph*: calls 2 internal fn (pinned_tree, unknown); called by 1 (spawn); 1 external calls (model_validate).


##### `Subagents._require_balance`  (lines 824–843)

```
async def _require_balance(self, connection: AsyncConnection, model: str | None, agent_id: UUID | None=None) -> None
```

**Purpose**: Checks that the workspace has enough prepaid balance to start new child work. This prevents helper turns from becoming unpaid extra work.

**Data flow**: It receives a database connection, the model to charge against, and optionally the child agent id → asks `BalanceGate` whether the workspace may run that model for that agent → returns nothing if admitted, or raises `BalanceExhausted` with the refusal message.

**Call relations**: `_admit` calls this for newly admitted children, and `message` calls it for new follow-up turns. Existing deduplicated work skips this because it was already admitted earlier.

*Call graph*: called by 2 (_admit, message); 2 external calls (__init__, __init__).


##### `Subagents._admit`  (lines 845–973)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, request_fingerprint: str, delivers_result: bool=False, name:
```

**Purpose**: Creates the database records for a child conversation and its first turn, or safely reconnects to records that already exist. This is the durable admission step before queueing.

**Data flow**: It receives child ids, agent/profile information, serialized inbound payload, delivery settings, runtime config, and billing model → locks any existing turn, checks agent-spawn permissions when needed, inserts the conversation and turn if absent, verifies that any existing row matches the same request and requester, checks balance for new work, marks the turn as ready for dispatch → returns `True` if it should be enqueued and `False` if it was already past the queued state.

**Call relations**: `Subagents.spawn` calls this after validation. It calls `_require_agent_spawn` for workspace-agent permission checks and `_require_balance` for payment gating, then leaves `_enqueue` to place admitted work on the workflow queue.

*Call graph*: calls 2 internal fn (_require_agent_spawn, _require_balance); called by 1 (spawn); 8 external calls (model_dump, select, update, workspace_tx, current_traceparent, authority_member_id, conversation_name, audience_member).


##### `Subagents._require_agent_spawn`  (lines 975–1026)

```
async def _require_agent_spawn(self, connection: AsyncConnection, target: AgentTarget, inbound: str) -> None
```

**Purpose**: Checks whether the current requester is allowed to spawn a workspace agent. A user may spawn their own agent, and workspace admins may spawn others.

**Data flow**: It receives a locked database connection, the target agent, and the validated inbound JSON → reloads the agent row, rejects missing or archived agents, checks owner/admin permission, and validates the inbound JSON against the agent’s current input schema → returns nothing if allowed, otherwise raises a clear error.

**Call relations**: `_admit` calls this only for new agent-child admissions. It uses `_profile_names` when reporting an unknown target and uses member/admin helpers to enforce workspace ownership rules.

*Call graph*: calls 2 internal fn (_profile_names, __init__); called by 1 (_admit); 5 external calls (execute, select, authority_member_id, member_is_admin, input_contract).


##### `Subagents._enqueue`  (lines 1028–1056)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Places an admitted child turn onto the express workflow queue. The express queue is used so child work can start even when parent turns are waiting.

**Data flow**: It receives the child turn id and conversation id → builds DBOS queue options using the turn workflow name and id → asks the DBOS client to enqueue the work → if enqueueing is cancelled or fails, clears the dispatch timestamp so another dispatcher can try later, and logs failures that should be retried later.

**Call relations**: `Subagents.spawn` calls this only when `_admit` says the turn is queued and needs dispatch. It does not run the child itself; it hands the child off to the workflow system.

*Call graph*: called by 1 (spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 1058–1101)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has written its terminal result. A terminal result is the durable record that says the turn is done, failed, cancelled, or otherwise ended.

**Data flow**: It receives a child turn id → retrieves the DBOS workflow handle when available and waits for its result → after workflow completion or errors, checks the database for a terminal or parked state → follows a newer running attempt if the workflow was retried → returns the `TerminalFrame` once committed, or raises if the workflow ended without a terminal.

**Call relations**: `spawn`, `wait`, and `_await_terminal_or_detach` use this as the basic wait primitive. It calls `_terminal_or_park` to read durable turn state and `_running_attempt` to handle workflow retry attempts.

*Call graph*: calls 2 internal fn (_running_attempt, _terminal_or_park); called by 3 (_await_terminal_or_detach, spawn, wait); 1 external calls (sleep).


##### `Subagents._running_attempt`  (lines 1103–1109)

```
async def _running_attempt(self, turn_id: UUID) -> str | None
```

**Purpose**: Reads the workflow id of the currently running attempt for a turn, if one is recorded. This helps waiting code follow retries.

**Data flow**: It receives a turn id → queries the turn row’s `running_attempt` field → returns that workflow id string or `None`.

**Call relations**: `_await_terminal` calls this when the workflow it was watching is missing or finished without a terminal, so it can switch to the active attempt instead of giving up too early.

*Call graph*: called by 1 (_await_terminal); 2 external calls (select, workspace_tx).


##### `Subagents._await_terminal_or_detach`  (lines 1111–1166)

```
async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Waits for a child result, but stops waiting if a new member message arrives for the parent conversation. In that case, the child is moved to background delivery.

**Data flow**: It receives a child turn id → starts one task waiting for the child terminal and another listening to the parent turn’s hub arrivals → if the terminal wins, returns it → if a valid member arrival wins and `_detach` succeeds, returns `None` to mean the child continues in the background → cleans up pending wait tasks before leaving.

**Call relations**: `Subagents.spawn` uses this for interruptible foreground spawns. It calls `_await_terminal` for normal completion and `_detach` to stamp the child so its later result will be delivered instead of returned inline.

*Call graph*: calls 2 internal fn (_await_terminal, _detach); called by 1 (spawn); 5 external calls (create_task, ensure_future, gather, wait, log).


##### `Subagents._terminal_or_park`  (lines 1168–1184)

```
async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Reads a child’s terminal result, and treats a parked child as a failure that should be cancelled. Parked means the turn stopped waiting on a spend limit without producing a final answer.

**Data flow**: It receives a turn id → reads the turn’s terminal and status from the database → returns a parsed `TerminalFrame` if present → if status is parked, cancels the turn and raises `SubagentParked` → otherwise returns `None` to mean it is still running.

**Call relations**: `_await_terminal` calls this after workflow events and errors. It is the point where waiting is tied back to durable database state rather than trusting only the workflow engine.

*Call graph*: called by 1 (_await_terminal); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents._detach`  (lines 1186–1217)

```
async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool
```

**Purpose**: Moves a still-running foreground child into background-result delivery when a real member message has arrived for the parent. This lets the parent answer the user without losing the child’s eventual result.

**Data flow**: It receives the child turn id and the arrival message id → in one database update, checks that the child has no terminal, has no result delivery already set, and that the arrival is a valid unconsumed member message for the parent turn → marks the child result as pending delivery if all guards pass → returns whether the move happened.

**Call relations**: `_await_terminal_or_detach` calls this to resolve the race between child completion and user interruption. A `False` result means the child probably finished first, so the foreground path should still return the terminal inline.

*Call graph*: called by 1 (_await_terminal_or_detach); 4 external calls (exists, select, update, workspace_tx).


##### `SubagentResult.deliver`  (lines 1246–1296)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished background child’s result back into the conversation that spawned it. This turns child completion into an ordinary arrival the parent can read later.

**Data flow**: It receives a finished child turn → ignores it if delivery is not pending or it has no parent → requires a committed terminal → loads the parent turn and, for agent children, the child agent row → builds the delivery body → invokes the parent conversation with a deduplicated key → marks the child delivery as delivered in the database.

**Call relations**: This is the delivery-side counterpart to background spawn and detach. It calls `_body` to create the safe message text, then uses the turn invoker to admit that message to the parent conversation before stamping the child as delivered.

*Call graph*: calls 1 internal fn (_body); 4 external calls (model_validate, select, update, workspace_tx).


##### `SubagentResult._body`  (lines 1298–1327)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Builds the structured message that carries a child’s result back to the parent. The message names the target, child id, status, and payload.

**Data flow**: It receives the child turn and, for agent children, the agent row → decides whether the target was a profile or agent → chooses the right output contract and trust walling behavior → asks `_payload` for the validated payload and status → wraps the payload in a spawn-result envelope and escapes any fake closing tag inside the payload → returns the final message body string.

**Call relations**: `SubagentResult.deliver` calls this before invoking the parent conversation. It uses the untrusted-content wall helper when output should not be treated as direct instructions and uses `output_contract` for agent-child validation.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (wall, output_contract).


##### `SubagentResult._payload`  (lines 1329–1348)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a child terminal record into the payload and status that should be delivered to the parent. It never blindly passes invalid final answers as successful answers.

**Data flow**: It receives an optional output contract and a terminal frame → if the child did not finish successfully, returns diagnostic text and the terminal status → if the child ended with a question, returns the structured question and a question status → if no contract is available, returns an invalid-profile message → otherwise validates the terminal text against the contract and returns clean JSON, or returns an invalid-output message with validation faults.

**Call relations**: `_body` calls this while constructing the delivery message. It is the final safety check that makes delivered background results match the same contract discipline expected from foreground spawns.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).


### `core/src/ufo/runtime/turns/contracts.py`

`domain_logic` · `schema write, spawn, dispatch, and delivery validation`

When one agent asks another agent to do work, both sides need an agreed shape for the message: what fields are required, what types they must have, and what result should come back. This file provides that shared rulebook. Some contracts are normal Pydantic models, which are Python classes that validate data. Others are raw JSON Schema objects stored as data for workspace agents. This file makes both kinds behave alike, so the rest of the system can ask the same questions: “What schema do you use?”, “Does this Python value fit?”, and “Does this JSON text fit?”

The default input contract is a simple object with a `task` string. The default output contract is a simple object with a `result` string. If a workspace agent declares its own schema, `JsonContract` wraps that schema and validates payloads against it.

A key safety choice is that declared schemas are kept limited. They must be small, describe a top-level object, and cannot use external references or regular-expression matching. This matters because these schemas may come from stored workspace data. Without these checks, a schema could try to fetch outside resources or make validation take unbounded time, like handing the server a puzzle that can trap its single serving loop.

#### Function details

##### `ValidatedJson.model_dump`  (lines 55–56)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated payload as a normal Python value. This lets a raw JSON Schema result act like a Pydantic model result when other code asks for its contents.

**Data flow**: It starts with a `ValidatedJson` object that holds `data`. It does not transform or copy that data in any special way. It simply gives the stored value back to the caller.

**Call relations**: After `JsonContract.model_validate` accepts a payload, it wraps the payload in `ValidatedJson`. Later code can call this method in the same way it would call `model_dump` on a Pydantic model, so callers do not need to care which kind of contract validated the data.


##### `ValidatedJson.model_dump_json`  (lines 58–59)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the already-validated payload into a JSON string. This is useful when the system needs to send or store the validated value as plain JSON text.

**Data flow**: It reads the stored `data` value, passes it to Python’s JSON encoder, and returns the resulting text. It does not re-validate the value because validation already happened before this wrapper was created.

**Call relations**: This completes the mimicry of a Pydantic model result. Code that expects a validated object with a JSON-dumping method can use a `ValidatedJson` from `JsonContract` without a separate special case.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 68–69)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the raw JSON Schema used by this contract. Other parts of the system use this to describe the expected payload shape to people or to other code.

**Data flow**: It reads the schema stored inside the `JsonContract` and returns it as a plain dictionary. The returned value represents the rules that future payloads must follow.

**Call relations**: This gives raw-schema contracts the same kind of schema-reporting method that Pydantic model classes provide. `payload_keys` relies on this shared method so it can summarize either kind of contract in one way.


##### `JsonContract.model_validate`  (lines 71–106)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks whether a Python value follows this contract’s JSON Schema. If it fits, the value is wrapped as validated data; if it does not fit, the function raises a Pydantic-style `ValidationError` so callers see the same error shape as with normal models.

**Data flow**: It receives any Python value, builds a JSON Schema validator from the stored schema, and gathers all validation problems in path order. If the schema tries to use an unresolvable reference, it turns that into a validation error instead of fetching anything. If any normal validation faults are found, it reports them with the exact part of the payload that failed. If there are no faults, it returns `ValidatedJson(data)`.

**Call relations**: This is the core bridge between raw JSON Schema contracts and Pydantic-style validation. `JsonContract.model_validate_json` calls it after parsing JSON text. The rest of the spawn, dispatch, and delivery flow can treat its success or failure like a normal Pydantic validation result.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 108–124)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks whether a JSON text string follows this contract. It first makes sure the text is valid JSON, then validates the resulting value against the schema.

**Data flow**: It receives a string. If the string is not valid JSON, it raises a Pydantic-style `ValidationError` saying the JSON itself is invalid. If parsing succeeds, it hands the parsed value to `JsonContract.model_validate`, and returns that function’s validated wrapper.

**Call relations**: This is the text-entry version of validation. It hands off to `JsonContract.model_validate` so the actual schema checking is kept in one place, and callers get the same success object or validation error either way.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `freeform_result_contract`  (lines 130–137)

```
def freeform_result_contract(contract: Contract) -> bool
```

**Purpose**: Checks whether a contract is the simple built-in shape of exactly one string field named `result`. This helps the system recognize the common freeform result handoff format.

**Data flow**: It receives a contract. If the contract is a Pydantic model class, it looks at the model’s fields and returns `true` only when the only field is `result` and its type is `str`. For raw `JsonContract` objects, it returns `false`.

**Call relations**: This is a small classification helper used when the system needs to know whether a result contract is the ordinary freeform text result. It does not call into other local helpers; it just inspects the model shape directly.


##### `payload_keys`  (lines 140–156)

```
def payload_keys(contract: Contract) -> str
```

**Purpose**: Creates a short human-readable summary of the fields a payload may contain, with required fields shown plainly and optional fields marked. This is useful for explaining a contract in catalogs or error messages.

**Data flow**: It receives either a Pydantic model contract or a raw `JsonContract`. It asks the contract for its JSON Schema, looks for the `properties` and `required` sections, and then builds a comma-separated list of field names. If there are no object properties to show, it returns `(no keys)`.

**Call relations**: This function depends on the shared `model_json_schema` method, which both Pydantic models and `JsonContract` provide. That means spawn listings and refusal messages can describe target payloads without caring how the contract was originally defined.

*Call graph*: 1 external calls (model_json_schema).


##### `input_contract`  (lines 159–160)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the input contract for an agent. If no custom schema was declared, it uses the default `task` string input; otherwise it wraps the declared schema as a `JsonContract`.

**Data flow**: It receives either `None` or a mapping that represents a JSON Schema. `None` becomes the built-in `TaskInput` model. A provided schema becomes a new `JsonContract` around that schema.

**Call relations**: This is used when the system needs a uniform input contract before validating a spawned or dispatched task. It creates the bridge object that later validation code can call like a Pydantic model.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 163–164)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the output contract for an agent. If no custom schema was declared, it uses the default `result` string output; otherwise it wraps the declared schema as a `JsonContract`.

**Data flow**: It receives either `None` or a mapping that represents a JSON Schema. `None` becomes the built-in `AgentResultOutput` model. A provided schema becomes a new `JsonContract` around that schema.

**Call relations**: This is used when the system prepares to validate an agent’s returned result. Like `input_contract`, it hides the difference between built-in model contracts and stored raw-schema contracts.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 167–182)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Validates a user-declared input or output schema before it is stored. It rejects schemas that are too large, are not top-level objects, contain unsafe keywords, or are not valid JSON Schema.

**Data flow**: It receives a candidate schema and the name of the field being checked. It serializes the schema to measure its size, verifies that the top-level type is `object`, asks `_refused_keyword` to search for banned features such as references and regular expressions, and finally asks the JSON Schema library to compile-check the schema. If anything fails, it raises `ValueError`; if everything passes, it returns nothing and the schema is considered acceptable.

**Call relations**: This runs at the point where declared schemas are written, not later when a spawn reads them. It calls `_refused_keyword` for the safety scan, then relies on the JSON Schema library for formal schema correctness. That keeps unsafe or invalid contracts out of stored workspace data.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 185–197)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches through a schema-like value for keywords this project does not allow, such as external references and regular-expression rules. It returns the first refused keyword it finds.

**Data flow**: It receives any nested value. If the value is a mapping, it checks each key and then recursively checks each value. If the value is a list, it recursively checks each item. If it finds a banned keyword, it returns that keyword; if the whole structure is clean, it returns `None`.

**Call relations**: This is the safety scanner used by `check_declared_schema`. It is kept separate so the write-time schema check can clearly ask one question: “Does this schema contain any feature we refuse to store?”

*Call graph*: called by 1 (check_declared_schema).


### Brief-writing pipeline
Declares the staged outline-to-draft-to-critique workflow and the schemas that let each writing step hand work safely to the next.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load`

This file is like a recipe card for a small team of specialist writing assistants. The parent agent can run three subagents in order: one creates an outline, one writes a draft from that outline, and one reviews the draft. Each subagent is “toolless,” meaning it does not call external tools or start more agents; it only reads its prompt and returns structured text. That keeps the pipeline simple and predictable.

The file uses Pydantic models, which are Python classes that describe and check data shapes. For example, a brief request must have a topic and may have an audience. The outline stage must return an outline string. The draft stage receives the topic and outline, then returns a draft. The critic stage receives the draft and returns a verdict plus optional improvements.

At the bottom, the file builds three `SubagentProfile` objects. A profile is the instruction sheet for a subagent: its name, the prompt text loaded from disk, what tools it may use, what input it expects, what output it must produce, and how many back-and-forth rounds it may take. Without this file, the brief extension would not know what stages exist, what prompts to use, or how to connect one stage’s result to the next.


### Browser delegation
Adds browser-focused delegation tools and the specialized browser subagent profile that executes web automation tasks.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling during browser tool calls`

This file is the bridge between the main agent and a specialized browser agent. The main agent does not directly drive a browser here. Instead, it gives a clear job to a child agent with the browser profile, waits for the result, and reports back in a controlled way. This matters because browser automation can get stuck on slow pages, login walls, popups, or loops. Without this file, a browser task could tie up the parent agent for too long, or many small browsing jobs would have to be run by hand one at a time.

The single-task path, `browser_task`, starts a fresh isolated browser session. It gives the child agent a starting URL, a self-contained task description, and a friendly task name. It also enforces a time limit. If the browser run takes too long, the child is cancelled and the parent gets a clear failure message explaining that any page actions already done still happened.

The batch path, `wide_browse`, reads a workspace file containing one URL or site name per line, removes duplicates, and sends each item to a browser child. It limits how many run at once, like only opening a fixed number of checkout lanes instead of all at once. Each child’s result is collected into `wide_browse.json`, and one failed item becomes one error row rather than ruining the whole batch.

#### Function details

##### `_browser_task`  (lines 99–146)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation job by spawning a browser subagent and waiting for it to finish. It protects the parent agent by enforcing the requested timeout and by returning a clear result or failure.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the URL, task instructions, task name, and timeout. It checks that subagent control is available, starts a browser-profile child turn with those instructions, and waits for that child within the time budget. If the child times out or is cancelled, it returns a failure result that says the browser may already have changed the page. If the child finishes successfully, it reads the child’s browser result text, validates it as a `BrowserResult`, and returns it as text content.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the main agent calls that tool, this function uses the shared spawning seam in `ToolContext.spawn` to create the browser child. It then waits through the subagent controller and turns the child’s final browser summary into the parent tool result.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 149–162)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. `wide_browse` uses it to get the URLs or site names that should each receive a browser visit.

**Data flow**: It receives the tool context and a file path. It shell-quotes the path so special shell characters in the filename are treated safely, asks the sandbox to run `cat` on that file, and raises an error if the file cannot be read. It then trims whitespace from each line, skips blank lines, removes duplicates while keeping the first occurrence, and returns the resulting list of entities.

**Call relations**: `_wide_browse` calls this before starting any browser children. It acts like the intake clerk for the batch: first make sure the list is readable and tidy, then hand the cleaned list back so the batch runner can fan out work.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 165–207)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs the same browser-style extraction task across many URLs or site names and saves all results into a JSON file. It is meant for broad, repetitive browsing jobs where each item can be processed independently.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing an entities file, a prompt template, and an optional output schema file path. It reads and deduplicates the entities, rejects the batch if it is too large, reads the schema text if available, and creates a semaphore, which is a gate that limits how many child jobs run at once. It launches one `visit` task per entity, gathers all outcomes, converts individual failures into error rows, writes the full row list to `wide_browse.json` in the sandbox, and returns a small JSON message containing the rows and output filename.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It first relies on `_read_lines` to prepare the batch list, then uses its nested `_wide_browse.visit` worker for each entity. `asyncio.gather` lets the workers run concurrently while still collecting all their answers into one final tool result.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 173–188)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser job for one entity in a `wide_browse` batch. It is deliberately isolated so one website failure becomes one row’s error instead of cancelling the whole batch.

**Data flow**: It receives one entity, such as a URL or company name. It waits for permission from the semaphore so the batch does not start too many browser children at once, fills `{entity}` into the prompt template, appends the output schema text if there is one, and spawns a browser-profile child with ordinary context size. It returns a dictionary containing the entity and the child’s JSON result text, or an empty result if the child produced no output.

**Call relations**: `_wide_browse` creates one of these workers for each cleaned entity and runs them together. Each worker hands its prompt to a browser child using the same subagent spawning mechanism as the single-task tool, then gives its small row-shaped result back to `_wide_browse` for collection and file writing.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup / subagent setup`

This file is like an ID badge and job description for a browser-focused helper agent. The main agent can delegate web work to this helper, such as opening pages, filling forms, saving screenshots, or gathering information from websites. Without this profile, the system would not have a clear, reusable way to start a browser-only child agent with the right prompt, tools, and data format.

The file first loads a written instruction prompt from `prompts/subagent_browser.md`. That prompt tells the browser subagent how to behave. It then builds the tool list the subagent is allowed to use: browser tools, plus a few basic file and web-search tools so it can save findings into the shared workspace for the parent agent.

Two small data models describe the conversation boundary. `BrowserTask` is what the parent gives the child: a freeform task, an optional URL, an optional task name, and an `extended_context` flag that defaults to true. In plain terms, this means browser sessions usually get a larger working memory budget because web tasks often take many steps and cannot easily be resumed halfway. `BrowserResult` is what comes back: a freeform result string.

Finally, `BROWSER_PROFILE` packages all of this together as a `SubagentProfile`, including the model choice and a warning that the output is untrusted. That matters because web pages can contain misleading or hostile text, so the parent should treat the child’s report carefully.


### Document-writing helper
Defines the focused prose-writing subagent profile used for drafting and editing work without code execution or browsing.

### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `subagent setup`

This file is like a job description and equipment checklist for a specialist assistant. The specialist is a child agent, meaning a smaller helper that a parent agent can start for a particular task. Here, that helper is only for writing work: reading drafts, editing them, saving them, searching files, and loading the writing workflow skill it needs.

The file names the profile “writing” and pins it to a specific model, `gpt-5.6-terra`, instead of inheriting whatever model the parent is using. That makes the writing helper predictable and deliberately chosen for prose work.

It also defines the tools the child is allowed to use. The list is intentionally narrow: file reading and writing tools, search tools, and `load_skill`. There is no shell, programming console, web access, or file-sharing tool. In plain terms, this keeps the child from turning into a general coding or research agent. It should work only from the workspace and the instructions it is given.

Two small Pydantic models describe the shape of the conversation with this subagent. Pydantic is a library that checks that data has the expected fields. `WritingTask` is what the parent sends in, including the writing objective and skills to preload. `WritingResult` is what comes back. Finally, all of these pieces are assembled into `WRITING_PROFILE`, the object the larger system can use when it wants to spawn this writing specialist.


### Research delegation
Provides parallel wide-research orchestration and the research and deep-research subagent profiles that perform the individual investigations.

### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `tool invocation / request handling`

This file solves a practical batching problem: if a user has many companies, topics, or entities to research, doing them one by one is slow and fragile. `wide_research` reads a plain text file of entities, removes duplicates, and then launches a limited number of child research jobs in parallel. Think of it like giving a stack of index cards to a small team: each worker researches one card, writes their answer to a private note, and the lead gathers all notes into one report.

The file is careful about reliability. Each child job gets a stable identity derived from the parent call and the entity name. That means if the parent run crashes and is retried, already-started or completed work can be reconnected instead of repeated. As each entity finishes, its row is saved into a recovery file. A failure for one entity becomes an error in that entity’s row, not a failure of the whole batch.

The final output is `wide_research.json`, marked as untrusted because it comes from web research and child agents. The tool also cleans up temporary per-entity result files after use. Without this file, users would have no built-in way to safely fan out a structured research task over many targets and collect the answers consistently.

#### Function details

##### `_read_lines`  (lines 66–79)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads the user’s entity list from the sandbox and turns it into a clean list of unique, non-empty lines. It exists so the rest of the tool can work with a tidy list instead of raw file text.

**Data flow**: It takes a tool context and a file path. It asks the sandbox to `cat` the file, safely quoting the path so shell special characters are treated as part of the filename. If the read fails, it raises an error. If it succeeds, it trims each line, skips blanks, removes duplicates while keeping the original order, and returns the resulting list of entities.

**Call relations**: _wide_research calls this near the start, before any child research jobs are launched. Its output decides exactly which entities the later fan-out will visit.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_WideResearch.run`  (lines 98–134)

```
async def run(self) -> ToolResult
```

**Purpose**: Runs the whole batch once setup is complete. It launches visits for all entities, gathers their rows, writes the final JSON report, and returns the tool response.

**Data flow**: It starts with a prepared `_WideResearch` object containing the entities, paths, recovery data, locks, and context. It registers cleanup for temporary result files, then asks `_visit` to process each entity in parallel. If an ordinary entity-level error happens, it converts that into a row with an error message instead of stopping the whole batch. It writes a recovery aggregate, writes the final `wide_research.json`, and returns a `ToolResult` containing the JSON summary plus the output filename.

**Call relations**: This is the main method called by `_wide_research` after setup. It delegates per-entity work to `_visit`, uses `_install_recovery` to persist the final aggregate, and packages the finished data into the result returned to the caller.

*Call graph*: calls 2 internal fn (_install_recovery, _visit); 6 external calls (__init__, __init__, __init__, __init__, gather, dumps).


##### `_WideResearch._remove_result_files`  (lines 136–143)

```
async def _remove_result_files(self) -> None
```

**Purpose**: Deletes temporary JSON files that child research jobs wrote for individual entities. This keeps the workspace from being cluttered with hidden intermediate files after the final report has been produced.

**Data flow**: It reads the set of persisted temporary result paths from the `_WideResearch` object. If there are none, it does nothing. Otherwise it builds a safe `rm -f` command with shell-quoted paths, runs it in the sandbox, and raises an error if deletion fails.

**Call relations**: _WideResearch.run registers this as a cleanup action with the tool context. It is not part of the main research path; it runs later as housekeeping once the tool context performs cleanup.

*Call graph*: 1 external calls (quote).


##### `_WideResearch._install_recovery`  (lines 145–155)

```
async def _install_recovery(self, rows: tuple[WideResearchRow, ...]) -> None
```

**Purpose**: Writes the current batch progress to a recovery file in a safe, replace-all-at-once way. This lets a later retry pick up completed rows instead of starting from zero.

**Data flow**: It takes the rows completed so far. It wraps them in a `WideResearchFile`, serializes that structure as formatted JSON, writes it to a temporary staging path, and then moves that temporary file into the real recovery path. If the move fails, it raises an error.

**Call relations**: _save_row` calls this every time an entity finishes, so progress is saved row by row. `_WideResearch.run` also calls it at the end to make sure the final recovery file matches the final report.

*Call graph*: called by 2 (_save_row, run); 3 external calls (__init__, dumps, quote).


##### `_WideResearch._save_row`  (lines 157–167)

```
async def _save_row(self, row: WideResearchRow) -> None
```

**Purpose**: Records one completed entity row and immediately updates the recovery file. It is the small checkpointing step that makes the batch resilient to interruptions.

**Data flow**: It receives a `WideResearchRow`, then takes an asynchronous lock so two parallel entity jobs do not write the shared aggregate at the same time. It stores the row by entity name, rebuilds the completed rows in the original entity order, asks `_install_recovery` to persist them, and marks that entity’s temporary result file as something cleanup should remove later.

**Call relations**: _visit` calls this after each entity has either produced JSON or produced an error row. It hands the updated progress to `_install_recovery`, which writes the durable recovery file.

*Call graph*: calls 1 internal fn (_install_recovery); called by 1 (_visit).


##### `_WideResearch._visit`  (lines 169–216)

```
async def _visit(self, entity: str) -> WideResearchRow
```

**Purpose**: Researches one entity, or reuses its recovered result if it was already completed. It turns one target from the entity list into one row in the final report.

**Data flow**: It takes an entity name from the prepared batch. A semaphore limits how many of these visits run at once, so the tool does not start too many child agents together. If the entity was recovered, it returns that saved row. Otherwise it builds a child objective by inserting the entity into the prompt template and telling the child where to write JSON. It spawns a research subagent with a stable deduplication key, reads the child’s result file, parses it as JSON, and returns a success row. If the file cannot be read or is not valid JSON, it saves and returns an error row instead.

**Call relations**: _WideResearch.run starts one `_visit` task per entity. `_visit` may call `_save_row` to checkpoint the row, and it also validates child output with the research output model when it needs to include the child’s own message in an error.

*Call graph*: calls 1 internal fn (_save_row); called by 1 (run); 4 external calls (__init__, model_validate, loads, quote).


##### `_wide_research`  (lines 219–290)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the actual handler for the `wide_research` tool. It prepares the batch: reads inputs, checks limits, loads the output schema, restores any previous progress, creates temporary paths, and then starts `_WideResearch.run`.

**Data flow**: It receives the tool context and validated user arguments. It first requires an idempotency key, which is a stable call identifier used to avoid duplicate work on retries. It reads and deduplicates entities with `_read_lines`, rejects batches that are too large, computes a call ID, prunes old recovery files, and tries to load the current recovery file. It then reads the requested output schema file; if that fails, it returns a clear `ToolFailure` and starts no children. If setup succeeds, it creates a concurrency limit, builds one hidden result path per entity, constructs a `_WideResearch` object, and returns the result of its `run` method.

**Call relations**: The tool definition at the bottom of the file points to this function as the handler users actually invoke. It is the setup stage before `_WideResearch.run`: it calls `_read_lines`, builds the `_WideResearch` instance, and hands off the prepared state for execution.

*Call graph*: calls 1 internal fn (_read_lines); 8 external calls (__init__, __init__, __init__, Lock, Semaphore, sha256, loads, quote).


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / config load`

This file is like a job description plus a toolbox list for research assistants inside the larger agent system. When the main agent needs focused research, it can hand a task to one of these subagents instead of doing everything itself.

The file defines two profiles. The regular `research` profile is meant for focused research tasks. The `deep_research` profile uses the same tool set but is allowed many more back-and-forth work rounds, so it can handle broader, multi-source investigations. Both profiles can search the web, fetch web pages, use selected browser and file tools, call external tools, search memory, and work with spreadsheets. They do not get every possible browser capability; some broader browsing tools stay with other parts of the system.

The file also defines simple input and output models using Pydantic, a library that describes and checks structured data. A research subagent receives one freeform `objective`, and returns one `result` written according to the shared delivery rules.

A notable detail is that the prompts are read from Markdown files as soon as this module is loaded. So this file does not contain the full instructions itself; it connects profile names and models to prompt files on disk. Without this file, the research extension would not know how to create these delegated research workers or what tools and limits they should have.


### Website-building delegation
Adds a website-building delegation tool and the specialized site-construction subagent profile that completes the build in a child session.

### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `tool invocation during request handling`

This file is like a front desk for website work. The main agent may know that a user wants a website, web app, dashboard, or simple web game, but the detailed building and browser-checking work is delegated to a specialist called the `website_building` subagent. Without this file, the main agent would not have this clean, typed way to start that specialist build session.

The file first describes what information the tool needs. `BuildWebsiteInput` requires a self-contained `objective`, because the child agent does not inherit the parent conversation history. It can also include a friendly task name, a list of skills to preload, and a flag for extra working time when the build is large.

When the tool runs, `_build_website` asks the tool context to `spawn` a child turn using the website-building profile. “Spawn” here means starting another agent session, not a new filesystem. The child works in the same sandbox, so any files it creates remain available afterward. The call uses an idempotency key, which is a reuse key that helps reconnect to the same child job if the system has to retry after a crash.

Finally, `DELEGATION_TOOLS` registers this as a side-effecting tool, meaning it changes the outside world of the conversation by creating files, running a site, or registering a hosted result.

#### Function details

##### `_build_website`  (lines 56–63)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the actual worker behind the `build_website` tool. It starts the specialized website-building child agent, waits for its result, and returns the child’s summary text to the caller.

**Data flow**: It receives the current tool context and a validated `BuildWebsiteInput` object. It turns the input into a plain data payload, leaving out empty optional fields, then passes that payload to `ctx.spawn` with the website-building profile name and the current idempotency key. When the child agent finishes, it takes the child output, converts it to JSON text if there is output, and wraps that text in a `ToolResult` so the main agent can read it.

**Call relations**: This function is called when the registered `build_website` tool is invoked. It hands the real work off to `ToolContext.spawn`, which starts or reconnects to the website-building subagent. After that subagent returns, `_build_website` packages the result using `TextContent` and `ToolResult` for the tool system to send back to the main conversation.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `when a website-building subagent is created`

This file is like a job description and toolbox checklist for a website-building subagent. A subagent is a smaller assistant started by a parent conversation to work on a focused task. Here, that task is building and checking a website inside the same workspace as the parent.

The file loads a website-building prompt from a nearby Markdown file, sets a name for the subagent, and gives it a round limit so it cannot run forever. It also chooses which tools the subagent may use. The list includes normal file tools for reading and editing code, site-building and local serving tools, JavaScript and spreadsheet REPL tools for testing, and optional web research tools. A REPL is an interactive tool where code can be run step by step.

Two important tools are deliberately left out. The subagent cannot use `publish_website`, because publishing a full app is meant to happen in the parent conversation. It also cannot use `share_file`, because the subagent is not directly talking to the member; it leaves its work in the shared workspace for the parent to inspect.

The small `WebsiteBuildingTask` and `WebsiteBuildingResult` models describe the shape of messages going into and coming out of the subagent. Finally, everything is bundled into `WEBSITE_BUILDING_PROFILE`, which the wider system can use whenever it needs to start this website-building helper.

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-tool-catalog` — The shared menu of tools the agent may call, including built-in tools, extension tools, and guarded bridge tools.
- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-turn-queue-state` — The durable queue of conversation turns, including admission source, run claim, parked state, resume state, and final status.
- `reg-transcript-history` — The saved conversation timeline, including messages, compacted summaries, final answers, costs, and readable history.
- `reg-cancellation-cleanup-state` — The shared stop-and-cleanup state that records when active turns, workflows, child work, sandboxes, and streams are being wound down.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-execution-environment-policy` — The shared rules for where commands and tools may run, such as local execution, Docker, cloud sandboxes, terminals, and browser sessions.
- `reg-delegation-state` — The parent-child work state that tracks subagent turns, their contracts, trace links, pending results, and delivery back to the parent.
- `reg-turn-assembly-snapshot` — The resolved per-turn host package handed into execution, including selected agent/model, effective prompts, allowed tools/spawn menu, seeded file digests, skills, and routing choices.
