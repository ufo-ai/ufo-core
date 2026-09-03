# Subagents, delegation, and child-turn orchestration  `stage-12`

This stage is about delegation during the main work loop. When the main agent has a job that is better done by a specialist, it can start a child agent, give it a clear task, wait for the answer, and fold that answer back into the parent turn. It works like a manager assigning a focused task to a teammate.

The core piece is `subagents.py`. It checks that the requested helper is allowed, prepares the child run, sends the right input, and accepts only a validated result. `turns/contracts.py` defines those input and output shapes, called contracts: agreed data formats that prevent confusing or unsafe messages from being passed around.

The extension files add concrete kinds of helpers. The browser delegation code lets the parent ask a browser agent to visit one site or many sites in parallel. The research subagent configuration defines normal and deep research helpers, including their tools, prompts, expected data, and model choices. The sites delegation code adds a website-building helper, so larger site work can happen in a focused child session while sharing the same project workspace.

## Files in this stage

### Core child orchestration
Core runtime support for spawning child agents, validating delegated payloads, and returning checked child results.

### `core/src/ufo/runtime/subagents.py`

`orchestration` · `request handling`

This file is the control desk for “spawn”, the feature that lets an agent delegate work to a smaller helper. A helper can be a named profile, which is like a saved job description with a prompt and input/output rules, or a full workspace agent owned by someone in the workspace. Without this file, delegated work could be duplicated after crashes, charged to the wrong account, run with the wrong permissions, or send unsafe text back as if it were trusted instructions.

The main flow is: resolve the requested target, validate the caller’s payload against that target’s expected input, create a child conversation and first turn in the database, check billing, and put the child turn on the express work queue. A foreground spawn waits for the child’s final saved result. A background spawn returns the child turn id right away and later delivers the result back into the parent conversation.

The file is careful about safety and recovery. A repeated spawn with the same deduplication key reconnects to the already-created child instead of making a second one. If a member sends a new message while the parent is waiting, the child can be detached and continue in the background, like a terminal command moved out of the foreground. Outputs are validated against schemas, and untrusted results are wrapped so the parent reads them as content, not instructions.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 145–149)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the registry of subagent profiles has no duplicate names. This matters because a spawn target must point to exactly one profile, not a confusing choice between two.

**Data flow**: It reads the profile names stored on the new registry → counts repeated names → either leaves the registry usable or raises an error naming the duplicates.

**Call relations**: This runs automatically when a SubagentRegistry is created, before later lookup methods such as SubagentRegistry.get and SubagentRegistry.find rely on profile names being unique.


##### `SubagentRegistry.get`  (lines 151–157)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Looks up a subagent profile by name and fails loudly if it is missing. Callers use this when a missing profile is a real error rather than an acceptable “not found”.

**Data flow**: It receives a profile name → asks SubagentRegistry.find for the matching profile → returns that profile, or raises an UnknownSubagentProfile error that includes the known profile names.

**Call relations**: It builds on SubagentRegistry.find. The queue profile-resolution path calls it when it needs a registered profile and cannot continue without one.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 159–160)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Searches for a profile by name and returns nothing if it is not present. This is the gentle lookup used when the caller wants to decide what to do about a miss.

**Data flow**: It receives a name → scans the registry’s stored profiles → returns the first profile with that name, or null if none match.

**Call relations**: SubagentRegistry.get calls this and turns a missing result into an error. Other parts of this file also use the same idea when deciding whether an old profile still exists.

*Call graph*: called by 1 (get).


##### `_target_model`  (lines 174–182)

```
def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None
```

**Purpose**: Finds the model explicitly chosen by a spawn target, when there is one. A “model” here means the AI engine/provider setting that affects cost and execution.

**Data flow**: It receives either a profile target or an agent target → for a profile, reads its pinned model → for an agent target, returns null because the agent’s own row supplies the model elsewhere.

**Call relations**: Subagents.spawn uses this while admitting a child turn, so billing and execution are based on the model that will actually answer the child.

*Call graph*: called by 1 (spawn).


##### `subagent_system_prompt`  (lines 185–221)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the system prompt, meaning the instruction text, for a profile-based subagent. It combines the profile’s instructions, available skills, optional preloaded skill text, and the required rule that the child must finish with a structured answer.

**Data flow**: It receives a profile plus optional skill listings and preloaded skills → fills the skill index slot, checks for forgotten template placeholders, adds preloaded skill bodies within a size limit, appends output-discipline rules and the finish-tool contract → returns the final prompt string or raises an error if the prompt is unsafe or incomplete.

**Call relations**: It uses prompt-rendering helpers to build the skill index and loaded-skill text. The resulting prompt is used by the wider runtime when actually running a profile subagent.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 243–252)

```
def authorize(self, authority: ExecutionAuthority) -> 'Subagents'
```

**Purpose**: Creates a copy of the Subagents helper bound to the authority of the current tool call. Authority means who the action is being done for, such as a specific member or the workspace.

