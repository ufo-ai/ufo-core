# Subagents, delegation, and multi-step agent workflows  `stage-13`

This stage is part of the main work loop, when the assistant decides a job is too large, specialized, or slow to do alone. It can hand work to child agents, like asking helpers in a workshop to research, browse, write, or build while the main conversation continues. The fallback profile defines what a basic helper may do, and the spawn catalog shows the agent which helpers are available right now. The core subagents code starts those helpers, checks permissions and costs, validates the agreed input and output formats, and returns results either immediately or later in the parent conversation.

Specialized profiles give helpers narrower jobs. The browser helper can use web tools; browser delegation runs one browsing session or many parallel visits. Research delegation runs many research helpers and merges their JSON results into a saved file. The brief pipeline passes typed messages through outline, draft, and critique helpers. The document helper drafts and edits prose. The objectives store records plans, steps, evidence, and blockers. The site tools hand off website building, guide safe app creation, and audit the result before accepting it.

## Files in this stage

### Core subagent orchestration
These files define the default child-agent contract, advertise spawnable helpers, and run the lifecycle for synchronous and background subagents.

### `core/src/ufo/loop/profiles.py`

`config` · `subagent setup and dispatch`

This file is the default instruction sheet for a delegated worker agent. In this system, a parent agent can spawn a subagent, which is like asking a helper to work on a clearly scoped task in the same shared workspace. If no extension names a more specialized helper, the system uses this file’s `general_purpose` profile.

The profile gives that helper a focused prompt: work independently, make reasonable assumptions, avoid asking the user questions, use available skills, and write useful files into `/workspace` so the parent or other helpers can read them later. It also warns the helper not to repeat a failing action forever, and to produce formal Office files such as `.docx`, `.pptx`, or `.xlsx` when those are requested instead of using Markdown.

The file also defines the tool allow-list for this helper. It includes practical tools such as reading and writing files, searching text, running shell commands, loading skills, sharing files, web access, external tool connectors, and spreadsheet support. It intentionally leaves out tools that would let the helper ask the user, spawn more agents, message or cancel siblings, or approve account connections. In plain terms, the helper can do the work, but it cannot take over coordination or user-facing decisions.

Finally, the file packages the name, prompt, allowed tools, input contract, and output contract into a `SubagentProfile`, then exposes it as the core set of subagent profiles.


### `core/src/ufo/loop/spawn_catalog.py`

`domain_logic` · `per turn, before or during delegation`

When an agent wants to delegate work, it needs to know which targets it can name and what input each target expects. This file creates that guide on demand, like printing a fresh menu before every order so it matches what the kitchen can actually make.

There are two kinds of spawn targets. First are fixed subagent profiles, which come from the running program’s registry. Second are workspace agents, which live in the database and can differ by workspace, owner, and permissions. Because workspace agents can change, the catalog is built per turn instead of once at startup.

The main function checks whether the current member is a workspace admin. Admins can see all unarchived agents in the workspace; non-admins only see their own. It then combines those database rows with the registered profiles and formats them into a markdown table. Each row names the target, says whether it is a profile or an agent, and lists the payload keys it accepts. If an agent has the same name as a profile, it is shown with an `agent:` prefix, because that is the unambiguous name `spawn` expects.

The result is packaged as a `RuntimeSkill`, so an agent can load `spawn-catalog` like any other skill before choosing a spawn target.

#### Function details

##### `_profile_payload`  (lines 29–36)

```
def _profile_payload(profile: SubagentProfile) -> str
```

**Purpose**: This helper turns a subagent profile’s input model into a short human-readable list of payload fields. It marks which fields are required and which are optional so the spawning agent knows what to send.

**Data flow**: It receives a `SubagentProfile`, reads the fields from its input model, and sorts them by name. If there are no fields, it returns “(no fields)”; otherwise it returns a comma-separated string where required fields are shown plainly and optional fields are labeled as optional.

**Call relations**: The catalog builder calls this while writing the table rows for fixed subagent profiles. Its output becomes the payload column for each profile target in the generated `spawn-catalog` skill.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `_schema_payload`  (lines 39–49)

```
def _schema_payload(schema: Mapping[str, object] | None) -> str
```

**Purpose**: This helper turns a workspace agent’s stored input schema into a readable list of payload fields. A schema is a structured description of what input data an agent accepts.

**Data flow**: It receives either a schema mapping or nothing. If there is no schema, it falls back to the standard `TaskInput` fields. If the schema has no usable properties, it returns “(no fields)”. Otherwise it reads the property names, checks which ones are marked required, and returns a sorted comma-separated field list with optional fields labeled.

**Call relations**: The catalog builder calls this for each workspace agent read from the database. Its result fills the payload column for agent rows in the generated catalog.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `spawn_catalog_skill`  (lines 52–104)

```
async def spawn_catalog_skill(registry: SubagentRegistry, member_id: UUID | None) -> RuntimeSkill
```

**Purpose**: This asynchronous function builds the complete `spawn-catalog` runtime skill for the current turn. It tells an agent exactly which spawn targets are available and what payload each one expects.

**Data flow**: It receives the live subagent registry and the current member’s ID, if there is one. It reads the current workspace, opens a workspace database transaction, checks whether the member is an admin, and queries the unarchived agents the member is allowed to spawn. It then combines those agents with the registry’s profiles, formats everything into a markdown table, wraps that table in skill metadata, and returns a `RuntimeSkill` object.

**Call relations**: This is the file’s main assembly point. It calls `_profile_payload` for registry profiles and `_schema_payload` for workspace agents, uses the database transaction and SQL query to read current agent rows, asks the seat/permission code whether the member is an admin, and uses the workspace context to stay inside the right workspace. The finished `RuntimeSkill` is what the rest of the runtime can offer to an agent that needs to decide how to delegate.

*Call graph*: calls 2 internal fn (_profile_payload, _schema_payload); 5 external calls (__init__, select, workspace_tx, member_is_admin, ws_current).


### `core/src/ufo/loop/subagents.py`

`orchestration` · `request handling`

This file is the control room for “spawned” helper work. A parent turn can ask for a named subagent profile, or for another workspace agent, to do a child turn. Without this layer, the system could start duplicate helpers, charge the wrong model, wait forever on stuck work, or let untrusted output flow back into a conversation as if it were safe instructions.

The flow is like giving a task to an assistant in another room. First the target name is resolved: is it a built-in profile or a workspace agent? Then the payload is checked against that target’s expected input contract, meaning the required shape of the data. A child conversation and first turn are written to the database, with a link back to the parent turn. The child is put onto the turn queue so the normal worker system can run it.

If the caller wants a foreground result, this file waits for the child’s terminal frame, which is the saved final state of the child turn. If a member sends a new message while the parent is waiting, the child can be detached and allowed to finish later, so the parent is not trapped waiting. Background children deliver their results through a special result arrival. Finished output is checked against the declared output contract, and unsafe or invalid content is wrapped or withheld instead of being blindly trusted.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 146–150)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the registered subagent profiles do not reuse the same name. This matters because a spawn request by name must point to exactly one profile.

**Data flow**: It reads the profile names stored in the registry → looks for names that appear more than once → either leaves the registry usable or raises an error before ambiguous spawning can happen.

**Call relations**: This runs automatically when a SubagentRegistry is created. It protects later lookups, such as those done by get and find, from having to choose between duplicates.


##### `SubagentRegistry.get`  (lines 152–158)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Returns a registered subagent profile by name, and raises a clear error if the name is unknown. Use this when the caller requires the profile to exist.

**Data flow**: It receives a profile name → asks find to search the registry → returns the matching profile, or raises an UnknownSubagentProfile error that includes the known names.

**Call relations**: Queue setup code calls this when it must resolve a profile for a turn. It relies on find for the search and turns a missing result into a user-facing failure.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 160–161)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Looks up a subagent profile by name and quietly returns nothing if it is not present. Use this when missing profiles are allowed and the caller will decide what to do next.

**Data flow**: It receives a name → scans the registry’s stored profiles → returns the first profile with that name, or null if none match.

**Call relations**: get uses this to perform the actual search. Other registry users also depend on this softer lookup when they need to handle missing profiles without immediately failing.

*Call graph*: called by 1 (get).


##### `_target_model`  (lines 176–184)

```
def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None
```

**Purpose**: Figures out whether a spawn target names a specific model to bill or run against. Profiles may pin a model, while workspace agents use the model stored on their own agent record.

**Data flow**: It receives either a profile target or an agent target → checks which kind it is → returns the profile’s model name for profile targets, or null for agent targets.

**Call relations**: Subagents.spawn calls this before admitting a child turn. The result is passed into the admission and balance-check path so the right model is considered.

*Call graph*: called by 1 (spawn).


##### `subagent_system_prompt`  (lines 187–223)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the full instruction text given to a profile-based subagent. It combines the profile’s own prompt, optional skill information, shared output rules, and the final-answer contract.

**Data flow**: It receives a profile, an available skill list, and optional preloaded skills → fills the skill-index slot, checks for forgotten prompt placeholders, appends preloaded skill text if it is not too large, and adds the required finish-tool instructions → returns one complete system prompt string.

**Call relations**: This is used when preparing a profile child to run. It calls the prompt-rendering and skill-loading helpers so the child receives both its special instructions and the system-wide rules for returning structured output.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 240–241)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: Creates a copy of the Subagents controller that acts on behalf of a specific member. This is used when permission checks need to know which human is responsible for the spawn.

**Data flow**: It receives a member id or null → copies the existing Subagents object with that requester set → returns the new copy without changing the original.

**Call relations**: Later methods read requester_member_id through acting_member_id. This method is the small handoff that stamps the controller with the member whose authority should be used.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 244–253)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Chooses the member whose permissions should apply to this spawn operation. It gives priority to an explicitly authorized requester, then the parent turn’s speaker, then the member the parent is acting for.

**Data flow**: It reads requester_member_id and fields from the parent turn → picks the first available member id in that order → returns that id, or null if no member is known.

**Call relations**: Permission checks, child admission, follow-up messages, and child ownership checks all use this property so the whole spawn flow agrees on who is responsible.


