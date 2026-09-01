# Subagent Dispatch and Recovery  `stage-11.1`

This stage is shared behind-the-scenes support for delegation. It lets the main agent hand off focused jobs to smaller “subagents,” which are child agents with their own instructions, tools, and expected input and output. The fallback profile in profiles.py is the default worker plan when no special one is supplied. subagents.py is the dispatcher: it checks that a child agent is allowed, validates the data being sent, creates the child conversation, queues the work, waits if needed, and returns the result to the parent. delivery.py is the safety net, sweeping for completed child turns whose results were saved but not delivered, so the parent does not wait forever.

The extension files plug in specialized workers. The browser files define a browser subagent and tools for sending one browsing session or many parallel visits. The research files define normal and deep research workers, plus wide_research, which fans out many research tasks and safely collects results into one JSON file. The sites files define a website-building worker and the build_website tool that sends site creation work to it.

## Files in this stage

### Core Subagent Runtime
Defines the default subagent profile, the parent/child dispatch lifecycle, and recovery delivery for completed child turns.

### `core/src/ufo/runtime/profiles.py`

`config` · `startup and subagent dispatch`

This file is the default job description for a spawned subagent. A subagent is a helper agent that the main agent can give a smaller, self-contained task to, much like asking a coworker to investigate one part of a larger problem.

The profile is called `general_purpose`. It is meant to be useful for many tasks, but deliberately limited. It can read and write files, search through the workspace, run shell commands, load skills, share files, and use optional extension tools such as web search or spreadsheet support if those extensions are installed. It is not allowed to ask the user questions, create more subagents, message or cancel sibling subagents, or approve account connections. That keeps it focused and prevents a helper from taking over coordination duties that belong to the parent agent.

The file also builds the prompt text the subagent sees. The prompt tells it to work independently, make reasonable assumptions, avoid repeated failed attempts, load relevant skills first, and save useful results into the shared `/workspace` directory. Finally, the file packages all of this into a `SubagentProfile`, including the expected task input shape and result output shape. Without this file, the system would have no built-in fallback profile for ordinary delegated work.


### `core/src/ufo/runtime/subagents.py`

`orchestration` · `request handling and background result delivery`

This file is the runtime machinery behind “spawn a helper agent.” A parent turn can ask for a named profile, such as a specialist prompt with a limited tool set, or a full workspace agent. The code checks that the target exists, that the caller is allowed to use it, that the input matches the target’s declared shape, and that the workspace has enough balance to pay for the work. It then creates a child conversation and first child turn in the database, queues that turn on the fast queue, and either returns immediately or waits for the child to finish.

The file treats foreground and background work differently. Foreground work is like running a command and waiting for its answer. Background work is like starting a job and getting a job number; when it finishes, its result is posted back into the parent conversation. There is also an interruptible foreground mode: if a human message arrives while the parent is waiting, the child keeps running in the background so the parent can respond to the human.

A major safety theme is “do not trust raw child output blindly.” Finished answers are checked against output contracts, which are structured schemas. Output from agents, unknown profiles, or explicitly untrusted profiles is wrapped so the parent sees it as quoted content rather than new instructions.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 142–146)

```
def __post_init__(self) -> None
```

**Purpose**: Checks the list of registered subagent profiles as soon as the registry is created. It prevents two profiles from using the same name, because that would make a spawn request unclear.

**Data flow**: It reads the profile names inside the new registry → looks for repeated names → either leaves the registry usable or raises an error naming the duplicates.

**Call relations**: This runs automatically when a SubagentRegistry is built, before any lookup or spawn uses it. It protects later calls from having to guess which profile a name means.


##### `SubagentRegistry.get`  (lines 148–154)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Looks up a subagent profile by name and fails loudly if it does not exist. This is used when code needs a definite profile, not a maybe.

**Data flow**: It receives a profile name → asks find for the matching profile → returns that profile if found, or raises an UnknownSubagentProfile error with the valid names.

**Call relations**: It relies on SubagentRegistry.find for the actual search. Runtime queue code calls it when resolving a stored profile name and needs to stop if the profile is no longer registered.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 156–157)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Searches for a profile by name and returns nothing if there is no match. This is the gentle lookup used when absence is an expected possibility.

**Data flow**: It receives a name → scans the registry’s profiles → returns the first profile with that name, or None.

**Call relations**: SubagentRegistry.get builds on this and turns a missing result into an error. Other parts of the file also use this kind of lookup when checking whether old or optional profiles still exist.

*Call graph*: called by 1 (get).


##### `_target_model`  (lines 171–179)

```
def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None
```

**Purpose**: Figures out whether a spawn target explicitly chooses a model. A model is the underlying AI service or configuration used to answer.

**Data flow**: It receives either a profile target or an agent target → if it is a profile, it returns the profile’s pinned model if any → if it is an agent, it returns None because the agent’s own row supplies the model elsewhere.

**Call relations**: Subagents.spawn uses this while admitting new child work, so billing and execution can be tied to the model that will actually run the child.

*Call graph*: called by 1 (spawn).


##### `subagent_system_prompt`  (lines 182–218)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the instruction text that a profile-based child agent receives. It combines the profile’s prompt, optional skill information, safety/output rules, and a final instruction to end by calling the finish tool.