**Data flow**: It receives an authority object → if it is member authority, stores that authority on a copied Subagents object → otherwise falls back to the parent turn’s authority → returns the copied object.

**Call relations**: Tool-call code uses this before spawning so ownership checks, billing, and child-turn stamps all agree about whose request this is.

*Call graph*: 1 external calls (replace).


##### `Subagents.spawn`  (lines 254–397)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnResult
```

**Purpose**: Starts a child turn for a profile or workspace agent. It is the main “delegate this work” operation: validate the request, create or reconnect to the child, enqueue it, and optionally wait for its final checked result.

**Data flow**: It receives a target name, payload, and options such as background mode or a deduplication key → chooses or derives a child conversation id and turn id → resolves the target, validates input, records the child in the database, checks balance, enqueues work → returns a SpawnResult immediately for background work or after reading and validating the terminal result for foreground work.

**Call relations**: This is the central caller of the helper methods in Subagents: it uses _resolve, _validated, _existing_agent_spawn, _admit, _enqueue, _await_terminal, and _await_terminal_or_detach. If a foreground wait fails, it cancels the child so unfinished work is not left running without a caller.

*Call graph*: calls 9 internal fn (_admit, _await_terminal, _await_terminal_or_detach, _enqueue, _existing_agent_spawn, _resolve, _validated, _target_model, __init__); 11 external calls (__init__, __init__, sha256, dumps, cancel_one_turn, input_contract, output_contract, ws_current, turn_id_for, uuid4 (+1 more)).


##### `Subagents.result`  (lines 399–436)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the final result of a child turn that this conversation spawned. It is useful when a host-side consumer wants the child’s saved terminal and validated output directly, without trusting a model to repeat it accurately.

**Data flow**: It receives a child turn id → verifies the turn belongs to this conversation through _require_child → loads its conversation, agent, and terminal from the database → picks the correct output contract from the profile registry or agent schema → validates the terminal text if possible → returns a SpawnResult with output, terminal, and trust status.

**Call relations**: It calls _require_child first to enforce ownership. It then uses _agent_output_schema and _untrusted_output to describe agent children correctly before returning the same kind of SpawnResult that foreground spawn returns.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, workspace_tx, output_contract).


##### `Subagents.wait`  (lines 438–458)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits for one or more child turns to finish and reports their terminal status and text. This is for tools that must briefly wait inside their own call rather than ending the parent turn and receiving a later delivery.

**Data flow**: It receives child turn ids → verifies each one belongs to this conversation → waits for each terminal frame → wraps each final status, text, and untrusted flag into SubagentStatus objects → returns all statuses as a tuple.

**Call relations**: It uses _require_child for the safety gate, _await_terminal for the blocking wait, and _untrusted_output so callers know whether the text should be treated carefully.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 460–477)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a running child turn that belongs to this parent conversation. It gives the caller a safe way to stop delegated work instead of leaving it to keep spending time or money.

**Data flow**: It receives a child turn id → verifies the child relationship → asks the shared cancellation primitive to cancel the workflow and commit a cancelled terminal if needed → reloads the child’s status and terminal text → returns a SubagentStatus.

**Call relations**: It relies on _require_child before touching the turn. It hands the actual workflow cancellation to cancel_one_turn, the shared runtime cancellation helper.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents.message`  (lines 479–594)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child conversation. This lets the parent answer a child’s question or continue a child’s work without starting a brand-new helper from scratch.

**Data flow**: It receives the original child turn id, message text, a deduplication key, and delivery preference → verifies the child relationship and still-valid profile if needed → locks the child conversation → either finds an existing follow-up with the same dedup key or inserts the next turn in that child conversation → checks balance for genuinely new work → dispatches the follow-up if queued → returns its status.

**Call relations**: It uses _require_child to confirm the caller may message the child, _profile_model and _require_balance before creating new work, and dispatch_next_turn after admission so the queued follow-up can run.

*Call graph*: calls 3 internal fn (_profile_model, _require_balance, _require_child); 9 external calls (__init__, model_validate, insert, select, workspace_tx, current_traceparent, authority_member_id, dispatch_next_turn, turn_id_for).


##### `Subagents._resolve`  (lines 596–619)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Turns a target string into the actual thing to spawn: either a registered profile or a workspace agent. It also catches unclear names, such as when a profile and agent share the same bare name.

**Data flow**: It receives a target string, possibly prefixed with profile: or agent: → searches the profile registry and/or workspace agents → returns the matching SubagentProfile or AgentTarget → raises a helpful error when the name is unknown or ambiguous.