##### `Subagents.spawn`  (lines 255–368)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnResult
```

**Purpose**: Starts a child turn for a named subagent profile or workspace agent. It can return immediately for background work, wait for a foreground answer, or detach if a new member message arrives.

**Data flow**: It receives a target name, input payload, and options such as background mode and deduplication key → resolves the target, checks permissions and input shape, creates or reuses a child conversation and turn, enqueues the work, optionally waits for completion, validates the final output → returns a SpawnResult with the child ids and possibly the validated output.

**Call relations**: This is the main public entry for spawning. It calls the resolver, admission, queueing, balance, waiting, and detaching helpers; if anything goes wrong during a foreground wait, it cancels the child so orphaned work is not left running.

*Call graph*: calls 7 internal fn (_admit, _await_terminal, _await_terminal_or_detach, _enqueue, _may_spawn, _resolve, _target_model); 8 external calls (__init__, __init__, turn_id_for, cancel_one_turn, input_contract, output_contract, uuid4, uuid5).


##### `Subagents.result`  (lines 370–407)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the finished result of a child turn that this conversation spawned. It validates the stored final answer before handing it back.

**Data flow**: It receives a child turn id → confirms the turn belongs to this spawning conversation, loads its terminal record and output contract, validates the saved text if possible → returns a SpawnResult with the terminal and either a trusted structured output or no output.

**Call relations**: Host-side tools can call this after a child has already completed. It relies on _require_child for safety, uses _agent_output_schema when the child was a workspace agent, and uses _untrusted_output to mark whether the answer must be treated carefully.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, workspace_tx, output_contract).


##### `Subagents.wait`  (lines 409–429)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits until one or more child turns finish, then reports their final status and text. This is for tool flows that explicitly need to block for child completion.

**Data flow**: It receives a tuple of child turn ids → checks each one belongs to this conversation, waits for each terminal frame, builds a status object for each → returns all statuses as a tuple.

**Call relations**: This method uses _require_child before waiting so callers cannot wait on unrelated turns. It then uses _await_terminal and _untrusted_output to produce the same cautious result status the rest of the spawn system expects.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 431–448)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a running child turn that belongs to this parent conversation. It then reports the child’s current stored status.

**Data flow**: It receives a child turn id → verifies ownership, asks the shared cancellation routine to stop the turn, reloads the turn’s status and terminal text from the database → returns a SubagentStatus.

**Call relations**: This public control method depends on _require_child to enforce boundaries. It hands the actual stopping work to cancel_one_turn, the same cancellation path used elsewhere in the turn system.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents.message`  (lines 450–572)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child conversation. This lets a parent answer a child’s question or continue work without starting the child over.

**Data flow**: It receives the original child turn id, message text, a deduplication key, and a delivery option → verifies the child, checks the profile still exists when needed, finds or creates the next turn in the child conversation, checks balance for new work, marks it ready for dispatch if no earlier queued turn blocks it, and enqueues it if appropriate → returns the follow-up turn’s status.

**Call relations**: This is the continuation path after a child has already been spawned. It calls _require_child for safety, _profile_model and _require_balance for billing, and _enqueue to schedule the follow-up through the same queue as ordinary turns.

*Call graph*: calls 4 internal fn (_enqueue, _profile_model, _require_balance, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._resolve`  (lines 574–597)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Translates a spawn target string into the actual thing to run: either a profile or a workspace agent. It also catches ambiguous names and gives useful errors for unknown names.

**Data flow**: It receives a target such as a bare name, profile:name, or agent:name → searches the profile registry and workspace agent table as needed → returns the matching profile or agent target, or raises an ambiguity or unknown-target error.

**Call relations**: Subagents.spawn calls this at the start of every spawn. It uses _profile_names, _agent_names, and _agent_target to build both the answer and helpful error messages.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._profile_names`  (lines 599–600)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the list of profile names known to this registry. It is mainly used to explain what targets are available when a spawn name is wrong.

**Data flow**: It reads the registry’s profiles → sorts their names → returns them as a tuple.

**Call relations**: _resolve uses this when it needs to report available profile targets in an error.

*Call graph*: called by 1 (_resolve).


##### `Subagents._agent_names`  (lines 602–614)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of active workspace agents that can be considered as spawn targets. Archived agents are left out.

**Data flow**: It opens a workspace database transaction → selects non-archived agent names for the parent workspace → returns the ordered names as a tuple.

**Call relations**: _resolve calls this when it needs to explain unknown targets. It gives the error message the current agent-side namespace.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 616–641)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Looks up a workspace agent by name and packages the facts needed to spawn it. These facts include its id, owner, and declared input and output schemas.

**Data flow**: It receives an agent name → queries the workspace database for a non-archived matching agent → returns an AgentTarget if found, or null if not.

**Call relations**: _resolve uses this to decide whether a target name refers to a workspace agent. Later spawn logic uses the returned owner and schemas for permission and validation.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 643–649)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Loads the declared output schema for a workspace agent. This is needed when reading or delivering a finished agent-child result.

**Data flow**: It receives an agent id → queries the database for that agent’s output schema → returns the schema dictionary or null.

**Call relations**: Subagents.result calls this when the child was spawned as an agent rather than as a profile, so it can validate the saved terminal text against the agent’s contract.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._may_spawn`  (lines 651–663)

```
async def _may_spawn(self, owner_member_id: UUID | None) -> bool
```

**Purpose**: Checks whether the acting member is allowed to spawn a workspace agent. Owners can spawn their own agents, and workspace admins can spawn any agent.

**Data flow**: It receives the target agent’s owner id → compares it with the acting member id, and if needed checks admin membership in the database → returns true if spawning is allowed, otherwise false.

**Call relations**: Subagents.spawn calls this before admitting an agent child. This keeps chat-based spawning aligned with the same ownership rules used elsewhere in the workspace.

*Call graph*: called by 1 (spawn); 2 external calls (workspace_tx, member_is_admin).


##### `Subagents._untrusted_output`  (lines 665–674)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child’s output must be treated as untrusted content. Agent children are always considered untrusted, and missing profile definitions also fail safely.

**Data flow**: It receives a profile name or null → for agent children returns true, for profile children looks up the profile and reads its untrusted-output flag → returns a boolean trust decision.

**Call relations**: Subagents.result and Subagents.wait call this when reporting child results. The decision tells later consumers whether to wrap or wall off the text instead of treating it as safe instructions.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 676–712)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Verifies that a given turn id belongs to a child spawned by this conversation and by the same acting member. It prevents one conversation or member from controlling another person’s child work.

**Data flow**: It receives a turn id → loads the turn’s parent link, profile, and member stamp from the database → checks whether it was spawned by this parent turn or a sibling turn in the same conversation and whether the member matches → returns the child’s profile name, or raises an error.

**Call relations**: Public operations such as result, wait, cancel, and message call this first. It is the shared guardrail before reading, stopping, or continuing a child turn.

*Call graph*: called by 4 (cancel, message, result, wait); 2 external calls (select, workspace_tx).


##### `Subagents._profile_model`  (lines 714–720)

```
def _profile_model(self, profile: str | None) -> str | None
```

**Purpose**: Finds the model pinned by a named profile, if any. If the profile is missing, it quietly returns null because this is used for billing already-existing child work.

**Data flow**: It receives a profile name or null → searches the registry → returns the profile’s model name when present, otherwise null.

**Call relations**: Subagents.message uses this before billing a follow-up turn. The result is passed to _require_balance so the balance check weighs the correct model.

*Call graph*: called by 1 (message).


##### `Subagents._require_balance`  (lines 722–740)

```
async def _require_balance(self, connection: AsyncConnection, model: str | None, agent_id: UUID | None=None) -> None
```

**Purpose**: Stops new child work from starting when the workspace does not have enough prepaid balance. This prevents spawned helpers from becoming a way to get unpaid model calls.

**Data flow**: It receives a database connection, an optional model name, and optionally the child agent id → asks the billing BalanceGate whether the workspace may start this work → returns normally if allowed, or raises BalanceExhausted if rejected.

**Call relations**: _admit calls this for a newly spawned child, and message calls it for a new follow-up. It is deliberately placed only on new work, not on recovery paths that reconnect to work already admitted.

*Call graph*: called by 2 (_admit, message); 2 external calls (__init__, __init__).


##### `Subagents._admit`  (lines 742–831)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, delivers_result: bool=False, name: str='', model: str | None=
```

**Purpose**: Creates the database records for a child conversation and its first turn, or safely reuses them during retry. It also stamps the child with ownership, tracing, delivery, sandbox, and billing information.

**Data flow**: It receives child ids, target agent/profile details, input text, and delivery settings → inserts the conversation and first turn if they do not already exist, checks the existing row belongs to the same acting member, checks balance, and marks the queued turn as dispatched → returns true when the caller should enqueue the turn, or false when it was already past queued.

**Call relations**: Subagents.spawn calls this after resolving and validating the target. _admit calls _require_balance before work is dispatched and sets up the rows that _enqueue and later wait/delivery code depend on.

*Call graph*: calls 1 internal fn (_require_balance); called by 1 (spawn); 6 external calls (select, update, workspace_tx, conversation_name, current_traceparent, audience_member).


##### `Subagents._enqueue`  (lines 833–862)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Asks DBOS, the durable workflow queue, to run a turn workflow for the child or follow-up. If enqueueing fails, it clears the dispatch marker so another dispatcher can try later.

**Data flow**: It receives a turn id and conversation id → builds queue options using the turn as workflow id and the conversation as partition key → attempts to enqueue; on cancellation or failure, resets dispatch_enqueued_at in the database and either re-raises cancellation or logs the deferred enqueue.

**Call relations**: Subagents.spawn and Subagents.message call this after admission says a turn is ready. It is the bridge from saved database work to the background worker system.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 864–907)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has a saved terminal frame, meaning it has finished in the database. It also handles workflow retries and parked turns so the parent does not wait forever.

**Data flow**: It receives a child turn id → tries to attach to the DBOS workflow, waits for its result, checks the database for a terminal or parked status, follows a newer running attempt if one exists → returns the TerminalFrame or raises if the workflow ended without one.

**Call relations**: Foreground spawn, wait, and interruptible wait all use this. It relies on _terminal_or_park for the database truth and _running_attempt when DBOS has moved the work to another attempt.

*Call graph*: calls 2 internal fn (_running_attempt, _terminal_or_park); called by 3 (_await_terminal_or_detach, spawn, wait); 1 external calls (sleep).


##### `Subagents._running_attempt`  (lines 909–915)

```
async def _running_attempt(self, turn_id: UUID) -> str | None
```

**Purpose**: Reads the workflow attempt id currently recorded for a turn. This helps the waiter follow a retried or replaced workflow attempt.

**Data flow**: It receives a turn id → queries the turn row for its running_attempt field → returns that workflow id string or null.

**Call relations**: _await_terminal calls this when the workflow it was watching is missing or ended without a terminal. It lets waiting continue on the live attempt instead of failing too early.

*Call graph*: called by 1 (_await_terminal); 2 external calls (select, workspace_tx).


##### `Subagents._await_terminal_or_detach`  (lines 917–972)