**Data flow**: It receives a SubagentProfile plus optional skill lists and preloaded skill bodies → fills the skill-index slot, checks for unresolved prompt placeholders, optionally appends preloaded skill text, enforces a size limit, and appends the output rules → returns one complete system prompt string.

**Call relations**: It calls the prompt-rendering helpers to build the skill index and find unfinished placeholders, and uses loaded_context to insert preloaded skill text. It is used by the broader runtime when preparing a spawned profile child’s prompt.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 240–241)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: Returns a copy of the Subagents helper stamped with the member whose authority should be used. This lets later permission checks use the right human identity.

**Data flow**: It receives a requester member id → copies the current Subagents object with that id set → returns the new copy without changing the old one.

**Call relations**: It is a small setup step before spawning, messaging, or cancelling. Later properties and checks, especially acting_member_id and ownership gates, read this value.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 244–253)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Chooses which workspace member the spawn is acting for. This matters because agent ownership, admin rights, and personal model keys are checked against a member.

**Data flow**: It reads the explicitly authorized requester first → if missing, falls back to the parent turn’s speaker → if that is missing, falls back to the parent turn’s on-behalf-of member → returns the chosen member id or None.

**Call relations**: Many other methods use this property when admitting work, checking ownership, validating deduplication, or stamping child turns.


##### `Subagents.spawn`  (lines 255–402)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnResult
```

**Purpose**: Creates and starts a child task. It is the main entry point for spawning either a subagent profile or a workspace agent.

**Data flow**: It receives a target name, input payload, and options such as background mode or a deduplication key → chooses or recreates the child ids, resolves the target, validates the input, records the child conversation and turn, queues the workflow, and then either returns the child id immediately or waits for a validated final answer → returns a SpawnResult with ids, output if available, terminal details, and trust information.

**Call relations**: This is the central coordinator. It calls target resolution, prior-spawn recovery, admission, enqueueing, foreground waiting, interruptible detach waiting, and model selection helpers. If a foreground wait fails, it cancels the child so abandoned work is not left running.

*Call graph*: calls 8 internal fn (_admit, _await_terminal, _await_terminal_or_detach, _enqueue, _existing_agent_spawn, _resolve, _target_model, __init__); 11 external calls (__init__, __init__, sha256, dumps, cancel_one_turn, input_contract, output_contract, ws_current, turn_id_for, uuid4 (+1 more)).


##### `Subagents.result`  (lines 404–441)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the finished result of a child that this conversation spawned. It gives host-side code a trustworthy structured result without relying on the parent model to repeat it correctly.

**Data flow**: It receives a child turn id → verifies that the turn belongs to this spawning conversation → loads its terminal record and the right output contract → validates the terminal text when possible → returns a SpawnResult with either structured output or None if the child failed, asked a question, or produced invalid output.

**Call relations**: It uses _require_child to enforce ownership, _agent_output_schema for agent children, and _untrusted_output to mark safety. It is the read-only counterpart to spawn and delivery.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, workspace_tx, output_contract).


##### `Subagents.wait`  (lines 443–463)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits for one or more child turns to finish and reports their final status and text. This is for tools that intentionally keep the current turn open while child work completes.

**Data flow**: It receives child turn ids → verifies each one belongs to this conversation → waits for each terminal record → returns a tuple of SubagentStatus objects with status, final text, and whether the output should be treated as untrusted.

**Call relations**: It depends on _require_child before waiting and _await_terminal during waiting. It is not the normal result-delivery path; background children usually deliver their own results later.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 465–482)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a child turn that this conversation spawned. It lets a parent stop child work safely through the shared cancellation path.

**Data flow**: It receives a child turn id → verifies the child relationship → asks the cancellation system to stop the turn → reloads the stored status and any terminal text → returns a SubagentStatus describing what happened.

**Call relations**: It uses _require_child to prevent cancelling someone else’s child. It hands off the actual durable cancellation to cancel_one_turn, the shared turn-cancellation primitive.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents.message`  (lines 484–599)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child’s own conversation. This is how a parent answers a child’s question or continues a spawned helper without starting over.

**Data flow**: It receives the original child turn id, message text, a deduplication key, and a delivery flag → verifies the child, checks that any profile still exists, locks the child conversation, finds or creates the next turn in that conversation, checks balance for new work, and queues it if needed → returns the follow-up turn id and status.

**Call relations**: It uses _require_child for permission, _profile_model and _require_balance for billing, and dispatch_next_turn to wake the child conversation. Its deduplication check prevents crash recovery from creating duplicate follow-up turns.

*Call graph*: calls 3 internal fn (_profile_model, _require_balance, _require_child); 8 external calls (__init__, model_validate, insert, select, workspace_tx, current_traceparent, dispatch_next_turn, turn_id_for).


##### `Subagents._resolve`  (lines 601–624)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Turns a spawn target string into the actual thing to run: either a profile or a workspace agent. It also catches unclear names.

**Data flow**: It receives a target string, possibly like profile:name or agent:name → checks the registry and/or workspace agents → returns the matching profile or agent target → raises a clear error if the name is unknown or ambiguous.