**Call relations**: Subagents.spawn calls this near the start of every new spawn. It uses _profile_names and _agent_names to make error messages useful, and _agent_target to load agent details.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._validated`  (lines 621–634)

```
def _validated(self, target: str, contract: Contract, payload: dict[str, Any]) -> str
```

**Purpose**: Checks that the spawn payload matches the target’s input contract. A contract is a schema: a rulebook for what keys and value shapes are accepted.

**Data flow**: It receives the target name, contract, and payload dictionary → asks the contract to validate the payload → returns a JSON string for the child’s inbound message, or raises SpawnPayloadRejected with readable details about bad or missing fields.

**Call relations**: Subagents.spawn calls this before admitting a new profile child or non-replayed agent child, so invalid input is rejected before any work is queued.

*Call graph*: calls 1 internal fn (__init__); called by 1 (spawn); 2 external calls (model_validate, payload_keys).


##### `Subagents._existing_agent_spawn`  (lines 636–675)

```
async def _existing_agent_spawn(self, turn_id: UUID, target: str) -> tuple[AgentTarget, str] | None
```

**Purpose**: Reconnects a repeated agent spawn to the child turn already created with the same deterministic turn id. This prevents crash recovery from creating a second independent agent child.

**Data flow**: It receives the expected turn id and requested target → looks for an existing turn joined to its agent row → verifies it is an agent child for the same parent, target, and member authority → returns the saved AgentTarget and inbound JSON, or null if there is no matching agent spawn.

**Call relations**: Subagents.spawn calls this only when a deduplication key is present. If it returns a replay, spawn skips fresh resolution and validation and uses the original admitted request.

*Call graph*: called by 1 (spawn); 4 external calls (__init__, select, workspace_tx, authority_member_id).


##### `Subagents._profile_names`  (lines 677–678)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the registered profile names in sorted order. This is mainly for clear error messages that tell the caller what profiles are available.

**Data flow**: It reads the registry’s profiles → extracts their names → sorts them → returns them as a tuple.

**Call relations**: Subagents._resolve uses it when reporting unknown or ambiguous targets. Subagents._require_agent_spawn also uses it when an agent target has disappeared and an UnknownSpawnTarget error needs the available profile names.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `Subagents._agent_names`  (lines 680–692)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Lists the non-archived workspace agents that can be named as spawn targets. This helps explain target lookup failures to the caller.

**Data flow**: It opens a workspace database transaction → selects active agent names for the parent’s workspace → orders them by name → returns the names as a tuple.

**Call relations**: Subagents._resolve calls this when it needs to raise an UnknownSpawnTarget error that includes what agent names are currently spawnable.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 694–717)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Loads a workspace agent by name as a spawn target. It gathers just the facts needed to start the child: id, name, input schema, and output schema.

**Data flow**: It receives an agent name → queries the workspace database for a non-archived agent with that name → returns an AgentTarget if found, or null if no active agent matches.

**Call relations**: Subagents._resolve calls this while deciding whether a target string names an agent, a profile, both, or neither.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 719–725)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Reads the declared output schema for a workspace agent. This is needed later to validate what an agent child claimed as its final answer.

**Data flow**: It receives an agent id → opens a workspace transaction → selects that agent’s output schema → returns the schema, which may be null if the agent uses the default contract.

**Call relations**: Subagents.result calls this for agent children, because agent children do not use a profile’s output model.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._untrusted_output`  (lines 727–736)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child’s output should be treated as untrusted content. Untrusted means it should be walled off as quoted content, not treated as instructions to follow.

**Data flow**: It receives a profile name or null for an agent child → agent children always return true → profile children look up the current profile and return true if missing or marked untrusted, otherwise false.

**Call relations**: Subagents.result and Subagents.wait use this when reporting child results, so callers know whether the returned text needs extra caution.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 738–774)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Verifies that a turn id is a child spawned by this conversation and by the same member authority. This is the guard that stops one conversation or member from controlling another person’s child work.

**Data flow**: It receives a turn id → loads the turn’s parent, profile, and member stamp → checks whether it was spawned by this parent turn or a sibling turn in the same conversation → checks the authority member id → returns the child’s profile name, or null for an agent child, if allowed; otherwise raises an error.

**Call relations**: Subagents.result, wait, cancel, and message all call this before reading, waiting on, stopping, or continuing a child turn.

*Call graph*: called by 4 (cancel, message, result, wait); 3 external calls (select, workspace_tx, authority_member_id).


##### `Subagents._profile_model`  (lines 776–782)

```
def _profile_model(self, profile: str | None) -> str | None
```

**Purpose**: Finds the model pinned by a named profile, if the profile still exists. It returns null instead of failing when the profile has vanished, because existing child work should not be stranded by a manifest change.

**Data flow**: It receives a profile name or null → searches the current registry → returns the profile’s model when found, otherwise null.

**Call relations**: Subagents.message uses this when admitting a follow-up turn, so the balance check is based on the profile’s model when that information is still available.

*Call graph*: called by 1 (message).


##### `Subagents._require_balance`  (lines 784–803)

```
async def _require_balance(self, connection: AsyncConnection, model: str | None, agent_id: UUID | None=None) -> None
```