```
async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Waits for a child to finish, but stops waiting if a new member message arrives for the parent conversation. In that case the child is moved to background delivery so the parent can respond now.

**Data flow**: It receives a child turn id → starts one task waiting for the child terminal and another listening for parent conversation arrivals → if the terminal wins, returns it; if a valid member arrival wins and _detach succeeds, returns null; otherwise keeps waiting or falls back to terminal waiting.

**Call relations**: Subagents.spawn uses this when detach_on_arrival is requested. It combines _await_terminal with hub arrival notifications and calls _detach to make the race safe in the database.

*Call graph*: calls 2 internal fn (_await_terminal, _detach); called by 1 (spawn); 5 external calls (create_task, ensure_future, gather, wait, log).


##### `Subagents._terminal_or_park`  (lines 974–990)

```
async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Checks whether a child has finished, and cancels it if it is parked on a spend limit. A parked child has no usable final answer, so the parent should not keep waiting.

**Data flow**: It receives a turn id → reads terminal and status from the database → returns a parsed TerminalFrame if present, cancels and raises SubagentParked if status is parked, or returns null if the child is still running.

**Call relations**: _await_terminal uses this as its database source of truth after workflow events. It calls the shared cancellation path when a parked child would otherwise leave the parent stuck.

*Call graph*: called by 1 (_await_terminal); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents._detach`  (lines 992–1023)

```
async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool
```

**Purpose**: Marks a foreground child so it will deliver its result later instead of returning inline. It only succeeds if a real, unconsumed member message interrupted the parent and the child has not already finished.

**Data flow**: It receives a child turn id and an arrival id → performs a guarded database update that sets result_delivery to pending only when the child has no terminal and the arrival belongs to the parent’s conversation → returns true if exactly one row was updated.

**Call relations**: _await_terminal_or_detach calls this to resolve the race between child completion and a new member message. If it succeeds, delivery later goes through SubagentResult; if it fails, the foreground wait continues or returns the terminal.

*Call graph*: called by 1 (_await_terminal_or_detach); 4 external calls (exists, select, update, workspace_tx).


##### `SubagentResult.deliver`  (lines 1047–1088)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished background child’s result back into the conversation that spawned it. It then marks the child result as delivered so the same result is not posted again.

**Data flow**: It receives a child Turn → ignores it unless delivery is pending and it has a parent, requires a committed terminal, loads parent conversation details and possibly the child agent row, builds the result body, invokes a new arrival in the parent conversation using a deterministic key, then updates the child delivery status to delivered.

**Call relations**: This is the delivery half of background spawning and detached foreground spawning. It calls _body to format the safe result envelope, then uses TurnInvoker to feed that envelope into the parent conversation as ordinary incoming work.

*Call graph*: calls 1 internal fn (_body); 3 external calls (select, update, workspace_tx).


##### `SubagentResult._body`  (lines 1090–1119)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Builds the text envelope that carries a child’s result back to the parent conversation. The envelope names the target, child id, and result status, and walls off untrusted payloads.

**Data flow**: It receives a finished child turn and, for agent children, the agent row → chooses the target label and output contract, asks _payload for the validated payload and status, wraps unsafe content with an untrusted-content wall, escapes any closing result tag inside the payload → returns the final spawn_result text block.

**Call relations**: SubagentResult.deliver calls this before invoking the parent conversation. It calls _payload for validation and uses the wall helper when profile rules or agent-child rules say the content should not be treated as trusted instructions.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (output_contract, wall).


##### `SubagentResult._payload`  (lines 1121–1140)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a child’s terminal frame into the payload and status that should be delivered. It distinguishes success, questions, failures, missing contracts, and invalid output.

**Data flow**: It receives an output contract or null and a terminal frame → if the child failed, returns a diagnostic and failure status; if it asked a question, returns the question JSON; if there is no contract, returns a withheld-result message; otherwise validates the final text and returns structured JSON or an invalid-output explanation → returns a payload string and status string.

**Call relations**: _body calls this while formatting a delivery. This function is the last safety check before a child’s answer is handed back to the parent conversation.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).


### Specialist subagent profiles
These files declare focused helper-agent shapes for brief writing, browser work, and prose drafting.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load / subagent setup`

This file is the blueprint for a simple writing assembly line. Instead of asking one agent to do everything at once, the brief pipeline splits the work into three focused stages: outline, draft, and critique. Each stage is a subagent profile, meaning a named setup that tells the system what prompt to use, what input shape to expect, what output shape to return, and how many conversation rounds it may take.

The typed models are built with Pydantic, a library that checks that data has the expected fields. For example, the outline stage receives a topic and audience, and must return an outline. The draft stage receives the topic plus that outline, and must return draft text. The critic receives the draft and returns a verdict plus optional improvements. This keeps each handoff predictable, like forms passed along a production line.

A notable design choice is that these subagents have no tools and cannot spawn further agents. Their entire job is to read their input and produce structured text. That makes the pipeline shallow and controlled: the parent agent decides the sequence, and each stage contributes one clear piece.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `subagent setup and launch`

This file is like the job description and equipment list for a specialized web assistant. The main agent can hand off a browser task, such as visiting a site, collecting information, filling a form, or saving screenshots. This profile tells the system how that browser-focused helper should behave.

The file loads a prompt from `subagent_browser.md`, which is the written instruction set for the browser subagent. It then builds the list of tools the subagent is allowed to use: browser automation tools, plus a few basic file and web-search tools so it can save notes, edit files, read shared workspace material, and search the web when needed.

It also defines two small data shapes using Pydantic, a library that checks that data has the expected fields. `BrowserTask` describes what the parent agent sends in: the task text, an optional URL, an optional task name, and whether the subagent should get extended context. `BrowserResult` describes what comes back: a freeform result string.

Finally, `BROWSER_PROFILE` packages all of this into a `SubagentProfile`. That profile is what the larger system uses when it wants to launch this browser helper. Without this file, the browser subagent would not have a clear name, prompt, allowed tools, input format, output format, or model choice.


### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `subagent setup`

This file is like a job description and tool badge for a specialized writing helper. The larger system can spawn child assistants, called subagents, for narrower tasks. This one is meant only for prose work: reading drafts, writing or editing files, searching text, and loading its writing workflow skill.

The file names the profile “writing” and pins it to the model `gpt-5.6-terra`, so the writing child does not simply inherit whatever model the parent assistant is using. It also loads a prompt from `prompts/subagent_writing.md`, which gives the child its instructions.

Two small Pydantic models define the contract for talking to this child. Pydantic is a library that checks that data has the expected shape. `WritingTask` says the parent must send an `objective`, and by default the child starts with the `writing-drafts` skill already loaded. `WritingResult` says the child returns a freeform `result`.

The tool list is intentionally narrow. The child can read, write, edit, search files, and load skills. It cannot run shell commands, use a coding REPL, browse the web, or share files directly. That keeps it as a writing assistant, not a second programmer or researcher. Without this file, the system would not have a clear, safe, reusable profile for spinning up a prose-focused subagent.


### Parallel delegation workflows
These files support fan-out delegation for browsing, objective-backed work tracking, and wide research aggregation.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file exists so the main agent does not have to drive a browser directly. Instead, it delegates the work to a specialized “browser” subagent, which is like sending a task to a helper with its own fresh browser window. That matters because web automation can be slow, can get stuck, and can involve many steps; this file puts limits around that work so one bad website does not freeze the parent task forever.

The single-task path, exposed as `browser_task`, starts one browser subagent with a URL, a self-contained task description, and a friendly task name. It waits for the child task to finish, but only up to a bounded timeout. If the browser task runs too long, it is cancelled and the caller gets a clear error message.

The batch path, exposed as `wide_browse`, reads a workspace file containing URLs or site names, removes blank lines and duplicates, and sends each item to a browser subagent. It keeps the parallel work bounded, like only opening a limited number of checkout lanes at once, so the system is not flooded. Each result is collected into `wide_browse.json` and also returned to the caller.

Both tools use deterministic keys for spawned work. This helps recovery: if a parent task is retried after a crash, it can reconnect to already-started browser children instead of accidentally starting duplicate sessions.

#### Function details

##### `_browser_task`  (lines 92–121)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation job by spawning a browser subagent and waiting for its summary. It protects the parent task by cancelling the browser work if it exceeds the requested time limit.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the starting URL, instructions, task name, and timeout. It sends those details to the browser profile through the context’s spawn mechanism, then waits for that child turn. If the wait times out or the child reports cancellation, it returns an error-style tool result saying the browser task was cancelled. If the child finishes successfully, it validates the child’s JSON summary as a `BrowserResult` and returns that JSON text to the caller.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the main agent calls that tool, this function creates the browser child through `ToolContext.spawn`, waits under an `asyncio.timeout` budget, and converts the browser child’s final text into the tool result the parent agent sees.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 124–137)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. It is used to load the URLs or site names for batch browsing.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for a shell command, reads the file with `cat` inside the sandbox, and raises an error if the file cannot be read. From the file contents, it strips whitespace, skips blank lines, removes duplicates while keeping the first occurrence, and returns the resulting list of strings.

**Call relations**: `_wide_browse` calls this first, before starting any browser subagents. Its output becomes the set of entities that the batch browser run will visit.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 140–167)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs many browser extraction tasks from a list of URLs or site names, with a fixed cap on both total items and parallel workers. It saves all collected results into a JSON file for later use.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing an entities file, a prompt template, and a schema file path. It reads and deduplicates the entities, rejects the request if there are too many, reads the optional JSON schema text, and creates a semaphore, which is a simple gate that limits how many visits can run at the same time. It then launches one `visit` operation per entity, gathers their result rows, writes those rows to `wide_browse.json` in the workspace, and returns a tool result containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It relies on `_read_lines` to prepare the entity list, uses `asyncio.gather` to run many `visit` tasks concurrently, and wraps the final JSON summary in a `ToolResult` for the caller.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 148–161)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser subtask for one entity in a `wide_browse` batch. It builds the per-entity prompt and returns one result row for that entity.

**Data flow**: It receives one entity string from the batch list. After entering the semaphore gate, it replaces `{entity}` in the prompt template with the actual entity. If a schema was read from the schema file, it appends instructions asking the browser result to match that schema. It then spawns a browser subagent with a deterministic deduplication key and returns a dictionary containing the entity and the browser child’s JSON output, or an empty string if there was no output.

**Call relations**: `_wide_browse` creates this inner helper and runs it once for each entity through `asyncio.gather`. The semaphore around it keeps only a limited number of browser children active at once, so the batch fan-out stays controlled.


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `cross-cutting`