**Call relations**: Subagents.spawn calls this before admission. It uses _profile_names and _agent_names to produce helpful error messages, and _agent_target to load agent details.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._existing_agent_spawn`  (lines 626–665)

```
async def _existing_agent_spawn(self, turn_id: UUID, target: str) -> tuple[AgentTarget, str] | None
```

**Purpose**: Checks whether a deduplicated agent spawn was already created before. This lets a retried tool call reconnect to the same child instead of creating a duplicate.

**Data flow**: It receives the expected child turn id and requested target → loads any existing turn and agent row → verifies it belongs to the same parent, member, and target request → returns the saved AgentTarget plus saved inbound text, or None if this is not an existing agent spawn.

**Call relations**: Subagents.spawn calls this only when a deduplication key is present. It supports crash recovery and retry safety for spawned workspace agents.

*Call graph*: called by 1 (spawn); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._profile_names`  (lines 667–668)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the known profile names in sorted order. This is mainly for clear error messages.

**Data flow**: It reads the registry’s profile list → extracts and sorts names → returns them as a tuple.

**Call relations**: _resolve and _require_agent_spawn use it when they need to explain what targets are available.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `Subagents._agent_names`  (lines 670–682)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Loads the active workspace agent names. This helps error messages tell the caller what agent targets can be spawned.

**Data flow**: It opens a workspace database transaction → selects non-archived agents in the parent workspace → returns their names in order.

**Call relations**: _resolve calls this when it needs to report an unknown target alongside valid agent names.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 684–707)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Looks up one active workspace agent by name and returns the details needed to spawn it.

**Data flow**: It receives an agent name → queries the workspace database for a non-archived agent with that name → returns an AgentTarget with id, name, input schema, and output schema, or None.

**Call relations**: _resolve uses this to decide whether a target string names a workspace agent.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 709–715)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Loads the output schema for a workspace agent. The output schema is the declared shape of the agent’s final answer.

**Data flow**: It receives an agent id → queries the database for that agent’s output_schema column → returns the schema or None.

**Call relations**: Subagents.result uses this when validating the final output of an agent child, because agent children do not use profile output models.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._untrusted_output`  (lines 717–726)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child’s output must be treated as untrusted content. Untrusted means the text should be walled off so it is not accidentally followed as instructions.

**Data flow**: It receives a profile name or None → returns True for agent children, True for missing/unknown profiles, or the profile’s untrusted_output setting for known profiles.

**Call relations**: Subagents.result and Subagents.wait call this when reporting child output safety. It makes safety fail closed when a profile has disappeared.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 728–764)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Verifies that a given turn id is a child of this conversation and was spawned under the same member authority. It prevents one conversation or member from controlling another’s child.

**Data flow**: It receives a turn id → loads the turn’s parent id, profile, and acting member → checks direct parentage or sibling-conversation parentage for delivered-result follow-ups → checks the member matches → returns the child’s profile name, or None for an agent child, otherwise raises an error.

**Call relations**: The public result, wait, cancel, and message methods all call this before touching a child turn. It is the main access gate for child operations after spawn.

*Call graph*: called by 4 (cancel, message, result, wait); 2 external calls (select, workspace_tx).


##### `Subagents._profile_model`  (lines 766–772)

```
def _profile_model(self, profile: str | None) -> str | None
```

**Purpose**: Finds the model pinned by a profile name, if that profile is still registered. It is used for billing follow-up work.

**Data flow**: It receives a profile name or None → searches the registry → returns the profile’s model if found, otherwise None.

**Call relations**: Subagents.message calls this when creating a follow-up turn, so the balance check is weighed against the model likely to answer.

*Call graph*: called by 1 (message).


##### `Subagents._require_balance`  (lines 774–793)

```
async def _require_balance(self, connection: AsyncConnection, model: str | None, agent_id: UUID | None=None) -> None
```

**Purpose**: Checks that the workspace has enough prepaid balance or allowed billing state to start new child work. It stops new work before it can run for free.

**Data flow**: It receives a database connection, optional model, and optional agent id → asks BalanceGate whether the turn is admitted for that workspace, agent, key slot, and model → returns nothing if allowed or raises BalanceExhausted if rejected.

**Call relations**: _admit calls this for new spawned children, and message calls it for new follow-up turns. Existing deduplicated work is not rechecked, so retries do not get stranded by later balance changes.

*Call graph*: called by 2 (_admit, message); 2 external calls (__init__, __init__).


##### `Subagents._admit`  (lines 795–924)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, request_fingerprint: str, delivers_result: bool=False, name:
```

**Purpose**: Records a child conversation and first child turn in the database, if they do not already exist. This is the durable “admission ticket” for spawned work.

**Data flow**: It receives child ids, target agent/profile details, serialized input, delivery options, and model information → checks agent-spawn permission if needed, inserts the conversation and turn without duplicating existing rows, verifies any existing row matches the same request, checks balance for brand-new work, stamps the turn as ready for dispatch → returns True if it should be enqueued, False if it was already past the queued state.

**Call relations**: Subagents.spawn calls this before enqueueing. It calls _require_agent_spawn for workspace-agent permission and input validation, and _require_balance before new work is allowed to proceed.

*Call graph*: calls 2 internal fn (_require_agent_spawn, _require_balance); called by 1 (spawn); 6 external calls (select, update, workspace_tx, current_traceparent, conversation_name, audience_member).


##### `Subagents._require_agent_spawn`  (lines 926–978)

```
async def _require_agent_spawn(self, connection: AsyncConnection, target: AgentTarget, inbound: str) -> None
```

**Purpose**: Checks whether the acting member is allowed to spawn a workspace agent, and validates the inbound payload against that agent’s input schema.