**Purpose**: Checks whether the workspace has enough prepaid balance or allowed billing capacity to start new work. It prevents a parent turn from spawning unpaid helper work.

**Data flow**: It receives a database connection, optional model, and optional child agent id → asks BalanceGate whether the workspace may admit this work under the correct agent and model → returns nothing on success or raises BalanceExhausted with the rejection message.

**Call relations**: Subagents._admit calls this for a newly admitted child turn. Subagents.message calls it for a new follow-up turn, but not when reconnecting to an already-admitted follow-up.

*Call graph*: called by 2 (_admit, message); 2 external calls (__init__, __init__).


##### `Subagents._admit`  (lines 805–934)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, request_fingerprint: str, delivers_result: bool=False, name:
```

**Purpose**: Creates the child conversation and first child turn in the database, or safely reconnects to existing rows for the same deduplication key. This is the durable admission step before queueing.

**Data flow**: It receives the child ids, target agent/profile facts, validated inbound JSON, delivery flags, name, and model → locks any existing turn → for agent targets, rechecks spawn permission → inserts conversation and turn rows without duplicating conflicts → verifies any existing row matches the same request and same authority → checks balance for genuinely new work → marks the queued turn as ready for dispatch → returns true if it should be enqueued.

**Call relations**: Subagents.spawn calls this before _enqueue. Inside, it calls _require_agent_spawn for workspace-agent permission checks and _require_balance before newly admitted work can run.

*Call graph*: calls 2 internal fn (_require_agent_spawn, _require_balance); called by 1 (spawn); 7 external calls (select, update, workspace_tx, current_traceparent, authority_member_id, conversation_name, audience_member).


##### `Subagents._require_agent_spawn`  (lines 936–987)

```
async def _require_agent_spawn(self, connection: AsyncConnection, target: AgentTarget, inbound: str) -> None
```

**Purpose**: Checks that the requester may spawn the named workspace agent and that the saved inbound still matches the agent’s current input schema. This protects private or restricted agents from being run by the wrong member.

**Data flow**: It receives a database connection, an AgentTarget, and inbound JSON → locks the agent row → rejects missing or archived agents → checks whether the requester owns the agent or is a workspace admin → validates the inbound JSON against the agent input contract → returns nothing if all checks pass.

**Call relations**: Subagents._admit calls this only for a new agent-child admission. It uses _profile_names when it needs to raise an unknown-target error that also mentions available profiles.

*Call graph*: calls 2 internal fn (_profile_names, __init__); called by 1 (_admit); 5 external calls (execute, select, authority_member_id, member_is_admin, input_contract).


##### `Subagents._enqueue`  (lines 989–1017)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Puts an admitted child turn onto the express DBOS workflow queue. DBOS is the workflow system used here to run durable background work.

**Data flow**: It receives a turn id and conversation id → builds enqueue options with the queue, workflow name, workflow id, and app version → asks DBOS to enqueue the turn → if enqueueing is cancelled or fails, clears the dispatch timestamp so another dispatcher can try later, and logs non-cancellation failures.

**Call relations**: Subagents.spawn calls this after _admit says the turn is still queued and needs dispatch. The cleanup path keeps database state honest if queue submission does not complete.

*Call graph*: called by 1 (spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 1019–1062)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has a terminal frame, meaning its durable final status and final text/question/error. It also handles workflow restarts and parked children.

**Data flow**: It receives a child turn id → tries to retrieve the DBOS workflow by id → waits for its result, polling periodically → after workflow events, checks the database for a terminal or parked status → follows a new running attempt id if the workflow was retried → returns the TerminalFrame or raises if the workflow ended without one.

**Call relations**: Subagents.spawn and Subagents.wait use this for foreground waits. Subagents._await_terminal_or_detach also starts it as one side of the race between child completion and a new member message.

*Call graph*: calls 2 internal fn (_running_attempt, _terminal_or_park); called by 3 (_await_terminal_or_detach, spawn, wait); 1 external calls (sleep).


##### `Subagents._running_attempt`  (lines 1064–1070)

```
async def _running_attempt(self, turn_id: UUID) -> str | None
```

**Purpose**: Reads the current workflow attempt id for a child turn. This helps the waiter follow a child that was restarted under a different workflow attempt.

**Data flow**: It receives a turn id → queries the turn row’s running_attempt field → returns that attempt id or null.

**Call relations**: Subagents._await_terminal calls this when the workflow it was watching is missing or ended before a terminal appeared.

*Call graph*: called by 1 (_await_terminal); 2 external calls (select, workspace_tx).


##### `Subagents._await_terminal_or_detach`  (lines 1072–1127)

```
async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Waits for a child result, but stops waiting if a member message arrives for the parent conversation first. In that case, it moves the child to background delivery so the parent can answer the member now.