An objective is a planned piece of work, broken into steps. This file makes sure those steps are not judged only by what a worker says happened. Instead, each step has recorded events, such as “did this” or “blocked,” and optional acceptance conditions, such as “this file exists” or “this command succeeds.” The extension, not the worker, decides whether those conditions currently hold.

The file defines database tables for objectives, steps, step events, and condition checks. It also defines small data shapes for the allowed checks: a file must exist, a file must contain text, or a command must succeed. Think of the database as a notebook where entries are added in ink: events and checks are appended, not edited away, so later turns can see what really happened over time.

The main store class, `Objectives`, reads and writes one workspace’s objective records. It can find an objective, create or revise a plan, append evidence that a step was attempted or blocked, and save the extension’s latest condition check. The view classes then turn raw database rows into a plain picture of the objective: which steps are pending, done, blocked, unmet, or still waiting for a check.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: This tells whether anyone has recorded that work was actually done for this step. A step is not treated as complete just because it exists in the plan.

**Data flow**: It reads the step’s event history → looks for any event whose kind is `did` → returns true if at least one such event exists, otherwise false.

**Call relations**: Other step-state decisions use this as a basic fact. For example, `StepView.state` uses it to distinguish a planned-but-untouched step from a step that was tried but still needs confirmation.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: This finds the currently standing blocker, if the last thing recorded for the step was a block. It prevents the system from asking the same unresolved question again and again.

**Data flow**: It reads the step’s events → looks only at the most recent event → returns that event if it is a `blocked` event, or returns nothing if the step is not currently blocked.

**Call relations**: This is used when deciding whether a step can run and when recording new blocked events. `ObjectiveView.runnable` avoids blocked steps, and `Objectives.record` uses it to avoid writing a duplicate block with the same evidence.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: This translates a step’s history and condition checks into a simple status such as pending, blocked, done, attempted, or unmet. It is the main rulebook for deciding what a step means right now.

**Data flow**: It reads the step’s latest event, whether it has been attempted, its acceptance conditions, and its saved verdicts → applies the rules in order → returns one state string describing the step’s current status.

**Call relations**: Objective-level views depend on this state. `ObjectiveView.confirmed` counts steps whose state is done, and `ObjectiveView.frontier` uses it to find steps that are not done yet.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: This counts how many times work was recorded across all steps in the objective. It gives a quick sense of effort spent.

**Data flow**: It reads every step and every event inside those steps → counts events marked `did` → returns that count.

**Call relations**: This property summarizes the objective for callers that need to compare effort against confirmed progress. It does not change anything; it reports what the recorded events already say.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: This counts how many steps are currently considered done. It shows confirmed progress, not just claimed effort.

**Data flow**: It reads each step → asks each step for its current state → counts the steps whose state is `done` → returns that count.

**Call relations**: It relies on `StepView.state` for the actual judgment. Together with `ObjectiveView.attempts`, it helps show whether repeated attempts are producing confirmed results.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: This lists the steps that may be started in parallel right now. It only includes steps that were declared independent, have not already been attempted, and are not blocked.

**Data flow**: It starts from the unfinished steps in the frontier → filters to independent steps with no attempt and no open block → returns those steps as a tuple.

**Call relations**: It builds on `ObjectiveView.frontier`, `StepView.attempted`, and `StepView.open_block`. A dispatcher can use this to decide which planned steps are safe to fan out at the same time.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: This returns the remaining work: every step that is not currently done. It is the objective’s active edge, like the visible row of tasks still on the board.

**Data flow**: It reads all steps in order → asks each for its state → keeps only steps whose state is not `done` → returns those unfinished steps.

**Call relations**: Other objective summaries build on this. `ObjectiveView.runnable` narrows the frontier further to the unfinished steps that can be launched independently.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: This turns a machine-readable acceptance condition into a short human-readable sentence. It is useful when explaining what a step is waiting to prove.

**Data flow**: It receives one condition object → checks which kind it is → returns text such as “path exists,” “path contains text,” or “command succeeds.”

**Call relations**: This is a presentation helper for the condition types defined in this file. It does not read the database or affect objective state.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: This looks up one objective by conversation and name. The conversation scope matters because a subagent may reuse a name without accidentally taking over its parent’s objective.

**Data flow**: It receives a conversation ID and objective name → queries the objective table for this workspace, conversation, and name → if found, passes the database row to `_view` to build a complete `ObjectiveView`; otherwise returns nothing.

**Call relations**: It is called directly when someone needs a specific named objective, and `Objectives.plan` uses it before creating or revising a plan. When a row is found, it hands off to `Objectives._view` to gather the related steps, events, and checks.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: This finds the most recently created objective for a conversation. It gives callers a way to resume the current objective without knowing its name.

**Data flow**: It receives a conversation ID → queries the objective table for matching records in this workspace → orders them newest first and takes one → returns a full `ObjectiveView` through `_view`, or nothing if there is no objective.

**Call relations**: This follows the same read path as `Objectives.named`: first find the objective row, then ask `Objectives._view` to assemble the complete readable picture.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: This creates a new objective or revises an existing one. It preserves acceptance conditions for steps that have already been attempted, so a worker cannot loosen the success rules after discovering the work is hard.

**Data flow**: It receives a conversation ID, name, directive, and planned steps → checks whether the objective already exists → inserts or updates the objective row → updates, inserts, or removes step rows according to the new plan while keeping attempted steps’ old acceptance conditions → returns the freshly loaded `ObjectiveView`.

**Call relations**: It begins by calling `Objectives.named` to see what already exists. It then uses database insert, update, and delete operations to make the stored plan match the requested plan, and finally calls `Objectives.named` again so callers get the same complete view that normal readers use.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: This appends an event saying what happened to a step, such as that work was done or the step is blocked. It avoids recording the exact same standing block twice, which would spam the history with repeated unresolved questions.

**Data flow**: It receives a step, event kind, actor turn ID, and evidence text → trims the evidence to the maximum stored length → checks whether this is a duplicate of the current open block → if not duplicate, inserts a new event row → returns true if it wrote something, false if it skipped a duplicate block.

**Call relations**: It uses `StepView.open_block` through the supplied step view to recognize duplicate blockers. Other parts of the extension call this after a worker attempt or block so the next turn can read the durable record instead of relying on temporary memory.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: This saves what the extension found when it evaluated a step’s acceptance conditions. These saved verdicts let later turns know whether the step really passed, even after the live working context is gone.

**Data flow**: It receives a step, a set of condition verdicts, and the actor turn ID → converts each verdict into database-friendly data → inserts a new check row with the current time → changes no existing check rows.

**Call relations**: This is called after the extension evaluates conditions such as file existence or command success. `Objectives._view` later reads the latest saved check for each step and includes it in the `StepView` used by `StepView.state`.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: This builds the full readable picture of an objective from raw database rows. It gathers the objective’s steps, their event history, and their latest condition checks into an `ObjectiveView`.

**Data flow**: It receives one objective row → queries step rows for that objective → queries event rows and check rows for those steps → groups events by step and keeps the latest check per step → parses stored condition data and verdict data → returns an `ObjectiveView` containing `StepView` and `StepEvent` objects.

**Call relations**: `Objectives.named` and `Objectives.on_conversation` call this after finding an objective row. It hands JSON-like stored data to `_conditions` and `_verdicts` so the rest of the code can work with typed condition and verdict objects instead of raw database payloads.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: This converts stored condition data back into the specific condition objects the code understands. It also rejects unknown condition kinds instead of silently pretending they are valid.

**Data flow**: It receives a raw payload, usually read from the database → if the payload is not a list, returns an empty tuple → for each list item, checks its `kind` and validates it as the matching condition type → returns the parsed conditions as a tuple.

**Call relations**: `Objectives._view` uses this when loading a step’s acceptance conditions. `_verdicts` also uses it to parse the condition stored inside each saved verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This converts stored check results into `ConditionVerdict` objects. A verdict says which condition was checked, whether it held, and a short detail about the result.

**Data flow**: It receives a raw payload from the check table → if the payload is not a list, returns an empty tuple → for each dictionary item, parses its embedded condition with `_conditions`, converts the result fields into normal Python values, and creates a `ConditionVerdict` → returns all verdicts as a tuple.

**Call relations**: `Objectives._view` calls this when building each `StepView`. It depends on `_conditions` so verdicts refer to the same validated condition shapes used by planned steps.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).


### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `tool invocation / request handling`

This file solves a practical bottleneck: researching a long list one item at a time is slow and fragile. The wide_research tool reads an entities file, removes blank lines and duplicates, then starts a limited number of research subagents in parallel. Think of it like giving the same worksheet to several assistants at once, each with a different company or topic filled in.

The input names three things: the file of entities, a prompt template containing {entity}, and an optional JSON schema file that tells each child what shape its answer should have. For each entity, the tool builds a prompt, asks a research-profile subagent to write JSON to a private result file, then reads that file back. Good results become rows with a result. Failures become rows with a short error message.

Because this is a side-effecting tool, it uses an idempotency key: a stable call identifier that lets a repeated run reconnect to earlier child work instead of starting everything over. It also writes a recovery aggregate file as rows complete, so partial progress is not lost. At the end it writes wide_research.json, marked as untrusted because it contains web-researched content, and returns the path and collected rows.

#### Function details

##### `_read_lines`  (lines 58–71)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads the user-supplied entities file from the sandbox and turns it into a clean list of unique entity names. It protects the shell command by quoting the file path, so unusual characters in the path are treated as a path, not as a command.

**Data flow**: It receives the tool context and a file path. It asks the sandbox shell to run cat on that path, then checks whether the read succeeded. It splits the file into lines, trims spaces, skips empty lines, removes duplicates while keeping the first occurrence, and returns the final list of entity strings. If the file cannot be read, it raises an error instead of returning a partial list.