**Data flow**: It receives a database connection, an AgentTarget, and serialized inbound JSON → locks and reads the agent row → rejects missing or archived agents, rejects callers who neither own the agent nor are workspace admins, validates the input JSON → returns nothing if all checks pass.

**Call relations**: _admit calls this only for new agent-child admission. It uses member_is_admin for the admin exception and input_contract to enforce the agent’s declared input shape.

*Call graph*: calls 2 internal fn (_profile_names, __init__); called by 1 (_admit); 4 external calls (execute, select, member_is_admin, input_contract).


##### `Subagents._enqueue`  (lines 980–1008)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Asks DBOS, the durable workflow system, to run the child turn on the express queue. If enqueueing fails, it leaves the database in a state where dispatch can try again later.

**Data flow**: It receives the child turn id and conversation id → builds workflow enqueue options → calls the DBOS client → if cancelled or failed, clears the dispatch-enqueued timestamp on the queued turn and either re-raises cancellation or logs a deferred enqueue.

**Call relations**: Subagents.spawn calls this after _admit says the child should be queued. It is the bridge from database admission to actual background execution.

*Call graph*: called by 1 (spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 1010–1053)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has a terminal record, meaning it has finished, failed, cancelled, or otherwise ended. It also handles workflow retries and parked turns.

**Data flow**: It receives a child turn id → tries to watch the DBOS workflow result → repeatedly checks the database for a terminal or parked state → follows a newer running attempt if the workflow was retried → returns the TerminalFrame when found, or raises if the workflow ended without one.

**Call relations**: Subagents.spawn and Subagents.wait use this for foreground waiting. _await_terminal_or_detach also wraps it when waiting can be interrupted by a human message.

*Call graph*: calls 2 internal fn (_running_attempt, _terminal_or_park); called by 3 (_await_terminal_or_detach, spawn, wait); 1 external calls (sleep).


##### `Subagents._running_attempt`  (lines 1055–1061)

```
async def _running_attempt(self, turn_id: UUID) -> str | None
```

**Purpose**: Reads the current workflow attempt id for a turn. This helps waiting code follow a child if execution was retried under a new attempt.

**Data flow**: It receives a turn id → queries the turn row’s running_attempt field → returns that workflow id or None.

**Call relations**: _await_terminal calls this when the workflow it was watching no longer explains the child’s state.

*Call graph*: called by 1 (_await_terminal); 2 external calls (select, workspace_tx).


##### `Subagents._await_terminal_or_detach`  (lines 1063–1118)

```
async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Waits for a child to finish unless a member message arrives for the parent first. If a message arrives, it can move the child to background delivery and let the parent respond now.

**Data flow**: It receives a child turn id → starts one task waiting for the child terminal and another listening to the parent conversation’s hub subscription → if the child finishes first, returns the terminal → if a valid member arrival comes first and _detach succeeds, returns None → always cleans up the background wait tasks.

**Call relations**: Subagents.spawn uses this for interruptible foreground spawns. It calls _await_terminal for normal completion and _detach to safely convert the child into a result-delivering background task.

*Call graph*: calls 2 internal fn (_await_terminal, _detach); called by 1 (spawn); 5 external calls (create_task, ensure_future, gather, wait, log).


##### `Subagents._terminal_or_park`  (lines 1120–1136)

```
async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Checks the database for a child’s terminal record, and treats a parked child as a stopped child that should be cancelled. Parked here means work stopped on a spend limit without a final answer.

**Data flow**: It receives a turn id → loads terminal and status → returns a TerminalFrame if present → if status is PARKED, cancels the turn and raises SubagentParked → otherwise returns None.

**Call relations**: _await_terminal calls this repeatedly while waiting on workflow state. It prevents the parent from waiting forever on a child that cannot finish.