**Data flow**: It receives a child turn id → if interrupting is not needed, delegates to _await_terminal → otherwise subscribes to the parent turn’s hub messages and starts _await_terminal in a task → waits for either child completion or an arrival notice → if a valid member arrival wins, calls _detach and returns null → otherwise returns the terminal frame.

**Call relations**: Subagents.spawn calls this for foreground spawns that allow detaching on arrival. It coordinates _await_terminal, the Hub subscription, and _detach, then cancels whichever async task is left over.

*Call graph*: calls 2 internal fn (_await_terminal, _detach); called by 1 (spawn); 5 external calls (create_task, ensure_future, gather, wait, log).


##### `Subagents._terminal_or_park`  (lines 1129–1145)

```
async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Checks whether a child has finished or has parked because it hit a spend limit. A parked child has no final answer, so this function cancels it rather than letting the parent wait forever.

**Data flow**: It receives a child turn id → reads the turn’s terminal and status from the database → returns a TerminalFrame if present → if status is parked, cancels the turn and raises SubagentParked → otherwise returns null to mean “still waiting”.

**Call relations**: Subagents._await_terminal calls this repeatedly after workflow polling events, using it as the database source of truth for whether the child is done.

*Call graph*: called by 1 (_await_terminal); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents._detach`  (lines 1147–1178)

```
async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool
```

**Purpose**: Marks a still-running foreground child so its result will be delivered later instead of returned inline. This is the database race check that decides whether the parent or background delivery will carry the answer.

**Data flow**: It receives a child turn id and an inbound arrival id → updates the child turn to DELIVERY_PENDING only if the child has no terminal, is not already marked for delivery, and the arrival is a valid unconsumed member message for the parent turn → returns true if exactly one row was changed.

**Call relations**: Subagents._await_terminal_or_detach calls this when the hub reports a member arrival. If it succeeds, spawn returns a detached result; if it fails, the child likely finished first and the inline wait continues.

*Call graph*: called by 1 (_await_terminal_or_detach); 4 external calls (exists, select, update, workspace_tx).


##### `SubagentResult.deliver`  (lines 1202–1244)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished child’s result back into the parent conversation as an ordinary incoming message. This is how background children, and detached foreground children, tell the parent what happened.

**Data flow**: It receives a finished child Turn → ignores it unless result delivery is pending and it has a parent → requires a committed terminal → loads the parent conversation and, for agent children, the child agent row → builds the delivery body → invokes the parent conversation with a deduplication key based on the child id → marks the child delivery as delivered.

**Call relations**: It calls SubagentResult._body to format the safe result envelope, then hands the message to TurnInvoker.invoke. The final database update happens after posting so a crash can retry delivery without silently losing the result.

*Call graph*: calls 1 internal fn (_body); 3 external calls (select, update, workspace_tx).


##### `SubagentResult._body`  (lines 1246–1275)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Formats the child’s terminal result into a tagged message that names the target, spawn id, and status. It also walls off untrusted output so the parent treats it as quoted content rather than instructions.

**Data flow**: It receives a child Turn and, for agent children, the agent row → chooses the target label and output contract → asks _payload for validated payload text and status → wraps untrusted payloads with wall() → escapes any closing result tag inside the payload → returns the complete spawn_result text block.

**Call relations**: SubagentResult.deliver calls this before invoking the parent conversation. It delegates result interpretation to SubagentResult._payload and uses output_contract for agent-child schemas.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (wall, output_contract).


##### `SubagentResult._payload`  (lines 1277–1296)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a terminal frame into the payload text and status that should be delivered to the parent. It makes failures, questions, missing profiles, and schema-invalid answers explicit.

**Data flow**: It receives an output contract or null plus a terminal frame → if the terminal is not done, returns an error diagnostic with that status → if the child asked a question, returns the structured question with question status → if no contract is available, returns an invalid-profile message → otherwise validates the terminal text against the contract and returns clean JSON, or an invalid-output message with validation faults.

**Call relations**: SubagentResult._body calls this while building the delivery envelope, so only checked output, structured questions, or clear failure messages reach the parent conversation.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).


### `core/src/ufo/runtime/turns/contracts.py`

`domain_logic` · `spawn, dispatch, and delivery validation`

When an agent starts or talks to another agent, both sides need to agree on what the message should look like. This file gives the system one common way to describe and check that shape, whether it comes from a normal Pydantic model or from a raw JSON Schema stored as data. Pydantic is a Python validation library: it checks that data has the expected fields and types. JSON Schema is a standard way to describe valid JSON data.

The default message shapes are simple: task input is an object with a string `task`, and result output is an object with a string `result`. Workspace agents can also declare their own schemas. Those schemas are wrapped in `JsonContract`, which behaves enough like a Pydantic model that the rest of the runtime can ask the same questions: “what schema do you have?”, “does this Python object fit?”, and “does this JSON text fit?”