**Call relations**: _wide_research calls this near the start, before doing any fan-out work. The clean list it returns becomes the master list that every later step follows: size checking, result-path creation, child spawning, recovery ordering, and final output.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_wide_research`  (lines 74–201)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the main body of the wide_research tool. It takes one batch research request, fans it out to research subagents, saves progress as it goes, and writes a single JSON file containing all rows.

**Data flow**: It receives the tool context and validated input: an entities file, a prompt template, and an output schema file. It first requires an idempotency key, reads and deduplicates entities, refuses overly large batches, computes stable names for recovery and child result files, and removes old recovery files from previous turns. It tries to reload any valid recovery file for this same call, reads the optional schema, then runs one visit task per entity with a semaphore, which is a counter-like lock that limits how many tasks run at once. As each entity finishes, its row is saved into the recovery aggregate. After all visits complete, it writes wide_research.json and returns a ToolResult containing the collected JSON plus the output file name.

**Call relations**: This function is registered as the handler for WIDE_RESEARCH_TOOL, so the tool system calls it when a user invokes wide_research. It calls _read_lines to get the work list, defines small inner helpers for cleanup, recovery writing, row saving, and per-entity visits, then uses asyncio.gather to run the visits together. It hands individual research work to ctx.spawn using the research profile, and hands the final answer back to the tool framework as TextContent inside a ToolResult.

*Call graph*: calls 1 internal fn (_read_lines); 10 external calls (__init__, __init__, __init__, Lock, Semaphore, gather, sha256, dumps, loads, quote).


##### `_wide_research.remove_result_files`  (lines 118–125)

```
async def remove_result_files() -> None
```

**Purpose**: This cleanup helper deletes temporary per-entity result files that were created or recovered during the wide research run. It keeps the workspace from accumulating hidden intermediate files after the tool has collected their contents.

**Data flow**: It reads the shared set of result-file paths that should be removed. If the set is empty, it does nothing. Otherwise it builds a safely quoted rm command and asks the sandbox shell to delete those files. If deletion fails, it raises an operating-system style error with the sandbox’s message.

**Call relations**: _wide_research registers this helper with ctx.cleanup while setting up the run. It is not part of the main research result itself; it is a teardown step that the tool context can call later to remove scratch files after they have served their purpose.

*Call graph*: 1 external calls (quote).


##### `_wide_research.install_recovery`  (lines 129–139)

```
async def install_recovery(rows: tuple[WideResearchRow, ...]) -> None
```

**Purpose**: This helper writes the current aggregate progress to the recovery file in a careful way. Its job is to make sure that, if the run is interrupted, a later retry can pick up completed rows instead of starting from zero.

**Data flow**: It receives an ordered tuple of completed rows. It wraps them in a WideResearchFile object with the current call id, converts that object to JSON, writes it to a temporary staging file, and then moves the staging file into the real recovery path. Writing to a temporary file first is like drafting a replacement page before swapping it into a binder; readers should see either the old complete file or the new complete file, not a half-written one.

**Call relations**: _wide_research uses this helper in two moments: after each saved row through save_row, and once more at the end with the full set of rows. It calls sandbox file-writing and shell move operations, while the surrounding _wide_research flow decides which rows belong in the recovery snapshot.

*Call graph*: 3 external calls (__init__, dumps, quote).


##### `_wide_research.save_row`  (lines 141–147)

```
async def save_row(row: WideResearchRow) -> None
```

**Purpose**: This helper records one finished entity row and immediately refreshes the recovery file. It makes progress durable one row at a time, so a long batch does not lose everything if interrupted late.

**Data flow**: It receives a WideResearchRow, which contains an entity plus either a JSON result or an error. It takes a lock, meaning only one parallel task can update the shared aggregate at a time. Then it stores the row, rebuilds the completed rows in the original entity order, asks install_recovery to write them, and marks that entity’s result file for later cleanup.

**Call relations**: The visit helper calls save_row whenever an entity finishes, whether successfully or with an error. Because many visit tasks run at once, save_row acts as the safe doorway into shared state; it prevents two tasks from writing overlapping recovery snapshots at the same time.


##### `_wide_research.visit`  (lines 149–193)

```
async def visit(entity: str) -> WideResearchRow
```

**Purpose**: This helper performs the research workflow for one entity. It either reuses an already recovered row or asks a research subagent to produce JSON for that specific entity.

**Data flow**: It receives one entity name. It waits for permission from the semaphore so the batch does not start too many child agents at once. If recovery already has this entity, it returns that row immediately. Otherwise it builds a stable child key, chooses the entity’s hidden result path, fills {entity} into the prompt template, adds instructions to write JSON to that path, and spawns a research subagent. After the child finishes or reconnects, it reads the result file. If the file is missing, it records a short error, optionally including the child’s own reported result. If the file exists but is not valid JSON, it records a JSON error. If parsing succeeds, it stores the parsed JSON as the row result. In all non-recovered cases, it calls save_row before returning the row.

**Call relations**: _wide_research creates one visit task for every entity and runs them together with asyncio.gather. visit is the bridge between the batch coordinator and the research subagent: it turns a single entity into a concrete subagent objective, calls ctx.spawn with the research profile, then converts the child’s file output into a WideResearchRow for the aggregate result.

*Call graph*: 4 external calls (__init__, model_validate, loads, quote).


### Website application building
These files hand website-building requests to a specialized child agent, run the controlled app-building workflow, and audit the result for acceptance or repair.

### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `tool invocation during website build requests`

This file exists so the main assistant does not have to build every website directly. Instead, it can delegate the work to a specialized “website_building” subagent, much like asking a dedicated contractor to build and test a site while still working in the same workshop. The child agent has its own conversation history, but it shares this conversation’s sandbox, so any files it creates remain available here afterward.

The file defines the shape of the request through `BuildWebsiteInput`. The most important field is `objective`, which must contain the full brief because the child agent does not know the earlier chat history. Optional fields let the caller give the build a friendly name, preload useful skills before the child starts, or allow more work rounds for a larger site.

The main work is done by `_build_website`, which calls `ctx.spawn` to start or reconnect to the website-building child. It uses the current call’s idempotency key, meaning if the system retries after a crash, it can reconnect to the same delegated build instead of accidentally starting a duplicate one.

Finally, `DELEGATION_TOOLS` registers this as a side-effecting tool because it can create files, run a site, and register a hosted result.

#### Function details

##### `_build_website`  (lines 56–63)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This function performs the actual handoff to the website-building subagent. It takes the user’s build brief and options, starts or reconnects to the child build session, and returns the child agent’s summary as tool output.

**Data flow**: It receives a tool context, which contains things like the current idempotency key, and a `BuildWebsiteInput` object containing the website objective and optional settings. It turns the input into a plain data payload, leaving out missing values, then asks the context to spawn the `website_building` profile with that payload. When the child returns, it converts the child’s output to JSON text if there is any output, wraps that text in a `TextContent`, and returns it inside a `ToolResult`.

**Call relations**: This function is the handler registered for the `build_website` tool. When an agent calls that tool, the tool system invokes `_build_website`; `_build_website` then hands the work to `ToolContext.spawn`, using the website-building profile and the current idempotency key so repeated dispatch can reconnect to the same child run. After the spawned child finishes or reports back, this function packages the result for the original caller.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/application_builder.py`

`orchestration` · `request handling`

This file is the guard-railed workshop for building a hosted application page. Without it, an agent could write files in the wrong place, skip design approval, deploy untested code, or return a site that does not match what was actually checked. The file sets up a fixed scaffold under /workspace/ufo-app, where the only real app source is app.tsx. Before source code is accepted, the builder must first write a visual SVG design contract. That design is parsed, rendered, checked for safe SVG content, checked for named regions, and saved with evidence. Then the builder can write app.tsx, but only if it imports the allowed UFO kit, mounts into the page correctly, avoids unsafe styling shortcuts, and compiles with Vite. If the first write fails, the builder repairs the retained candidate through exact text replacements rather than rewriting freely. The file also creates a simple PNG preview image from a typed design summary so a member can approve the direction before the deeper build begins. Finally, deployment is not trusted just because the worker says it succeeded. ApplicationBuildAcceptance checks stored QA proof, deployed source hashes, site ownership, and homepage binding. In everyday terms, this file is both the assembly line and the quality inspector for one generated app page.

#### Function details

##### `ApplicationBuilderTask.source_is_the_scaffolds_app_tsx`  (lines 525–537)

```
def source_is_the_scaffolds_app_tsx(self) -> 'ApplicationBuilderTask'
```

**Purpose**: Checks that the requested source file is exactly app.tsx inside the requested scaffold folder, and that both live under /workspace. This prevents the builder from being pointed at some other file by mistake or by a malicious path.

**Data flow**: It reads scaffold_path and source_path from the task, converts them into safe workspace-relative paths, and compares them. If the source is exactly scaffold/app.tsx, the task is kept; otherwise validation stops with a clear error.

**Call relations**: This validation runs when an ApplicationBuilderTask is created, including before the worker is spawned and during acceptance checks. It relies on contained_relative to enforce the workspace boundary before any build tool uses the paths.

*Call graph*: 2 external calls (PurePosixPath, contained_relative).


##### `ApplicationBuilderResult.result_matches_status`  (lines 580–589)

```
def result_matches_status(self) -> 'ApplicationBuilderResult'
```

**Purpose**: Makes sure a worker result tells a consistent story. A deployed result must name the deployed site, while a blocked result must explain what stopped it.

**Data flow**: It reads the result status plus site name, site URL, and blocker fields. It either returns the same result as valid, or rejects impossible combinations such as “deployed” with a blocker.

**Call relations**: This runs whenever ApplicationBuilderResult is validated, especially when the parent tool receives the worker’s output. It helps ApplicationBuildAcceptance start from a structurally sane result.


##### `ApplicationBuildAcceptance.accept`  (lines 599–699)

```
async def accept(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult
```

**Purpose**: Decides whether a worker’s claimed deployment can be trusted. It only accepts a deployed app if product QA passed, the deployed site exists, the owner is correct, and the deployed app.tsx matches the approved source.

**Data flow**: It receives a worker result and reads stored QA proof, hosted site records, the deployed source manifest, and the accepted source hash from the sandbox. If all checks match, it binds the site as the agent’s homepage and returns an updated deployed result; if anything is missing or mismatched, it returns a blocked result.

**Call relations**: build_ufo_application calls this after the child builder returns. It hands failures to ApplicationBuildAcceptance._blocked and uses helper paths such as _source_acceptance_path and _runtime_root to compare what was QA-checked with what was deployed.

*Call graph*: calls 3 internal fn (_blocked, _runtime_root, _source_acceptance_path); 6 external calls (__init__, __init__, model_validate, model_copy, model_validate_json, site_url).


##### `ApplicationBuildAcceptance._blocked`  (lines 701–714)

```
def _blocked(self, result: ApplicationBuilderResult, reason: str, browser_batches: int) -> ApplicationBuilderResult
```

**Purpose**: Builds a clean blocked result when acceptance finds a problem. It preserves useful evidence, such as checked controls and observed errors, while replacing the success claim with a specific blocker reason.

**Data flow**: It takes the original result, a reason, and a browser QA count. It creates a new ApplicationBuilderResult with status blocked, the fixed source path, copied evidence fields, and the supplied blocker message.

**Call relations**: ApplicationBuildAcceptance.accept calls this whenever a deployment claim fails one of its checks. It is the common exit ramp from an untrusted or incomplete worker result.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `EditApplicationSourceInput.json_text_edits_are_objects`  (lines 762–784)