*Call graph*: called by 1 (_await_terminal); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents._detach`  (lines 1138–1169)

```
async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool
```

**Purpose**: Marks a still-running child so its result will be delivered later instead of returned inline. This is used when a human message interrupts a foreground wait.

**Data flow**: It receives a child turn id and an arrival id → performs one guarded database update that only succeeds if the child has no terminal, has not already been marked for delivery, and the arrival is a real unconsumed member message for the parent turn → returns True if the child was moved to background delivery.

**Call relations**: _await_terminal_or_detach calls this to resolve the race between “child finished” and “human message arrived.” If it returns False, the normal terminal path wins.

*Call graph*: called by 1 (_await_terminal_or_detach); 4 external calls (exists, select, update, workspace_tx).


##### `SubagentResult.deliver`  (lines 1193–1235)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished background child’s result back into the parent conversation. This lets the parent receive the child’s answer as a normal new arrival instead of holding a turn open.

**Data flow**: It receives a child Turn → ignores it unless result delivery is pending and it has a parent → requires a committed terminal → loads the parent conversation and, for agent children, the agent schema/name → builds a safe result body → invokes a new parent-conversation turn with a stable idempotency key → marks the child delivery as delivered.

**Call relations**: It calls _body to format and validate the delivered message, then hands the message to TurnInvoker. A later sweep can safely retry because the invocation key is based on the child turn id.

*Call graph*: calls 1 internal fn (_body); 3 external calls (select, update, workspace_tx).


##### `SubagentResult._body`  (lines 1237–1266)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Builds the actual text envelope delivered to the parent conversation for a finished child. The envelope identifies which spawn answered and whether the result was done, invalid, a question, or an error.

**Data flow**: It receives the child turn and, for agent children, the agent row → chooses the target label and output contract → asks _payload for the validated payload and status → wraps untrusted payloads in a safety wall → escapes closing tags inside the payload → returns one formatted spawn_result block.

**Call relations**: SubagentResult.deliver calls this before invoking the parent conversation. It calls _payload for status-specific content and uses walling for untrusted profile or agent output.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (wall, output_contract).


##### `SubagentResult._payload`  (lines 1268–1287)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a child’s terminal record into the payload and status that should be delivered. It prefers structured, schema-checked data over raw prose.

**Data flow**: It receives an optional output contract and a terminal frame → if the child did not finish successfully, returns a diagnostic and the terminal status → if the child asked a question, returns the question JSON and question status → if there is no contract, returns an unknown-profile warning → otherwise validates terminal text against the contract and returns clean JSON, or an invalid-output message with validation faults.

**Call relations**: SubagentResult._body calls this while constructing the delivery message. It is the final validation gate before a background child’s answer reaches the parent.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).


### `core/src/ufo/runtime/delivery.py`

`orchestration` · `periodic background job`

When one agent delegates work to a child agent, the child normally sends its result back as it finishes. But some endings happen outside the child’s own execution path, such as cancellation from another process or a crash after the result was saved. In those cases, the result can be safely stored in the database but never delivered to the parent conversation. This file is the backstop for that gap.

`DeliverySweep` is the body of a periodic job. It looks in durable database state, not in memory, for child turns that are finished, still marked as waiting for result delivery, and whose parent agent is not archived. It groups those child turns by the parent conversation, so if several children finished around the same time, the parent can be woken once with the whole batch rather than many separate times.

The sweep also uses a short cooldown. If a conversation was already woken by a delivered child recently, the sweep leaves its other pending children for a later pass. This prevents a rapid loop where waking a parent immediately creates more children that wake it again.

If the parent agent has been archived, delivery is skipped rather than treated as a fatal error. The pending child remains pending, so if the app is restored later, the owed result can still be delivered.

#### Function details

##### `DeliverySweep.run`  (lines 54–72)

```
async def run(self) -> None
```

**Purpose**: Runs one full delivery sweep for the current workspace. It finds finished child turns whose results have not reached their parent, skips parent conversations that were just woken, and asks `SubagentResult` to deliver each remaining child result.

**Data flow**: It starts with no direct input besides the current workspace and the sweep’s stored dependencies: an invoker factory and a subagent registry. It reads outstanding child turns from `_outstanding`, calculates a recent-time cutoff, asks `_woken_since` which parent conversations were already woken recently, then creates a `SubagentResult` delivery helper for the current workspace. For each still-eligible child turn, it attempts delivery. The function returns nothing, but it may change database state and wake parent conversations through the delivery helper. If delivery discovers that the parent agent is archived, it skips that child for now instead of stopping the whole sweep.

**Call relations**: This is the main action for the scheduled result-delivery job. It calls `_outstanding` first to learn what work exists, calls `_woken_since` to avoid waking the same conversation too often, uses `ws_current` to choose the right workspace invoker, and hands each chosen child turn to `SubagentResult.deliver` for the actual result posting.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 74–93)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have at least one finished child turn waiting for result delivery. A scheduler can use this to avoid running the sweep in workspaces where there is nothing to do.

**Data flow**: It opens an owner-level database transaction, which can see workspace metadata across the system. It searches for turns marked as pending delivery, already terminal, and belonging to a parent agent that has not been archived. It returns a tuple of workspace IDs that match, with duplicates removed.

**Call relations**: This function is a scout for the wider job system. It does not deliver anything itself; it tells the scheduler where `DeliverySweep.run` is worth running. It uses SQLAlchemy to build the database query and `owner_tx` to run that query in the owner database context.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 95–131)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: Collects the actual child turns in the current workspace that are finished but not yet delivered. It groups them by the parent conversation that needs to receive them.

**Data flow**: It opens a workspace database transaction and selects child turns whose result delivery is still pending, whose terminal result exists, and whose parent agent is not archived. It also reads the parent turn’s conversation ID, orders the rows so each parent conversation’s children are together and in finish order, and limits the batch size. It then turns each database row into a `Turn` record and returns a dictionary: parent conversation ID → list of child turns waiting to be delivered.

**Call relations**: `DeliverySweep.run` calls this at the start of a sweep to get the work list. This helper uses `workspace_tx` because it only needs data inside the current workspace, and it uses `Turn.model_validate` to convert raw database fields into the project’s normal turn object before handing them back to the runner.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 133–160)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Checks which parent conversations have already been woken by a child result after a given time. This protects the system from repeatedly waking the same conversation too quickly.

**Data flow**: It receives a cutoff time and a tuple of conversation IDs to check. It opens a workspace database transaction and looks for delivered child turns connected to those parent conversations whose update time is newer than the cutoff. It returns a frozen set of conversation IDs that were recently woken.

**Call relations**: `DeliverySweep.run` calls this after finding outstanding work and before delivering more results. The returned set tells `run` which conversation groups to skip for this pass. It uses SQLAlchemy for the query and `workspace_tx` to read the current workspace’s turn records.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### Browser Delegation
Exposes browser-work delegation tools and the specialized browser subagent profile they dispatch to.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file is the bridge between the main agent and a separate browser worker. Instead of giving the main agent direct control of a browser, it asks a browser subagent to do the work and report back. That matters because browser automation can get slow, stuck, or branch into many steps, so it needs clear time limits and a safe way to cancel it.

There are two public tools here. `browser_task` is for one larger web task, such as opening a site, filling forms, clicking through pages, and returning a summary. It starts a fresh browser session, waits for the child task, and cancels it if it runs too long. The timeout has both a lower and upper limit, like giving the browser enough time to be useful but not enough to hold the whole system hostage.

`wide_browse` is for batch work. It reads a workspace file containing URLs or names, removes blanks and duplicates, then starts several browser subagents at once, with a fixed limit so it does not flood the system. Each child receives a prompt made from a template, optionally plus a JSON schema describing the desired output shape. The collected results are written to `wide_browse.json` and also returned to the caller.

A key safety detail is idempotency: repeated or recovered calls use stable keys, so the system can reconnect to already-started child work instead of accidentally launching duplicates.

#### Function details

##### `_browser_task`  (lines 92–121)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation job through the browser subagent. It is used when a caller wants a focused web session with a starting URL, instructions, a friendly task name, and a time limit.

**Data flow**: It receives the tool context and a `BrowserTaskInput` object containing the URL, task instructions, task name, and timeout. It checks that subagent control is available, starts a browser-profile child turn with the task details, then waits for that child to finish within the timeout. If the wait expires or the child is cancelled, it returns an error-style tool result saying the task was cancelled; if the child finishes normally, it parses the browser result text and returns it as tool output.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the tool is invoked, it uses the shared spawn path to start the browser subagent, waits for that child turn, and then packages the child’s final browser result into a normal tool response for the parent agent.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 124–137)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. `wide_browse` uses it to get the URLs or site names that should be visited.

**Data flow**: It receives the tool context and a file path. It reads the file through the sandbox shell, safely quotes the path so special shell characters are treated as part of the filename, and raises an error if the read fails. It then trims whitespace from each line, skips empty lines, removes duplicates while preserving first-seen order, and returns the resulting list.

**Call relations**: `_wide_browse` calls this first, before it starts any browser children. This helper gives `_wide_browse` a trustworthy list of entities, so the batch step does not waste work on blank lines or repeated entries.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 140–167)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs many small browser tasks in parallel and gathers their answers into one JSON result file. It is meant for jobs like visiting a list of companies, URLs, or products and extracting the same kind of information from each.

**Data flow**: It receives the tool context and a `WideBrowseInput` object containing an entities file, a prompt template, and a schema file path. It reads and deduplicates the entities, refuses to continue if there are more than the configured limit, reads the optional output schema, and creates a semaphore, which is a counter-like lock that limits how many visits run at the same time. It then launches one visit task per entity, waits for them all, writes the collected rows to `wide_browse.json`, and returns both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It relies on `_read_lines` to prepare the batch, uses its nested `visit` function for each individual browser run, waits for all visits together, and finally hands the combined results back to the caller.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 148–161)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser subagent for one entity inside a larger `wide_browse` batch. It turns one URL or site name into one browser task and returns one result row.

**Data flow**: It receives a single entity string from the batch. While holding the concurrency slot, it builds a task by replacing `{entity}` in the prompt template, appends the output schema text if one was available, then spawns a browser-profile child task with a stable deduplication key for that entity. It returns a dictionary containing the original entity and the child task’s JSON output, or an empty string if there was no output.

**Call relations**: `_wide_browse` creates this nested helper and schedules it once for each entity. The helper is the per-item worker in the batch: `_wide_browse` controls the overall list and final file, while `visit` performs each individual browser delegation.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `subagent setup`

This file is like a job description and equipment list for a browser-focused helper. The main agent can delegate web work to this subagent when it needs someone to open pages, click around, extract information, fill forms, or save findings back into the shared workspace.

The file starts by loading the browser subagent’s instructions from a markdown prompt file. Those instructions teach the helper how to behave while using the browser. It then builds the list of tools the helper is allowed to use: the browser tool set, plus a few basic file tools such as reading, writing, editing, and web search. This keeps the helper focused. It can browse and save useful results, but it does not get every possible tool in the system.

Two small data models describe the conversation boundary. `BrowserTask` says what the parent agent can send in: the task text, an optional starting URL, an optional task name, and whether the subagent should get extended context. `BrowserResult` says what must come back: a freeform result string.

Finally, `BROWSER_PROFILE` packages all of this into a `SubagentProfile`. That profile is what the larger system uses when it wants to launch this browser helper. The profile also marks the output as untrusted, which is important because web pages can contain misleading or hostile text; the parent agent should treat the result carefully rather than blindly believing it.


### Research Delegation
Provides parallel research delegation with retry-safe result collection and the research worker profiles used for that work.

### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `tool invocation / request handling`

This file solves a practical problem: researching many companies, people, or topics one by one is slow and fragile. The `wide_research` tool turns one big batch request into many smaller research jobs that run at the same time, like giving a stack of index cards to several assistants instead of asking one person to do them all.

The tool starts by reading an entities file, trimming blank lines and removing duplicates. It limits the batch size so one request cannot create an unbounded amount of work. For each entity, it builds a research prompt from a template, replacing `{entity}` with the current item. It then asks the system to spawn a child agent using the research profile. Each child writes its answer as JSON to a private workspace file.

The file is careful about reliability. Because this tool changes workspace files and starts child work, it requires an idempotency key: a stable call identifier used to recognize the same request if it is retried. Each child gets a deterministic deduplication key, so a recovered run reconnects to work that already happened instead of duplicating it. As rows finish, the tool writes a recovery aggregate file. At the end, it writes `wide_research.json`, containing one row per entity with either a JSON result or a short error message.

#### Function details

##### `_read_lines`  (lines 59–72)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads the user-provided entities file from the sandbox and turns it into a clean list of unique, non-empty entries. This gives the rest of the tool a safe, predictable list of things to research.

**Data flow**: It receives a tool context and a file path. It asks the sandbox shell to `cat` that file, quoting the path so special shell characters are treated as part of the filename. It then splits the file into lines, trims whitespace, skips blanks, removes duplicates while keeping the original order, and returns the final list. If the file cannot be read, it raises an error instead of continuing with missing input.

**Call relations**: The main `_wide_research` setup function calls this first, before any child research jobs are started. Its output becomes the entity list that `_WideResearch.run` later fans out across parallel visits.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_WideResearch.run`  (lines 91–101)