The file is careful about safety. Declared schemas must be small, must describe a top-level object, and may not use references or regular expression patterns. That matters because references could make the server fetch outside resources, and complex patterns could make one shared serving loop spend too long matching text. In short, this file is the gatekeeper that keeps agent handoffs predictable, understandable, and safe.

#### Function details

##### `ValidatedJson.model_dump`  (lines 55–56)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated payload as a normal Python value. This lets a raw JSON Schema result act like a Pydantic model result, because callers can ask both for `model_dump()`.

**Data flow**: It starts with the `data` stored inside the `ValidatedJson` object. It does not change or re-check that data. It returns the same data object for the caller to use.

**Call relations**: This is part of the small adapter that makes raw-schema validation look like Pydantic validation. After `JsonContract.model_validate` has accepted a payload and wrapped it in `ValidatedJson`, later code can read it through this method just as it would read a validated Pydantic model.


##### `ValidatedJson.model_dump_json`  (lines 58–59)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the already-validated payload into JSON text. This gives callers the same kind of JSON output method they would expect from a Pydantic model.

**Data flow**: It reads the stored `data`, passes it to Python’s JSON encoder, and returns the resulting JSON string. The stored data is not changed.

**Call relations**: This method sits after successful validation. `JsonContract.model_validate` creates the `ValidatedJson` object, and code that needs a JSON string can call this method instead of treating raw-schema results differently.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 68–69)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the raw JSON Schema that describes this contract. Callers use it to show or inspect what keys and shapes a payload must have.

**Data flow**: It reads the schema stored in the `JsonContract`. It copies that mapping into a plain dictionary and returns it, so the caller receives the contract description rather than the internal object itself.

**Call relations**: This method is what lets `JsonContract` stand in for a Pydantic model class. `payload_keys` calls this shared interface so it can describe both Pydantic-based contracts and raw JSON Schema contracts in the same way.


##### `JsonContract.model_validate`  (lines 71–106)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks whether a Python value matches this contract’s JSON Schema. If it fits, it wraps the value in `ValidatedJson`; if it does not, it raises a Pydantic-style `ValidationError` so callers see the same error shape for every kind of contract.

**Data flow**: It takes an input value and the schema stored on the contract. It builds a JSON Schema validator with an empty reference registry, checks the value, sorts any problems by their JSON path, and converts those problems into Pydantic validation errors. If a schema reference cannot be resolved, that is also turned into a validation error. If there are no problems, it returns a `ValidatedJson` object containing the original value.

**Call relations**: This is the main validation path for raw JSON Schema contracts. `JsonContract.model_validate_json` calls it after parsing JSON text. It hands accepted data to `ValidatedJson` and hands rejected data back as Pydantic-shaped errors, matching the behavior expected from model-based contracts.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 108–124)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks whether a JSON string is valid JSON and also matches this contract’s schema. It is used when the payload arrives as text rather than as an already-parsed Python object.

**Data flow**: It receives JSON text. First it tries to parse the text with Python’s JSON parser. If parsing fails, it raises a Pydantic-style validation error that says the JSON itself is invalid. If parsing succeeds, it sends the parsed value to `JsonContract.model_validate` and returns that result.

**Call relations**: This is the text-entry version of raw-schema validation. It relies on `JsonContract.model_validate` for the actual schema check, so both parsed objects and JSON strings end up following the same validation rules and returning the same `ValidatedJson` wrapper.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `freeform_result_contract`  (lines 130–137)

```
def freeform_result_contract(contract: Contract) -> bool
```

**Purpose**: Answers whether a contract is the simple built-in shape of one string field named `result`. This lets the runtime recognize the most flexible result handoff form.

**Data flow**: It receives a contract. If the contract is a Pydantic model class, it inspects that model’s fields and checks that there is exactly one field, `result`, and that the field is a string. For raw JSON Schema contracts, it returns `False`.

**Call relations**: This helper is used when other code needs to distinguish the special freeform result contract from more specific contracts. It does not call other project helpers; it reads the model metadata directly and returns a yes-or-no answer.


##### `payload_keys`  (lines 140–156)

```
def payload_keys(contract: Contract) -> str
```

**Purpose**: Creates a short human-readable list of the keys a payload should contain. This is useful for messages shown to people or models, especially when explaining why a spawn or handoff was refused.

**Data flow**: It asks the contract for its JSON Schema through `model_json_schema()`. It reads the schema’s `properties` and `required` lists. It then returns a string where required keys are shown plainly, optional keys are marked as optional, and schemas with no visible keys become `(no keys)`.

**Call relations**: This function depends on the shared contract interface, so it works for both Pydantic model contracts and `JsonContract` raw schemas. It is the place where the system turns a formal payload shape into a compact description for catalogs or error messages.

*Call graph*: 1 external calls (model_json_schema).


##### `input_contract`  (lines 159–160)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract for an agent input payload. If no custom schema is declared, it uses the built-in task input shape; otherwise it wraps the declared schema as a `JsonContract`.