```
def json_text_edits_are_objects(cls, value: object) -> object
```

**Purpose**: Accepts source edits in a few convenient text formats and normalizes them into structured old_text/new_text replacements. This makes the repair tool more forgiving without making the actual edit rules loose.

**Data flow**: It receives the raw edits value. String items that look like JSON are parsed, patch-style SEARCH/REPLACE strings are split into old and new text, and paired strings can become replacement objects. The output is a tuple of normalized edit entries for later validation.

**Call relations**: This validator runs before EditApplicationSourceInput is fully validated. edit_application_source later uses the cleaned edits to apply exact replacements.

*Call graph*: 1 external calls (loads).


##### `_validate_application_source`  (lines 787–828)

```
def _validate_application_source(source: str) -> None
```

**Purpose**: Checks that app.tsx follows the product’s rules before it can be compiled or accepted. It protects the app from unsupported imports, unsafe style escape hatches, and design-system violations.

**Data flow**: It reads the full source text and scans it with several patterns. If the source imports only from ufo/kit, mounts correctly, avoids exports and forbidden styling patterns, and follows spacing/token rules, nothing is returned; otherwise it raises a repair-focused error.

**Call relations**: write_application_source and edit_application_source call this before compiling and publishing source. It is the main source-code gate in the builder’s workflow.

*Call graph*: called by 2 (edit_application_source, write_application_source).


##### `_validate_application_design`  (lines 831–944)

```
def _validate_application_design(source: str) -> tuple[tuple[str, ...], int]
```

**Purpose**: Checks that the SVG design contract is safe, measurable, and useful before source work begins. It makes sure the design is a plain drawing with the expected width, height, named regions, and no active or external content.

**Data flow**: It receives SVG text, parses it as XML, inspects the root size, drawing elements, IDs, effects, scripts, external links, and data-app-region labels. It returns the ordered region names and page height, or raises a clear error if the design breaks the rules.

**Call relations**: write_application_design calls this before browser-rendering the design, and _require_application_design calls it again before source writing. It feeds region names and height into _render_application_design.

*Call graph*: called by 2 (_require_application_design, write_application_design); 3 external calls (isfinite, split, fromstring).


##### `_build_application_project`  (lines 947–961)

```
async def _build_application_project(ctx: ToolContext, project: str, runtime_root: str | None=None) -> None
```

**Purpose**: Builds the application project with the UFO page kit and Vite compiler. This catches real TypeScript or bundling problems before a file is accepted.

**Data flow**: It receives a project path and optionally a runtime root. It writes the project config, unpacks the page kit, runs vite build, and either finishes silently or raises an error containing the compiler message.

**Call relations**: _compile_application_source uses this for temporary compile checks, while write_application_source and edit_application_source use it again on the real scaffold after publishing app.tsx.

*Call graph*: called by 3 (_compile_application_source, edit_application_source, write_application_source); 1 external calls (unpack_page_kit).


##### `_compile_application_source`  (lines 964–977)

```
async def _compile_application_source(ctx: ToolContext, task: ApplicationBuilderTask, source: str) -> None
```

**Purpose**: Compiles a proposed app.tsx in a temporary project before it touches the real scaffold. This is like test-fitting a part before bolting it onto the machine.

**Data flow**: It receives the tool context, the build task, and source text. It copies the existing index.html and proposed app.tsx into a runtime check folder, then calls _build_application_project. It returns nothing if the source compiles, or raises if it fails.

**Call relations**: write_application_source and edit_application_source call this after source validation. It depends on _runtime_root and _build_application_project to create and build the isolated check project.

*Call graph*: calls 2 internal fn (_build_application_project, _runtime_root); called by 2 (edit_application_source, write_application_source).


##### `_source_claim_path`  (lines 980–984)

```
async def _source_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path used to record that this builder turn owns the initial app.tsx candidate. The path includes a hash so the marker is stable but does not expose raw path details unnecessarily.

**Data flow**: It reads the task source path and turn id, hashes the source path, and asks the sandbox for the matching runtime path. The result is a claim-file path.

**Call relations**: write_application_source uses this to claim the first source write, and _require_application_source uses it to confirm that a candidate exists before reading or editing.

*Call graph*: called by 2 (_require_application_source, write_application_source); 1 external calls (sha256).


##### `_runtime_root`  (lines 987–988)

```
async def _runtime_root(ctx: ToolContext) -> str
```

**Purpose**: Finds the sandbox runtime root used by the small helper scripts. These scripts need a trusted base folder when reading and writing contained files.

**Data flow**: It asks the sandbox for the runtime path to tool-output, takes its parent folder, and returns that as a string. It does not change files itself.

**Call relations**: Many source, design, acceptance, and delegation functions call this before running containment-aware Python snippets. It is the shared doorway into runtime-owned files.

*Call graph*: called by 9 (accept, _compile_application_source, _require_application_design, _require_application_source, build_ufo_application, edit_application_source, read_application_source, write_application_design, write_application_source); 1 external calls (PurePosixPath).


##### `_design_path`  (lines 991–992)

```
def _design_path(task: ApplicationBuilderTask) -> str
```

**Purpose**: Returns the fixed SVG design path for a build task. The design always lives beside app.tsx in the scaffold.

**Data flow**: It reads the task scaffold path and appends application-design.svg. The output is the workspace path where the visual contract should be written.

**Call relations**: write_application_design uses this as the target design path, while _design_claim_path and _require_application_design use it to locate the design’s ownership and presence checks.

*Call graph*: called by 3 (_design_claim_path, _require_application_design, write_application_design).


##### `_design_claim_path`  (lines 995–999)

```
async def _design_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path used to record that this builder turn owns the accepted design. This prevents competing or repeated design writes from silently overwriting each other.

**Data flow**: It derives the design path from the task, hashes it, combines it with the turn id, and asks the sandbox for the runtime path. The output is a claim-file path.

**Call relations**: write_application_design creates and later cleans up this claim. _require_application_design checks it before allowing app.tsx to be written.

*Call graph*: calls 1 internal fn (_design_path); called by 2 (_require_application_design, write_application_design); 1 external calls (sha256).


##### `application_design_acceptance_relative`  (lines 1002–1008)

```
def application_design_acceptance_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Builds the relative runtime filename for the accepted SVG design. Other code can use this predictable location to find the product-approved design for one builder turn.

**Data flow**: It takes a design path and turn id, hashes the design path, and returns a relative path ending in accepted.svg. It does not touch the filesystem.

**Call relations**: write_application_design uses this to choose where the accepted design copy is stored after validation.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `application_design_evidence_relative`  (lines 1011–1017)

```
def application_design_evidence_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Builds the relative runtime filename for the accepted design evidence JSON. The evidence records what the browser audit saw, such as visible regions.

**Data flow**: It takes a design path and turn id, hashes the design path, and returns a relative path ending in accepted-design.json. It does not read or write files.

**Call relations**: write_application_design uses this alongside application_design_acceptance_relative so the design and its proof are stored as a matched pair.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `_source_candidate_path`  (lines 1020–1026)

```
async def _source_candidate_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path for the current editable app.tsx candidate. Repairs happen against this candidate before the real workspace file is updated.

**Data flow**: It reads the task source path and turn id, hashes the source path, and returns a runtime path for a candidate .tsx file. No file is changed by this helper.

**Call relations**: write_application_source writes the first candidate there. read_application_source and edit_application_source later read or update the same candidate during repair.

*Call graph*: called by 3 (edit_application_source, read_application_source, write_application_source); 1 external calls (sha256).


##### `_render_application_design`  (lines 1029–1075)

```
async def _render_application_design(ctx: ToolContext, candidate_path: str, names: tuple[str, ...], page_height: int) -> tuple[ApplicationAuditRegion, ...]
```

**Purpose**: Runs a browser-style audit of the SVG design and turns its output into checked region data. This catches visual problems that plain XML parsing cannot see, such as overlaps or content outside the view box.

**Data flow**: It receives a candidate SVG path, expected region names, and page height. It writes the audit script, runs Node.js against the SVG, validates the JSON output, checks that visible regions match and do not overlap, and returns the audited region records.

**Call relations**: write_application_design calls this after _validate_application_design. It also uses application_region_relation from the audit module to confirm region relationships.

*Call graph*: called by 1 (write_application_design); 2 external calls (search, application_region_relation).


##### `_source_acceptance_path`  (lines 1078–1084)

```
async def _source_acceptance_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Creates the runtime path where the accepted source hash is stored. That hash is later used to prove that QA, deployment, and accepted source all refer to the same app.tsx.

**Data flow**: It hashes the task source path, combines it with the turn id, and returns a runtime path ending in accepted. It does not write the hash itself.

**Call relations**: write_application_source and edit_application_source write the accepted hash there after a successful build. ApplicationBuildAcceptance.accept reads it during final deployment acceptance.

*Call graph*: called by 3 (accept, edit_application_source, write_application_source); 1 external calls (sha256).


##### `_require_application_source`  (lines 1087–1096)

```
async def _require_application_source(ctx: ToolContext, task: ApplicationBuilderTask) -> None
```

**Purpose**: Refuses repair actions unless an initial source candidate has already been claimed. This keeps read and edit tools from operating before there is a file to repair.

**Data flow**: It computes the source claim path, runs a contained helper script to check for a regular file, and either returns silently, raises a user-facing ordering error, or raises a runtime error for unexpected failures.

**Call relations**: read_application_source and edit_application_source call this before touching the candidate. It uses _source_claim_path and _runtime_root.

*Call graph*: calls 2 internal fn (_runtime_root, _source_claim_path); called by 2 (edit_application_source, read_application_source).


##### `_require_application_design`  (lines 1099–1112)

```
async def _require_application_design(ctx: ToolContext, task: ApplicationBuilderTask) -> None
```

**Purpose**: Refuses app.tsx writing until a design contract has been accepted. This enforces the workflow: design first, code second.

**Data flow**: It checks the design claim file, reads the design SVG from the workspace, and re-validates the SVG. If the claim or design is missing or invalid, it raises an error telling the builder to complete write_application_design first.

**Call relations**: write_application_source calls this before claiming source ownership. It uses _design_claim_path, _design_path, _runtime_root, and _validate_application_design.

*Call graph*: calls 4 internal fn (_design_claim_path, _design_path, _runtime_root, _validate_application_design); called by 1 (write_application_source).


##### `write_application_design`  (lines 1115–1254)

```
async def write_application_design(ctx: ToolContext, args: WriteApplicationDesignInput) -> ToolResult
```

**Purpose**: Accepts and publishes one full SVG design contract for the application. It is the official design gate before any app source can be written.