```
async def run(self) -> ToolResult
```

**Purpose**: Runs the whole batch after setup is complete. It starts research visits for every entity, waits for them all, saves the final combined JSON file, and returns a small tool response pointing to that output.

**Data flow**: It starts with a prepared `_WideResearch` object containing the context, entity list, output paths, recovery data, and concurrency controls. It registers cleanup for temporary per-entity result files, launches one `_visit` task per entity, and waits until all rows are available. It then builds the final aggregate object, updates the recovery file, writes `wide_research.json` in the workspace, and returns a `ToolResult` whose text includes the collected data plus the output filename.

**Call relations**: This is called by `_wide_research` after all setup is done. Inside the run, it delegates the per-entity work to `_WideResearch._visit`, uses `_WideResearch._install_recovery` to persist the final aggregate, and wraps the final response in the system's tool result format.

*Call graph*: calls 2 internal fn (_install_recovery, _visit); 5 external calls (__init__, __init__, __init__, gather, dumps).


##### `_WideResearch._remove_result_files`  (lines 103–110)

```
async def _remove_result_files(self) -> None
```

**Purpose**: Deletes temporary per-entity JSON files that were created or reused during the wide research run. This keeps the workspace from filling up with hidden intermediate files after the final aggregate has been produced.

**Data flow**: It reads the set of persisted result paths from the `_WideResearch` object. If there are none, it does nothing. Otherwise, it builds a safe shell `rm -f` command with each path quoted, runs it in the sandbox, and raises an error if the removal command fails.