**Data flow**: It receives either a schema mapping or `None`. With `None`, it returns the default `TaskInput` model class. With a schema, it creates and returns a `JsonContract` around that schema.

**Call relations**: This is a small factory for input-side validation. Code that has read an agent’s declared input schema can call this once and then treat the returned value as a normal contract, without caring whether it came from a Pydantic model or raw JSON Schema.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 163–164)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract for an agent output payload. If no custom schema is declared, it uses the built-in freeform result shape; otherwise it wraps the declared schema as a `JsonContract`.

**Data flow**: It receives either a schema mapping or `None`. With `None`, it returns the default `AgentResultOutput` model class. With a schema, it creates and returns a `JsonContract` around that schema.

**Call relations**: This is the output-side companion to `input_contract`. It lets delivery code validate results through one contract interface, whether the output shape is the default `result` string or a custom declared schema.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 167–182)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Rejects unsafe or invalid custom schemas before they are stored. This keeps bad schema data from reaching the later spawn and delivery paths.

**Data flow**: It receives a candidate schema and the name of the field being checked. It serializes the schema to measure its size, confirms that it declares a top-level JSON object, searches for refused keywords such as references and patterns, and asks the JSON Schema library to compile-check the schema. If any check fails, it raises `ValueError`; if all checks pass, it returns nothing and leaves the schema accepted.

**Call relations**: This is the write-time safety gate for declared contracts. It calls `_refused_keyword` to find unsafe nested schema features, and it uses the JSON Schema validator’s schema checker to catch malformed schemas. By doing this early, later validation through `JsonContract` can stay predictable.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 185–197)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches a schema-like object for keywords this project does not allow, such as `$ref` or regular expression pattern fields. It helps enforce the rule that declared schemas must be self-contained and cheap to validate.

**Data flow**: It receives any nested value. If the value is a mapping, it checks each key and then recursively checks each child value. If the value is a list, it recursively checks each item. It returns the first refused keyword it finds, or `None` if the whole structure is clean.

**Call relations**: This is a private helper for `check_declared_schema`. That outer function uses its result to decide whether to reject a declared schema and to name the forbidden keyword in the error message.

*Call graph*: called by 1 (check_declared_schema).


### Extension delegation targets
Extension-level declarations and tools that expose browser, research, and website-building work as delegated subagent tasks.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file is the bridge between the main assistant and the browser automation worker. Instead of giving the main agent a raw browser to control, it lets the agent ask a separate “browser” subagent to do the work. That matters because browser sessions can be slow, messy, or get stuck on broken websites. This file puts those sessions behind clear limits, timeouts, and cancellation rules.

There are two public tools here. `browser_task` starts one fresh browser session for a single multi-step job, such as filling a form or extracting data from a site. It waits for the browser subagent to finish, but only up to a bounded time limit. If the browser gets stuck, the task is cancelled so the parent agent is not trapped waiting forever.

`wide_browse` is the batch version. It reads a workspace file containing URLs or site names, removes blank lines and duplicates, then starts several browser subagents at once, like sending a small team to check many shops instead of visiting them one by one. It limits the number running at the same time and writes the collected results to `wide_browse.json`.

A key detail is deduplication: each delegated browser job gets a stable key. If a run is recovered after a crash, the system can reconnect to already-started browser children instead of launching duplicates.

#### Function details

##### `_browser_task`  (lines 92–121)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation job by spawning a dedicated browser subagent and waiting for its summary. It protects the parent task by enforcing a timeout and cancelling the browser run if it takes too long.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the starting URL, task instructions, a friendly task name, and a timeout. It checks that subagent control is available, then asks the context to spawn a `browser` profile child with the task details. While it waits, it keeps a timeout clock running. If the clock expires, or if the child reports cancellation, it returns an error-style tool result saying the browser task was cancelled. If the child finishes normally, it reads the child’s text as a `BrowserResult`, turns that into JSON, and returns it as text content.

**Call relations**: This is the handler behind the `browser_task` tool. When a user or agent asks for a full browser session, this function calls `ToolContext.spawn` to start the browser child, waits through the subagent interface, and uses `BrowserResult.model_validate_json` to turn the child’s final report into a clean result for the caller.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 124–137)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. `wide_browse` uses it to get the list of URLs or site names to visit.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for the shell, runs `cat` inside the sandbox, and fails with a clear error if the file cannot be read. From the file contents, it trims whitespace, skips empty lines, removes duplicates while keeping the first occurrence, and returns the resulting list of strings.

**Call relations**: `_wide_browse` calls this first, before starting any browser jobs. It acts like the batch tool’s intake desk: it checks the list of targets and hands back a tidy set of entities for the rest of the flow.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 140–167)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs the same kind of browser extraction over many URLs or site names in parallel, then saves all results into a JSON file. It is meant for bounded batch browsing rather than one long browser session.