**Data flow**: It reads the builder task and idempotency key, validates the SVG text, writes a candidate copy, browser-audits it, creates evidence, claims ownership, publishes accepted runtime copies, and finally writes the design into the workspace. It returns JSON describing the path, digest, size, height, and rendered regions.

**Call relations**: This is exposed as the write_application_design tool for the application builder profile. It coordinates _validate_application_design, _render_application_design, the acceptance/evidence path helpers, _design_claim_path, _runtime_root, and _complete_application_design_cleanup if anything fails partway through.

*Call graph*: calls 8 internal fn (_complete_application_design_cleanup, _design_claim_path, _design_path, _render_application_design, _runtime_root, _validate_application_design, application_design_acceptance_relative, application_design_evidence_relative); 6 external calls (__init__, __init__, __init__, sha256, dumps, application_design_region_size_failure).


##### `_complete_application_design_cleanup`  (lines 1257–1273)

```
async def _complete_application_design_cleanup(ctx: ToolContext, program: str, *args: str) -> tuple[ExecResult | None, tuple[str, ...]]
```

**Purpose**: Finishes cleanup of design claims or accepted design files even if cancellation happens. It tries hard not to leave half-owned design state behind.

**Data flow**: It starts a sandbox Python cleanup program with the supplied arguments and waits for it while shielding the task from cancellation. It returns the cleanup result if available plus any interruption or cleanup failure messages.

**Call relations**: write_application_design calls this when publishing the design fails after a claim or accepted files may have been created. It is the safety broom for the design-write transaction.

*Call graph*: called by 1 (write_application_design); 2 external calls (create_task, shield).


##### `read_application_source`  (lines 1276–1330)

```
async def read_application_source(ctx: ToolContext, args: ReadApplicationSourceInput) -> ToolResult
```

**Purpose**: Shows limited, useful excerpts from the current app.tsx candidate during repair. It avoids dumping the whole file while still helping the builder find exact text to replace.

**Data flow**: It validates that a source candidate exists, reads the candidate file, searches for requested terms, builds small line-numbered windows around matches plus the top and bottom of the file, and returns those excerpts as tool text.

**Call relations**: This is exposed as the read_application_source tool. It is normally used after an edit mismatch, and it depends on _require_application_source, _source_candidate_path, and _runtime_root.

*Call graph*: calls 3 internal fn (_require_application_source, _runtime_root, _source_candidate_path); 2 external calls (__init__, __init__).


##### `edit_application_source`  (lines 1333–1381)

```
async def edit_application_source(ctx: ToolContext, args: EditApplicationSourceInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to the current app.tsx candidate and accepts the result only if it still validates and builds. It is the repair path after the initial full write.

**Data flow**: It reads the candidate source, checks that each old_text appears exactly once and edits do not overlap, applies replacements, checks size, validates product source rules, compiles in a temporary project, writes the real workspace file, builds the real scaffold, records the accepted source hash, and returns edit counts and size.

**Call relations**: This is exposed as the edit_application_source tool. It follows read_application_source in repair loops and calls _require_application_source, _source_candidate_path, _validate_application_source, _compile_application_source, _build_application_project, and _source_acceptance_path.

*Call graph*: calls 7 internal fn (_build_application_project, _compile_application_source, _require_application_source, _runtime_root, _source_acceptance_path, _source_candidate_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `write_application_source`  (lines 1384–1428)

```
async def write_application_source(ctx: ToolContext, args: WriteApplicationSourceInput) -> ToolResult
```

**Purpose**: Writes the first complete app.tsx candidate for the build. It only succeeds after a design is accepted, and it forces later fixes through the repair tools.

**Data flow**: It checks that a design exists, claims the source write, stores the candidate source in runtime storage, validates the source rules, compiles it, writes it to the scaffold, builds the real project, records the accepted source hash, and returns the path and byte size. If validation fails, the candidate is kept for repair instead of allowing another full rewrite.

**Call relations**: This is exposed as the write_application_source tool for the child builder. It calls _require_application_design, _source_claim_path, _source_candidate_path, _validate_application_source, _compile_application_source, _build_application_project, _source_acceptance_path, and _runtime_root.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _runtime_root, _source_acceptance_path, _source_candidate_path, _source_claim_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `_preview_font`  (lines 1431–1432)

```
def _preview_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont
```

**Purpose**: Provides a font object for drawing the preview image. It centralizes the simple font choice used by the preview renderer.

**Data flow**: It receives a font size and returns Pillow’s default font at that size. It does not read project state or change files.

**Call relations**: _application_preview and _preview_card call this whenever they draw text into the generated PNG.

*Call graph*: called by 2 (_application_preview, _preview_card); 1 external calls (load_default).


##### `_preview_text`  (lines 1435–1441)

```
def _preview_text(value: str, width: int, lines: int) -> str
```

**Purpose**: Wraps and trims text so it fits into a preview area. If the text is too long, it ends the last visible line with an ellipsis.

**Data flow**: It receives a string, a character width, and a maximum line count. It strips and wraps the text, keeps only the allowed lines, possibly shortens the final line, and returns the display string.

**Call relations**: _application_preview and _preview_card call this before drawing purpose, priority, region, and direction text.

*Call graph*: called by 2 (_application_preview, _preview_card); 1 external calls (wrap).


##### `_preview_card`  (lines 1444–1477)

```
def _preview_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, detail: str) -> None
```

**Purpose**: Draws one region card in the preview PNG. A card shows the region name and a short placeholder description.

**Data flow**: It receives a drawing surface, a rectangle, a title, and detail text. It draws a rounded box, title, divider line, and optional detail text inside that box. It returns nothing because it changes the image directly.

**Call relations**: _application_preview calls this once for each requested region, after _preview_boxes decides where each card should go.

*Call graph*: calls 2 internal fn (_preview_font, _preview_text); called by 1 (_application_preview); 3 external calls (line, rounded_rectangle, text).


##### `_preview_boxes`  (lines 1480–1524)

```
def _preview_boxes(layout: ApplicationLayout, count: int) -> tuple[tuple[int, int, int, int], ...]
```

**Purpose**: Calculates where region cards should sit in the preview image for each layout type. It turns layout names like timeline or metrics into simple rectangles.

**Data flow**: It receives a layout and region count. Based on fixed preview dimensions and gaps, it returns a tuple of box coordinates for the cards.

**Call relations**: _application_preview calls this before drawing cards. Its output is paired with the region names and passed to _preview_card.

*Call graph*: called by 1 (_application_preview).


##### `_application_preview`  (lines 1527–1594)

```
def _application_preview(args: RenderApplicationPreviewInput) -> bytes
```

**Purpose**: Creates the preview PNG from the member-facing design contract. It gives a quick visual sketch without running the full app or touching deployment.

**Data flow**: It receives purpose, first-screen priority, regions, layout, and optional design direction. It creates a blank image, draws a header, summary area, region cards, and footer direction text, then returns the PNG bytes.

**Call relations**: _PillowApplicationPreview.render calls this as the concrete renderer. It uses _preview_font, _preview_text, _preview_boxes, and _preview_card to assemble the image.

*Call graph*: calls 4 internal fn (_preview_boxes, _preview_card, _preview_font, _preview_text); called by 1 (render); 3 external calls (new, Draw, BytesIO).


##### `_PillowApplicationPreview.render`  (lines 1599–1600)

```
def render(args: RenderApplicationPreviewInput) -> bytes
```

**Purpose**: Provides a small wrapper around the Pillow preview renderer. It gives the rest of the file a simple render method that can be run in a worker thread.

**Data flow**: It receives the typed preview input and passes it directly to _application_preview. The output is PNG bytes.

**Call relations**: render_application_preview calls this through asyncio.to_thread so image drawing does not block the async request flow.

*Call graph*: calls 1 internal fn (_application_preview).


##### `homepage_design_block`  (lines 1603–1616)

```
def homepage_design_block(contract: RenderApplicationPreviewInput) -> str
```

**Purpose**: Turns the approved preview contract into plain text instructions for the application builder. This keeps the builder aligned with the exact regions, order, layout, and direction the member saw.

**Data flow**: It reads the preview contract fields, fills in the default direction if needed, and returns a multi-line “Homepage design” block.

**Call relations**: render_application_preview calls this when returning the preview result. The resulting text can be carried into the application-building prompt.

*Call graph*: called by 1 (render_application_preview).


##### `render_application_preview`  (lines 1619–1632)

```
async def render_application_preview(ctx: ToolContext, args: RenderApplicationPreviewInput) -> ToolResult
```

**Purpose**: Generates and shares one PNG preview from a typed design summary. It is a safe, stateless preview step before the heavier build workflow.

**Data flow**: It receives the preview input, renders PNG bytes in a background thread, shares the image artifact, hashes the contract JSON to make a design digest, builds a structured result, and returns it as tool text.

**Call relations**: This is exposed as the render_application_preview tool. It calls _PillowApplicationPreview.render through asyncio.to_thread, shares the artifact through ToolContext, and uses homepage_design_block for the text contract.

*Call graph*: calls 2 internal fn (share_artifact, homepage_design_block); 7 external calls (__init__, __init__, __init__, to_thread, model_dump, sha256, dumps).


##### `_ensure_application_scaffold`  (lines 1635–1643)

```
async def _ensure_application_scaffold(ctx: ToolContext) -> None
```

**Purpose**: Makes sure the fixed application scaffold files exist before the child builder starts. It creates index.html, placeholder app.tsx, and preview.html only if they are missing.

**Data flow**: It checks each expected scaffold path by trying to read a byte. For missing files, it writes the built-in default content into the sandbox workspace.

**Call relations**: build_ufo_application calls this before spawning the child builder so the worker starts from a known project shape.

*Call graph*: called by 1 (build_ufo_application).


##### `build_ufo_application`  (lines 1646–1714)

```
async def build_ufo_application(ctx: ToolContext, _args: BuildUfoApplicationInput) -> ToolResult
```

**Purpose**: Delegates the current member request to the specialized application builder worker and returns the accepted result. It is the parent-facing tool that starts the controlled build process.

**Data flow**: It checks extension context and idempotency, claims that delegation for this turn, records redeploy and audit contract metadata when present, ensures the scaffold exists, builds the worker objective, spawns the application builder profile, then validates or blocks the worker output through ApplicationBuildAcceptance. It returns the final structured result as tool text.

**Call relations**: This is exposed as the build_ufo_application tool. It starts the child subagent, calls _ensure_application_scaffold and _runtime_root, and hands the child result to ApplicationBuildAcceptance.accept.

*Call graph*: calls 2 internal fn (_ensure_application_scaffold, _runtime_root); 9 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, format, format).


##### `limit_application_builder_repair_reads`  (lines 1717–1752)

```
async def limit_application_builder_repair_reads(ctx: HookContext) -> Deny | None
```

**Purpose**: Limits how many times the builder can keep rereading source during a repair attempt without editing. This nudges the worker toward making fixes instead of looping on inspection.

**Data flow**: It reads the hook context, ignores unrelated turns and tools, checks whether a product audit repair attempt exists, resets the read count when an edit happens, increments it when a read happens, and returns a denial once the limit is reached.

**Call relations**: This runs as a pre-tool-use hook for the application builder profile. It can deny read_application_source after too many repair reads, while allowing edit_application_source to reset the count.

*Call graph*: 2 external calls (__init__, format).


##### `require_application_builder_qa`  (lines 1755–1768)

```
async def require_application_builder_qa(ctx: HookContext) -> Deny | None
```

**Purpose**: Blocks deployment until product-owned QA proof exists and is valid. It prevents the builder from deploying an app that has not passed the deterministic browser checks.

**Data flow**: It reads the current builder turn and looks up the stored QA proof for that turn. If no proof exists, it returns a denial; if proof exists but is malformed, it raises a runtime error; otherwise it allows the tool call.

**Call relations**: This hook is meant to guard the deployment tool for the application builder profile. ApplicationBuildAcceptance later relies on the same proof to compare the deployed source against the QA-checked source.

*Call graph*: 2 external calls (__init__, model_validate).


### `extensions/sites/ufo_ext_sites/application_audit.py`

`domain_logic` · `application audit / acceptance checking`

This file is a deterministic quality gate for an interactive application. In plain terms, it asks: did the page render in the required sizes and color modes, is the text readable, does the layout avoid broken or overlapping content, does it match the accepted design, and can a user actually interact with it? Without this file, the system would have no shared, repeatable standard for accepting an application or telling the builder what to fix.

Most of the file defines typed records using Pydantic, a validation library that checks incoming data has the expected shape. These records describe facts the app must show, measured browser views, text contrast failures, visible regions, controls, interactions, audit issues, and final proof objects.

The main audit function, `audit_application`, receives a complete browser report. It checks for missing views, empty pages, low contrast, horizontal overflow, clipped text, accidental overlaps, browser console errors, too few controls, too few successful interactions, missing required facts, and facts that are not visible near the top of the desktop page. A separate design-fidelity check compares named layout regions from the accepted design with the rendered desktop app. The result is an `ApplicationAuditVerdict`: either no issues, meaning it passed, or a bounded list of concrete repair messages.

#### Function details

##### `AcceptedApplicationDesignEvidence.regions_are_unique`  (lines 129–133)

```
def regions_are_unique(self) -> 'AcceptedApplicationDesignEvidence'
```

**Purpose**: This validation step makes sure an accepted design does not name two visible regions the same thing. Unique names matter because later checks compare design regions to application regions by name.

**Data flow**: It reads the `regions` already placed on the design evidence object → collects their names → compares the number of names with the number of unique names → returns the same object if all names are unique, or raises an error before the object can be accepted.

**Call relations**: This is called automatically by Pydantic when an `AcceptedApplicationDesignEvidence` object is created. It protects later design comparison code from confusing two different regions that share one label.


##### `ApplicationAuditReport.views_are_unique`  (lines 198–202)

```
def views_are_unique(self) -> 'ApplicationAuditReport'
```

**Purpose**: This validation step makes sure the browser report has at most one measurement for each combination of color scheme and viewport width. The audit relies on each required view being unambiguous.

**Data flow**: It reads the report’s `views` → turns each view into a key made from its color scheme and width → checks for duplicate keys → returns the same report if all view keys are unique, or raises an error if duplicates are present.

**Call relations**: This runs automatically when Pydantic builds an `ApplicationAuditReport`. It gives `audit_application` a clean report where looking up, for example, the dark 1440px view has one clear answer.


##### `ApplicationAuditVerdict.passed`  (lines 223–226)

```
def passed(self) -> bool
```

**Purpose**: This property gives a simple yes-or-no answer: the audit passed only when there are no repair issues. It is a convenience for callers that do not want to inspect the issue list themselves.

**Data flow**: It reads the verdict’s `issues` tuple → checks whether that tuple is empty → returns `True` if there are no issues and `False` otherwise. It does not change the verdict.

**Call relations**: Code that receives an `ApplicationAuditVerdict` can use this property after `audit_application` has finished. It turns the detailed repair report into a single pass/fail signal.


##### `_needed_ratio`  (lines 273–278)

```
def _needed_ratio(item: ApplicationAuditText) -> float
```

**Purpose**: This helper decides the minimum contrast ratio a piece of text needs. Contrast ratio is the difference between text color and background color; higher means easier to read.

**Data flow**: It receives one measured text item → checks whether it is in a special quiet UI slot, or whether it is large or bold enough to use the lower large-text standard → returns the required numeric contrast threshold for that item.

**Call relations**: `audit_application` calls this while reviewing every measured text sample. The returned threshold is used to decide whether each text item should become a contrast repair issue.

*Call graph*: called by 1 (audit_application).


##### `_issue`  (lines 281–288)

```
def _issue(code: AuditIssueCode, message: str, terms: tuple[str, ...]=()) -> ApplicationAuditIssue
```

**Purpose**: This helper creates one audit issue while enforcing the file’s size limits for messages and search terms. It keeps feedback compact so the builder receives a bounded, readable repair list.

**Data flow**: It receives an issue code, a message, and optional related terms → trims the message to the maximum allowed length and keeps only the first ten terms → creates and returns an `ApplicationAuditIssue` object.

**Call relations**: `audit_application` calls this whenever it finds a problem, such as missing views, poor contrast, or failed interactions. The helper hands back standardized issue objects that are later packed into the final verdict.

*Call graph*: called by 1 (audit_application); 1 external calls (__init__).


##### `application_first_screen_scale`  (lines 291–301)

```
def application_first_screen_scale(page_height: int) -> float
```

**Purpose**: This helper converts the fixed first-screen height into a fraction of a measured page. It lets layout rules mean the same physical pixel size even when the full page is taller.

**Data flow**: It receives a page height in pixels → divides the fixed first-screen height, 844 pixels, by that page height → returns the scaling factor used for vertical measurements.

**Call relations**: `application_region_relation` uses it to keep the allowed near-touch gap realistic on tall pages. `application_design_region_size_failure` uses it so minimum region height and area are judged consistently across accepted page heights.

*Call graph*: called by 2 (application_design_region_size_failure, application_region_relation).


##### `application_region_relation`  (lines 304–324)

```
def application_region_relation(first: ApplicationAuditRegion, second: ApplicationAuditRegion, page_height: int=APPLICATION_DESIGN_FOLD) -> tuple[Literal['horizontal', 'vertical'], int] | None
```

**Purpose**: This function tells whether two visible regions are separated vertically or horizontally, and in which order. It is used to check whether the app keeps the same layout order as the accepted design.

**Data flow**: It receives two regions and the page height their coordinates were measured against → scales the small vertical tolerance for that page height → compares the regions’ top, height, left, and width values → returns a pair such as `("vertical", -1)` or `("horizontal", 1)` when one region clearly comes before the other, or `None` if they overlap or cannot be separated.

**Call relations**: `application_design_fidelity` calls this first to reject overlapping design regions, then again to compare each matching pair of design and rendered application regions. It depends on `application_first_screen_scale` so vertical spacing is judged fairly on taller pages.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_size_failure`  (lines 327–345)