**Call relations**: The `run` method registers this as a cleanup action with the tool context. It is not part of collecting results; it is the later housekeeping step that removes temporary files once the system decides cleanup should happen.

*Call graph*: 1 external calls (quote).


##### `_WideResearch._install_recovery`  (lines 112–122)

```
async def _install_recovery(self, rows: tuple[WideResearchRow, ...]) -> None
```

**Purpose**: Writes the current batch progress to a recovery file in the workspace. This lets a retried `wide_research` call pick up completed rows instead of starting over.

**Data flow**: It receives a tuple of completed rows. It wraps them in a `WideResearchFile` structure, converts that structure to JSON, and writes it first to a temporary staging path. Then it moves the staging file into the real recovery path. Writing through a staging file makes the update safer: readers are less likely to see a half-written recovery file.

**Call relations**: `_WideResearch._save_row` calls this whenever a single entity finishes, so progress is saved throughout the run. `_WideResearch.run` also calls it at the end to install the final complete recovery aggregate.

*Call graph*: called by 2 (_save_row, run); 3 external calls (__init__, dumps, quote).


##### `_WideResearch._save_row`  (lines 124–134)

```
async def _save_row(self, row: WideResearchRow) -> None
```

**Purpose**: Records one finished entity row and immediately updates the recovery file. It is the checkpoint step that makes partial progress durable while many child research jobs are running at once.

**Data flow**: It receives a `WideResearchRow`, which contains an entity and either its result or its error. It takes an async lock, meaning only one task can update the shared aggregate at a time. While holding that lock, it stores the row, rebuilds the completed rows in the original entity order, writes the recovery file, and marks that entity's temporary result file as one that should later be cleaned up.

**Call relations**: `_WideResearch._visit` calls this after each child research attempt succeeds or fails. It then hands the current completed set to `_WideResearch._install_recovery`, which performs the actual workspace write.

*Call graph*: calls 1 internal fn (_install_recovery); called by 1 (_visit).


##### `_WideResearch._visit`  (lines 136–183)

```
async def _visit(self, entity: str) -> WideResearchRow
```

**Purpose**: Researches one entity, or reuses a recovered result if that entity was already completed. It is the per-item worker used by the batch runner.

**Data flow**: It receives an entity name from the batch. First it waits on a semaphore, which is a counter-like gate that limits how many research children run at the same time. If the entity was recovered from a previous attempt, it returns that saved row immediately. Otherwise, it builds a stable child deduplication key, creates a prompt from the template, adds instructions telling the child where to write JSON, and spawns a research subagent. After the child finishes, it reads the expected result file. If the file is missing, unreadable, or not valid JSON, it creates an error row. If it can parse the JSON, it creates a result row. In both cases, it saves the row through `_save_row` and returns it.