**Data flow**: It receives the tool context and a `WideBrowseInput` with an entities file, a prompt template, and a JSON schema file path. It reads and cleans the entity list through `_read_lines`, rejects the job if there are too many entries, and tries to read the schema file from the sandbox. It creates a semaphore, which is a small gate that only lets a fixed number of browser jobs run at once. For each entity, it runs the nested `visit` function, gathers all returned rows, writes them as formatted JSON to `wide_browse.json`, and returns a tool result containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool. It relies on `_read_lines` for the input list, uses `asyncio.Semaphore` to control parallelism, uses `asyncio.gather` to wait for all entity visits, and finally uses sandbox file writing plus `ToolResult` text content to hand the batch result back to the caller.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 148–161)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs one browser subagent job for one entity in the wider batch. It turns a single URL or site name into a browser task and returns one result row.

**Data flow**: It receives one entity string from the batch list. After entering the semaphore gate, it fills the prompt template by replacing `{entity}` with that entity. If an output schema was read successfully, it appends instructions asking the browser subagent to match that schema. It then spawns a `browser` profile child with a stable deduplication key based on the parent call and entity. When the child returns, it builds a dictionary containing the original entity and the child’s JSON output, or an empty string if there was no output.

**Call relations**: `_wide_browse` creates this helper and runs one copy for each entity through `asyncio.gather`. Each `visit` call is one member of the batch team: it performs the individual delegated browser run and hands its row back so `_wide_browse` can collect and write the full table.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup/config load`

This file is like a job description and equipment list for research helpers. The main agent can hand a research task to a smaller, focused subagent instead of doing all the searching itself. Without this file, the system would not know that these research subagents exist, what instructions to give them, or which tools they are allowed to touch.

It defines two profiles. The regular `research` profile is meant for focused research jobs. The `deep_research` profile is meant for longer, multi-source investigations, so it gets a much larger round limit, meaning it can take more back-and-forth steps before it must stop.

Both profiles use the same basic input and output format. The input is an `objective`, which is the research task in plain text. The output is a `result`, described using the shared subagent result rules from the wider system.

The file also chooses the tool set available to these agents: web search, URL fetching, browser tasks, external tools, file tools, memory search, and spreadsheet-style help. It deliberately does not give them every browser capability; some browsing powers stay with other agents. Finally, it loads the actual instruction prompts from Markdown files beside this code, then packages everything into `SubagentProfile` objects that the rest of the system can register and run.


### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file is a small bridge between the main agent and a specialized website-building helper. Instead of making the main agent do every website task itself, it exposes a tool called `build_website`. The tool takes a clear build objective, such as what pages to create and how the site should look or behave, then starts a child agent profile dedicated to building, running, checking, and deploying websites.

The important idea is that the child agent gets its own conversation history, but not a separate filesystem. It works in the same sandbox, like asking a specialist coworker to sit at the same desk and use the same project folder. When the child finishes, the files it created and any registered site are still available to the parent conversation.

`BuildWebsiteInput` describes exactly what the caller can provide: the build goal, an optional friendly task name, optional skills to preload so the child starts with useful instructions, and an optional larger work budget for bigger projects. The file then registers a `ToolDef` named `build_website`, marks it as side-effecting because it can create files and start a hosted site, and binds it to site objects. It also uses an idempotency key when spawning the child, so if the same tool call is retried after a crash, the system can reconnect to the already-started child instead of accidentally launching a duplicate build.

#### Function details

##### `_build_website`  (lines 56–63)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the actual tool function behind `build_website`. It starts the website-building subagent with the user's build instructions, waits for that child session to finish, and returns the child's summary as the tool result.

**Data flow**: It receives the tool context, which contains runtime information such as the idempotency key, and a validated `BuildWebsiteInput` object containing the build objective and options. It turns the input into a plain data payload, leaves out any options that were not provided, and passes that payload to `ctx.spawn` to start the `website_building` child profile. When the child returns, it converts the child's output to JSON text if there is output, or to an empty string if there is none, then wraps that text in a `ToolResult` for the caller.

**Call relations**: This function is called when the registered `build_website` tool is invoked. Its main handoff is to `ToolContext.spawn`, which creates or reconnects to the child website-building turn using the current call's idempotency key. After the child finishes, `_build_website` packages the child result with `TextContent` and `ToolResult` so the tool system can send the summary back to the parent agent.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).

## 📊 State Registers Touched

- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-turn-queue` — The durable queue of conversation turns waiting, running, parked, resumed, or blocked as duplicates.
- `reg-live-workflow-state` — The shared run-state for active turns, including locks, progress, retry guards, cancellation, and completion markers.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-browser-sessions` — The active or reusable Chrome browser sessions, tabs, downloads, and remote-control connections used by agents.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-subagent-state` — The parent-child turn links, delegation contracts, spawn identities, and pending result deliveries for helper agents.