```
def application_design_region_size_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function checks whether any accepted design region is too small to count as a meaningful visible part of the screen. Tiny regions would make design matching unreliable, like trying to compare a whole room by looking at a postage stamp.

**Data flow**: It receives the design regions and the page height → calculates the first-screen scaling factor → checks each region’s width, height, and area against minimum useful sizes → returns the first human-readable failure message it finds, or `None` if all regions are large enough.

**Call relations**: `application_design_fidelity` calls this before comparing the design to the app. If a design region is too small, fidelity checking stops early because the design evidence itself is not strong enough.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_fidelity`  (lines 348–431)

```
def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity
```

**Purpose**: This function scores how well the rendered desktop application matches the accepted design’s named layout regions. It checks region count, names, useful size, overlap, above-the-fold visibility, and relative order.

**Data flow**: It receives a full audit report → reads the accepted design regions and the measured desktop views → first validates that the design has the right number of unique, non-overlapping, useful regions → then, for both light and dark desktop views, compares region names, visibility near the top of the page, and pair-by-pair layout order → returns an `ApplicationDesignFidelity` object with passed checks, total checks, and failure messages.

**Call relations**: `audit_application` calls this as the design portion of the overall audit. Inside, it uses `application_design_region_size_failure` to reject weak design regions and `application_region_relation` to compare layout order between the design and the rendered app.

*Call graph*: calls 2 internal fn (application_design_region_size_failure, application_region_relation); called by 1 (audit_application); 1 external calls (__init__).


##### `audit_application`  (lines 434–570)

```
def audit_application(report: ApplicationAuditReport, contract: ApplicationAuditContract | None=None) -> ApplicationAuditVerdict
```

**Purpose**: This is the main acceptance check for a staged application. It turns raw browser evidence and optional required facts into a final verdict that either passes or tells the builder what to repair.

**Data flow**: It receives an `ApplicationAuditReport` and, optionally, an `ApplicationAuditContract` containing facts the page must show → organizes measured views by color scheme and width → checks for missing or empty views, unreadable contrast, horizontal overflow, clipped content, overlaps, design mismatch, browser errors, too few controls, too few successful interactions, missing facts, and required facts not visible above the desktop fold → creates a bounded list of standardized issues → returns an `ApplicationAuditVerdict` containing those issues.

**Call relations**: This function is the file’s central flow. It calls `_needed_ratio` while judging text contrast, `_issue` whenever it needs to add a repair item, and `application_design_fidelity` for the design-match portion. At the end it constructs the `ApplicationAuditVerdict` that downstream builder logic can inspect or send back as feedback.

*Call graph*: calls 3 internal fn (_issue, _needed_ratio, application_design_fidelity); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-workflow-claims` — The workflow attempt and run-claim state that prevents two workers from running the same turn, scheduled task, listener, or cleanup job at once.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-subagent-tasks` — The parent-child delegation state that tracks spawned helper agents, their inputs, outputs, names, costs, and undelivered results.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-objectives-plan-store` — Durable objective, plan, step, evidence, and blocker records used by multi-step agent workflows and objective-tracking extensions.
- `reg-turn-created-reference-index` — Durable per-turn list of objects, artifacts, sites, files, or other references created during a turn for later transcript display, panels, delivery, and recovery.