**Call relations**: `_WideResearch.run` creates one `_visit` task for each entity. `_visit` calls `_WideResearch._save_row` to checkpoint its outcome. It also validates child-agent output through `ResearchOutput` when it needs to include child feedback in an error message.

*Call graph*: calls 1 internal fn (_save_row); called by 1 (run); 4 external calls (__init__, model_validate, loads, quote).


##### `_wide_research`  (lines 186–241)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: Prepares and launches a `wide_research` tool call. It validates the call, reads inputs, sets up recovery, creates per-entity result paths, and then hands execution to `_WideResearch.run`.

**Data flow**: It receives the tool context and the parsed input arguments. It first checks for an idempotency key, because recovery and child deduplication depend on a stable call identity. It reads the entities file, rejects batches that are too large, hashes the call key into a call id, and chooses recovery file paths. It prunes old recovery files from earlier turns, tries to load a matching recovery file for this call, and reads the optional output schema file. Then it creates a semaphore to limit parallelism, computes hidden result-file paths for each entity, builds a `_WideResearch` object with all this prepared state, and returns the result of running it.

**Call relations**: This is the handler attached to the exported `WIDE_RESEARCH_TOOL`, so the tool system calls it when a user invokes `wide_research`. It calls `_read_lines` during setup, constructs `_WideResearch`, and then lets that object perform the actual fan-out and collection work.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, Lock, Semaphore, sha256, loads, quote).


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / subagent registration`

This file is like a job description and equipment list for research-focused child agents. A child agent is a smaller helper that the main agent can delegate work to. Without this file, the system would not know how to launch a focused research helper, what model to use for it, what prompt instructions to give it, or which tools it is allowed to touch.

The file defines two profiles. The regular `research` profile is meant for focused research tasks. It uses a specific model, `gpt-5.6-terra`, with high reasoning settings. The `deep_research` profile is for larger research jobs that may need many steps and many sources. It uses `claude-sonnet-5` and raises the allowed number of back-and-forth work rounds to 200.

Both profiles share the same toolbox. They can search the web, fetch pages, use vertical search, ask a browser-focused helper to do browser work, call external tools, read and write files, search memory, and use spreadsheet-style tools. They do not get direct access to every browsing surface; that separation keeps responsibilities clear.

The file also defines simple input and output shapes using Pydantic, a validation library that checks data has the expected fields. The input is an `objective`, meaning the task to research. The output is a `result`, described using the shared delivery rules so parent agents receive a consistent handoff.


### Website Delegation
Connects website-building requests to a focused site-building child agent and defines that agent’s profile.

### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file exists so the main assistant does not have to do every website build itself. Instead, it can delegate the job to a purpose-built “website_building” subagent, like asking a specialist contractor to take over a construction task while still working on the same property.

The file defines what information the tool needs: a complete build objective, an optional friendly task name, optional skills to preload, and an optional larger work budget for big builds. The objective must be self-contained because the child agent does not inherit the parent conversation history.

When the tool runs, it starts a child turn through the normal `ctx.spawn` path. That matters because the child is still limited to the tools and permissions of the current profile; it is not given a raw backdoor into the website system. The child works in the same sandbox, meaning any files it creates remain available after it finishes, and any hosted site is registered to the current conversation.

The tool is marked as side-effecting because it can create files, start or register a site, and change the sandbox. It also uses an idempotency key, which is a “same request” marker, so if work is retried after a crash, the system can reconnect to the already-started child job instead of accidentally launching a duplicate build.

#### Function details

##### `_build_website`  (lines 56–63)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the actual worker behind the `build_website` tool. It takes the user’s website-building instructions, starts the specialized website-building subagent, then returns that subagent’s final summary as the tool result.

**Data flow**: It receives the current tool context and a validated `BuildWebsiteInput` object. It turns the input into a plain data payload, leaving out empty optional fields, and sends that payload to the `website_building` child agent through `ctx.spawn`. When the child finishes, it takes the child’s output, converts it to JSON text if there is output, and wraps that text in a `ToolResult` so the caller can read the summary.

**Call relations**: This function is called when the registered `build_website` tool is invoked. Its main handoff is to `ToolContext.spawn`, which creates or reconnects to the child agent using the current idempotency key. After the child returns, `_build_website` packages the response with `TextContent` and `ToolResult` for the tool system to send back to the main assistant.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup and delegation`

This file is like a job description and tool badge for a helper whose only task is building and checking websites. When the main assistant wants to delegate website work, this profile explains how to start that child worker safely and consistently.

The file loads a website-building prompt from a nearby Markdown file. That prompt contains the actual working instructions. It then lists the tools the subagent is allowed to use: basic file tools for reading and editing, build and local website tools, JavaScript and spreadsheet REPL tools for testing, and optional web research tools if they are installed. A REPL is an interactive scratchpad where code can be run and inspected.

Two important tools are deliberately left out. The subagent cannot use `publish_website`, because publishing a backend-backed app belongs to the parent assistant that is talking to the user. It also cannot use `share_file`, because the child has no direct user to send files to. Instead, it leaves its work in the shared workspace so the parent can read it and decide what to do next.

The file also defines small Pydantic models. Pydantic is a library that checks data shapes. These models say that the subagent receives a freeform objective and returns a freeform result. Finally, everything is bundled into `WEBSITE_BUILDING_PROFILE`, which the wider system can register and run.
