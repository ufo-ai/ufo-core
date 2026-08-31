# Turn engine claim, context assembly, and prompt construction  `stage-9`

This stage is the preparation room for one agent turn, before and during the first model call. A turn is one piece of pending conversation work. The queue safely claims that work, makes sure only one runner owns it, and records success or recovery if something crashes. The engine is the traffic controller: it gathers the conversation, watches for cancellations or spending limits, absorbs new user messages that arrive late, calls tools and the model, saves the transcript, and publishes the result.

Several helpers build the agent’s “briefing packet.” Compaction shortens old conversation history into a checked summary while keeping recent messages unchanged, so the model has room to think. Skill selection chooses only the most relevant saved skills, instead of flooding the prompt. The delivery register loads the writing rules agents must follow when answering or reporting to a parent agent. The conversation slot code adds visible hosted sites as safe links when the viewer is allowed to see them. Finally, prompt rendering fills the approved template, checks every slot, and creates a digest, like a fingerprint, so the exact prompt can be traced later.

## Files in this stage

### Turn work orchestration
These files claim pending turn work and coordinate the durable execution path for a complete agent turn.

### `core/src/ufo/loop/queue.py`

`orchestration` · `turn queue processing`

This file is the traffic controller for agent work. A turn is put on a DBOS queue, where DBOS is the workflow system that can replay work after a crash. The queue is partitioned by conversation, so turns in the same conversation run in order, like customers waiting in a single checkout lane.

When a turn starts, the file loads the turn, agent, audience, tools, skills, subagents, model choice, billing rules, credentials, sandbox access, and transcript storage. It then builds a TurnEngine, which does the actual model-and-tool loop. This file is mostly the careful setup around that engine: deciding which tools are allowed, which saved skills appear in the prompt, whether the workspace’s own model key is being used, and how a sandbox should be opened or authorized.

It also protects the system from half-finished work. If a turn was already claimed elsewhere, it repairs the transcript and exits. If setup fails before the engine can write a result, it writes a failed terminal result itself and publishes it so clients stop waiting. If a child or subagent turn finishes, it delivers the result back to the parent conversation. Without this file, turns could run out of order, lose billing consistency, leak tools they should not have, or leave users waiting forever after failures.

#### Function details

##### `_without_workspace_skills`  (lines 160–163)

```
async def _without_workspace_skills(name: str) -> None
```

**Purpose**: This is a harmless placeholder used when an agent has workspace skills turned off. It makes sure no saved workspace skill is loaded by name for that agent.

**Data flow**: It receives a skill name, ignores it, and returns nothing. The before state is “a skill name was requested”; the after state is “no skill was provided.”

**Call relations**: It is chosen inside the turn setup path when workspace skills are disabled. That lets the rest of the skill-loading machinery use the same shape of callback without actually exposing workspace skills.


##### `_member_skill_turn`  (lines 166–178)

```
def _member_skill_turn(turn: Turn) -> bool
```

**Purpose**: This decides whether a turn is the kind of turn where member-visible saved skills should be considered. It keeps skills out of special internal or prepared-intent turns where they would not match the normal user prompt flow.

**Data flow**: It reads the turn’s admission source, speaker, and parent information. From those fields it returns true for ordinary skill-relevant turns and false for prepared intents or a root internal turn with no speaker.

**Call relations**: It is used by _member_skill_block to decide whether to add a skill block to the prompt, and by _run_turn to decide whether to run background evidence gathering for skill selection.

*Call graph*: called by 2 (_member_skill_block, _run_turn).


##### `_member_skill_block`  (lines 181–188)

```
def _member_skill_block(turn: Turn, view: MemberVisibility, enabled: bool) -> str
```

**Purpose**: This chooses the saved-skills text that should be inserted into the model’s prompt for this turn. It returns an empty string when the feature is off or when this is not a skill-relevant turn.

**Data flow**: It takes the turn, a prepared view of visible member skills, and a feature flag. It checks whether the turn qualifies, then either returns the view’s text block or returns an empty block.

**Call relations**: During _run_turn, after skill visibility is computed, this function provides the actual prompt text that the engine will later receive.

*Call graph*: calls 1 internal fn (_member_skill_turn); called by 1 (_run_turn).


##### `_prompt_skill_index`  (lines 191–195)

```
def _prompt_skill_index(skills: SkillRegistry, enabled: bool) -> tuple[tuple[str, str], ...]
```

**Purpose**: This decides what skill list should appear in the system prompt. When member skills are enabled, it uses a fold-aware prompt index; otherwise it shows only the normal deployed skill index.

**Data flow**: It receives the skill registry and a boolean setting. It turns that registry into a tuple of skill names and descriptions, choosing either the richer prompt index or the registry’s basic index.

**Call relations**: The turn setup calls this while building prompts for both main agents and subagents, so the model sees the right catalog of skills for the current configuration.

*Call graph*: calls 1 internal fn (index); called by 1 (_run_turn); 1 external calls (prompt_index).


##### `_fire_shadow_selection`  (lines 201–208)

```
def _fire_shadow_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: This starts a background comparison of two skill-selection methods without making the turn wait. It is used to gather evidence about skill search quality while keeping user-facing latency unchanged.

**Data flow**: It receives the search index, embedding client, turn, and skill cards. It creates an asynchronous task and stores a reference so the task is not garbage-collected before it finishes.

**Call relations**: The main turn setup calls it when member skills exist but do not all fit in the prompt. It hands the actual work to _shadow_skill_selection and then steps aside.

*Call graph*: calls 1 internal fn (_shadow_skill_selection); called by 1 (_run_turn); 1 external calls (create_task).


##### `_shadow_skill_selection`  (lines 211–237)

```
async def _shadow_skill_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: This compares keyword-style skill selection with vector search, then logs what each method would have chosen. It is best-effort: failures are recorded but never allowed to affect the turn.

**Data flow**: It takes a turn and skill cards, shortens the inbound text, creates an embedding, searches the index, and compares those hits with lexical top-k selection. The output is a log entry, not a value returned to the caller.

**Call relations**: It runs only as the background task launched by _fire_shadow_selection. It calls the embedding and index services and records the comparison through logging.

*Call graph*: calls 2 internal fn (embed, vector); called by 1 (_fire_shadow_selection); 3 external calls (timeout, log, select_top_k).


##### `with_implied_grants`  (lines 247–250)

```
def with_implied_grants(names: set[str]) -> set[str]
```

**Purpose**: This expands a set of allowed tool or action names with companion permissions that are required to make them useful. For example, a skill loader also needs the related skill search action.

**Data flow**: It receives a mutable set of names. For each current name, it looks up any implied companion names and adds them to the same set, then returns the expanded set.

**Call relations**: Tool and action selection for both agents and subagents call this before filtering the live registry. It keeps allowlists from accidentally granting a tool without the helper it depends on.

*Call graph*: called by 4 (_agent_actions, _agent_tools, _subagent_actions, _subagent_tools).


##### `_agent_actions`  (lines 253–268)

```
def _agent_actions(actions: Mapping[str, Mapping[str, BoundAction]], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> frozenset[str]
```

**Purpose**: This computes which canonical object actions the main agent is allowed to use during a turn. A canonical action id is the stable internal name for an action, even if tools expose it in different ways.

**Data flow**: It reads the action registry, the agent’s optional allowlist, the admission source, and the speaker. It returns a frozen set of canonical action ids that are actually registered and permitted for this turn.

**Call relations**: The main turn setup in _run_turn uses this beside _agent_tools. The result later tells the engine which object actions are granted and whether extra action-discovery tools must ride along.

*Call graph*: calls 1 internal fn (with_implied_grants); called by 1 (_run_turn).


##### `_subagent_actions`  (lines 271–289)

```
def _subagent_actions(actions: Mapping[str, Mapping[str, BoundAction]], profile: SubagentProfile, grants: frozenset[str]) -> frozenset[str]
```

**Purpose**: This computes which canonical object actions a subagent profile may use. It respects the profile’s own tool names and whether the subagent is isolated from broader grants.

**Data flow**: It receives the action registry, a subagent profile, and cross-extension grants. It combines the profile’s allowed names with any non-isolated grants, expands implied grants, and returns the matching canonical action ids.

**Call relations**: When _run_turn is preparing a subagent turn, it calls this to mirror the subagent tool selection and pass a clear set of granted actions into the engine.

*Call graph*: calls 1 internal fn (with_implied_grants); called by 1 (_run_turn).


##### `_with_action_verbs`  (lines 292–313)

```
def _with_action_verbs(selected: tuple[ToolDef, ...], all_tools: tuple[ToolDef, ...], granted_actions: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: This adds the basic object-action dispatcher and read tools when a turn has at least one granted object action. Without these helper tools, the model might technically have an action but no way to discover or invoke it correctly.

**Data flow**: It receives selected tools, all available tools, and granted action ids. If no actions are granted, it removes the generic object action dispatcher; if actions are granted, it adds the dispatcher and read helpers when available.

**Call relations**: _run_turn calls this after selecting agent or subagent tools. It adjusts the final ToolRegistry so action permissions and action-discovery tools stay in sync.

*Call graph*: called by 1 (_run_turn).


##### `_agent_tools`  (lines 316–340)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> tuple[ToolDef, ...]
```

**Purpose**: This chooses the actual callable tools available to the main agent for a turn. It prevents profile-only tools from leaking to general agents unless an allowlist explicitly names them.

**Data flow**: It receives all live tools, the agent’s optional allowlist, the admission source, and the speaker. It returns only the tools allowed under those rules, after adding implied grant names when an allowlist is present.

**Call relations**: _run_turn uses it while building the main agent’s ToolRegistry. Its output is then refined by _with_action_verbs before being given to the engine.

*Call graph*: calls 1 internal fn (with_implied_grants); called by 1 (_run_turn).


##### `_resolve_profile`  (lines 343–358)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: This looks up the subagent profile requested by a turn. If the profile no longer exists, it logs useful details before letting the error continue.

**Data flow**: It receives the subagent registry, turn id, and requested profile name. It returns the matching profile, or logs the requested and registered profile names before raising the lookup error.

**Call relations**: _run_turn calls this when a turn is a subagent turn. It depends on the SubagentRegistry and gives the rest of setup the profile that defines prompt, model, tools, and output contract.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 361–373)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: This chooses the concrete tools available to a subagent. It follows the subagent profile’s tool list and, unless isolated, includes shared grants and default subagent tools.

**Data flow**: It receives all tools, a profile, and grant names. It builds the allowed name set, expands implied grants, filters the live tools, and returns the selected tool definitions.

**Call relations**: _run_turn calls this while preparing a subagent ToolRegistry, then passes the result through _with_action_verbs so object actions have their helper tools.

*Call graph*: calls 1 internal fn (with_implied_grants); called by 1 (_run_turn).


##### `_apply_provisions`  (lines 379–388)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: This makes sure a workspace has the default agents shipped by active extensions. It runs at most once per workspace in the current process and is safe to repeat at the data level.

**Data flow**: It receives the runtime and workspace id. If this process has already provisioned that workspace, it does nothing; otherwise it applies extension-provided agent provisioning and remembers the workspace.

**Call relations**: _execute_turn calls it near the start of every workspace’s first turn in a process. This gives newly used workspaces the agents they should have before the turn runs.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `init_runtime`  (lines 430–441)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: This installs the process-wide Runtime object that turn execution depends on. It also seeds sandbox carriers with bundled system skills so sandboxes can start with the built-in skill package.

**Data flow**: It receives a Runtime containing configuration, storage, registries, model access, sandboxes, and more. It refuses to run twice, builds a system skill bundle, seeds compatible sandbox carriers, and stores the runtime globally.

**Call relations**: The server setup calls this before any queued turn runs. Later, _execute_turn reads the installed runtime and fails fast if it was never initialized.

*Call graph*: calls 1 internal fn (from_skills).


##### `reset_runtime`  (lines 444–449)

```
def reset_runtime() -> None
```

**Purpose**: This clears the process-wide Runtime so tests can install a fresh one. Production serving normally initializes once and does not use this reset path.

**Data flow**: It takes no input. It changes the module-level runtime reference from the current Runtime back to nothing.

**Call relations**: It is a test seam around init_runtime’s single-initialization guard. It does not participate in normal turn processing.


##### `_execute_turn`  (lines 452–504)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: This is the outer body of a queued turn workflow. It binds the workspace, opens observability context, runs the turn, catches setup-level failures, and finally tries to deliver child results to a parent.

**Data flow**: It receives workspace and turn ids as strings from the workflow queue. It loads basic turn metadata, applies provisions, runs _run_turn, writes a failed terminal result if an exception escapes, delivers to a parent if needed, and returns a status string.

**Call relations**: turn_workflow calls this as the DBOS workflow body. It delegates the main work to _run_turn, uses _commit_failed_terminal as the safety net, and always follows with _deliver_to_parent.

*Call graph*: calls 4 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _run_turn); called by 1 (turn_workflow); 6 external calls (select, agent, workspace_tx, turn_span, ws, UUID).


##### `_deliver_to_parent`  (lines 507–539)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: This delivers a finished child or subagent turn’s terminal result back to the parent conversation. It avoids turning delivery problems into a false failure of the child turn itself.

**Data flow**: It reads the durable turn row from the database. If the turn has no parent or no terminal result, it does nothing; otherwise it asks SubagentResult to deliver the saved result, logging and deferring if delivery fails.

**Call relations**: _execute_turn calls it after every turn attempt. It uses the runtime’s invoker and subagent registry to hand the child result back to the parent-side machinery.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_enqueue_handoff`  (lines 542–577)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: This enqueues another turn when claiming the current turn reveals a handoff is needed. The queue partition key is the conversation id, which keeps conversation work ordered.

**Data flow**: It receives the DBOS client and ids for workspace, turn, conversation, and workflow. It asks DBOS to enqueue the workflow; if enqueueing is cancelled or fails, it clears the turn’s dispatch marker so another sweep can try again.

**Call relations**: _run_turn calls this after claiming a turn if the claim operation returns a handoff. It logs deferred enqueue failures instead of breaking the current turn setup.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 580–922)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: This is the main assembler and driver for a single turn. It claims the turn, loads all needed context, chooses model, tools, skills, billing, sandbox, and subagents, then runs the TurnEngine.

**Data flow**: It starts with a turn id and the already installed Runtime. It claims the turn, loads database records, builds prompts and registries, freezes billing and key decisions, prepares sandbox authorization, constructs TurnEngine, runs either normal or intent mode, and returns the final status such as completed, failed, parked, or superseded.

**Call relations**: _execute_turn calls this inside the workspace and tracing context. This function is the hub that calls most helpers in the file, then hands the fully prepared environment to TurnEngine.run or TurnEngine.run_intent.

*Call graph*: calls 17 internal fn (_agent_actions, _agent_tools, _commit_failed_terminal, _enqueue_handoff, _fire_shadow_selection, _frozen_billing_identity, _frozen_byok, _load_turn, _member_skill_block, _member_skill_turn (+7 more)); called by 1 (_execute_turn); 35 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 619–623)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: This small inner helper creates an authorized subagent interface for a particular acting member. It is used when tools need to spawn or interact with subagents under a member’s authority.

**Data flow**: It receives an acting member id or none. It asks the Subagents object to authorize that actor, then returns both the spawn function and the authorized Subagents wrapper.

**Call relations**: _run_turn defines it while preparing the engine and passes it into TurnEngine. Later engine code can call it when a tool or model action needs member-scoped subagent access.


##### `_commit_failed_terminal`  (lines 925–986)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: This is the failure backstop for errors that happen before or outside the engine’s own failure recording. It keeps retrying until the turn has a failed terminal result and waiting clients are notified.

**Data flow**: It receives the hub, turn id, and exception. It builds a failed TerminalFrame, tries to update queued or running turns to failed, emits metrics and logs if it made the transition, publishes the terminal event, and retries with backoff if even that reporting path fails.

**Call relations**: Both _execute_turn and _run_turn call this when exceptions escape their normal paths. It publishes through the Hub so listeners learn that the turn is finished even after setup failures.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 10 external calls (__init__, __init__, sleep, update, workspace_tx, emit_metric, formatted_stack, log, log_error, turn_profile).


##### `turn_workflow`  (lines 990–991)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: This is the DBOS workflow entry for running a turn from the durable queue. It is the named workflow that DBOS can enqueue, resume, and replay.

**Data flow**: It receives workspace and turn ids as strings from DBOS. It simply forwards them to _execute_turn and returns that status result.

**Call relations**: The TURN_QUEUE and enqueue code refer to this workflow name. Its only job is to connect DBOS workflow execution to the file’s real outer runner, _execute_turn.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 994–1081)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: This loads the full turn record, its agent settings, and the conversation audience from the database. It converts raw database fields into typed objects the rest of the turn runner can trust.

**Data flow**: It receives a turn id. It queries the turn, agent, and conversation tables, validates nested fields like context and terminal frames, builds a Turn object, an Agent object, and an Audience, then returns all three.

**Call relations**: _run_turn calls this after claiming a turn, and also when a claim shows the turn was superseded and transcript repair needs the durable turn data.

*Call graph*: called by 1 (_run_turn); 8 external calls (__init__, __init__, model_validate, model_validate, model_validate, select, workspace_tx, parse_audience).


##### `_run_lineage`  (lines 1084–1116)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: This finds where live activity from a spawned turn should be published. For child turns, it traces back to the root turn so user surfaces can follow one combined activity stream.

**Data flow**: It receives a Turn. If the turn has no parent, it returns nothing; otherwise it walks parent links in the database to find the root, determines a profile label, and returns a RunLineage object.

**Call relations**: _run_turn calls it before constructing the engine. The resulting lineage is passed into TurnEngine so spawned or subagent activity appears under the right parent stream.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 1119–1131)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: This finds when the previous turn in the same conversation ended. That timestamp helps the engine understand timing between turns.

**Data flow**: It receives a Turn. If this is the first turn, it returns nothing; otherwise it reads the previous sequence number’s updated time from the database and ensures the result has timezone information.

**Call relations**: _run_turn calls it for non-intent turns and passes the timestamp into TurnEngine as context.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_frozen_billing_identity`  (lines 1134–1155)

```
async def _frozen_billing_identity(turn_id: UUID, candidate: _BillingIdentity) -> _BillingIdentity
```

**Purpose**: This freezes the billing model and prices for one workflow attempt. It prevents crash recovery from replaying the same attempt under different pricing information.

**Data flow**: It receives a turn id and a candidate billing identity. It locks and reads the turn’s stored billing identity; if the same attempt is already stored, it returns that, otherwise it writes the candidate and returns it.

**Call relations**: _run_turn calls it after resolving the model and current price. The returned billing identity is then used to build the Pricing object passed into the engine.

*Call graph*: called by 1 (_run_turn); 4 external calls (model_dump, select, update, workspace_tx).


##### `_frozen_byok`  (lines 1158–1207)

```
async def _frozen_byok(workspace_id: UUID, turn_id: UUID, key_slot: str | None, attempt: str) -> bool
```

**Purpose**: This decides, once per workflow attempt, whether the workspace is using its own model provider key. BYOK means “bring your own key,” where the workspace pays the provider directly rather than using the platform key.

**Data flow**: It receives workspace id, turn id, model key slot, and attempt id. It reads any stored decision for that attempt; if none exists, it checks key ownership, writes the decision for the attempt, rereads the settled value, and returns the final boolean.

**Call relations**: _run_turn calls it before constructing the engine. The engine uses the result for billing behavior, and the freeze protects crash recovery and parked-turn resumes from inconsistent charges.

*Call graph*: called by 1 (_run_turn); 5 external calls (or_, select, update, workspace_owns_the_key, workspace_tx).


##### `_open_sandbox`  (lines 1210–1273)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: This opens or attaches to the sandbox container where a turn can run commands and use files. It supplies a signed run token and only derives credentials when the sandbox is actually needed.

**Data flow**: It receives sandbox services, token codec, turn, grants, connector command-line credentials, credential store, credential slots, and a cache setting. It builds environment variables for conversation id, tool bridge, git proxy behavior, grants, and keyed providers, then opens the conversation’s sandbox session.

**Call relations**: _run_turn passes this as the lazy opener for _LateSandbox. That means the sandbox is not opened during setup unless the engine or tools actually need it.

*Call graph*: calls 2 internal fn (open, encode); 7 external calls (__init__, span, cache_git_config, _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env).


##### `SandboxAuthorizer.authorize`  (lines 1284–1296)

```
async def authorize(self, acting_member_id: UUID | None) -> Sandbox
```

**Purpose**: This re-authorizes an existing sandbox for a specific acting member. It gives sandbox commands a fresh run token and member-scoped connector grant environment.

**Data flow**: It receives an acting member id or none. It encodes a run token containing workspace, turn, and acting member, builds grant-related environment variables, and returns a sandbox wrapper authorized with those values.

**Call relations**: _run_turn creates a SandboxAuthorizer and passes its authorize method to the engine for normal turns. When the engine needs sandbox access on behalf of a member, this method updates the authorization without reopening the whole sandbox.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### `core/src/ufo/loop/engine.py`

`orchestration` · `turn execution`

A “turn” is one unit of work for an agent: a user asks something, the agent may think, call tools, receive more user messages while running, and eventually answer or stop. This file makes that whole journey reliable. Without it, a crash could call the model twice, run a tool twice, lose a user message, forget to bill tokens, or publish an answer that ignored something the user sent mid-run.

The main class, TurnEngine, acts like an air-traffic controller. It first claims the turn in the database so only one worker owns it. It loads the saved conversation, adds context such as message time and sender, and then enters model rounds. In each round it checks seats and spending limits, compacts old context if needed, streams model text to live listeners, dispatches tool calls, and feeds tool results back to the model. New incoming messages are drained between rounds, never in the middle of a tool exchange.

Several operations are wrapped as DBOS steps, meaning their outputs are recorded and replayed after a crash instead of being performed again. That is why model calls, tool calls, and arrival drains are written carefully as replay-safe boundaries. The file also handles special exits: parked turns when spending is blocked, cancelled turns, failed turns, subagent “finish” contracts, transcript repair, live activity updates, image offloading, and cost reporting.

#### Function details

##### `_claim_turn`  (lines 238–285)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued or parked turn for one workflow attempt, or reclaims a turn already running under the same attempt after crash recovery. This prevents two workers from actively running the same turn.

**Data flow**: It receives a turn id and attempt id, reads the current turn row and locks its conversation, then updates the row to running only if the claim is allowed. It returns whether the claim was fresh, adopted from the same attempt, or not won at all.

**Call relations**: TurnEngine._mark_running uses this at the start of normal and intent turns. _claim_turn_with_handoff also uses it before looking for the next queued turn to hand off.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 296–356)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[str | None, _TurnHandoff | None]
```

**Purpose**: Claims the current turn and, if possible, marks the next queued turn in the same conversation as ready to dispatch. This helps keep a conversation moving without racing two turns at once.

**Data flow**: It takes a turn id and attempt id, tries to claim the turn, then checks the conversation for the next queued turn. If it finds one not already enqueued, it stamps it and returns a small handoff record with the ids needed to run it.

**Call relations**: It builds on _claim_turn. The returned _TurnHandoff is used by higher-level queue logic outside this file to continue dispatching work.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `_activity_goal`  (lines 425–426)

```
def _activity_goal(requesters: Mapping[UUID, ActiveMessage]) -> str
```

**Purpose**: Turns the currently active requester messages into a plain goal string for activity summaries. It gives the activity summarizer the human request the tool is serving.

**Data flow**: It receives a mapping of message ids to active messages, extracts the member-facing text from each message, and joins them into one string.

**Call relations**: TurnEngine._model_round and TurnEngine.run_intent call it before starting activity generation for tool calls.

*Call graph*: called by 2 (_model_round, run_intent); 1 external calls (member_message_text).


##### `_RoundInput.__repr__`  (lines 438–443)

```
def __repr__(self) -> str
```

**Purpose**: Provides a short debug-friendly summary of a model round request without dumping the full prompt. This makes logs readable and avoids exposing huge message bodies.

**Data flow**: It reads the round input fields, counts messages and system prompt characters, and returns a compact string.

**Call relations**: It is used implicitly when _RoundInput is logged or displayed, especially around model-round DBOS steps.


##### `EffectiveCall.semantic_call`  (lines 463–474)

```
def semantic_call(self) -> ToolUseBlock
```

**Purpose**: Returns the tool call under the meaningful tool identity that downstream code should reason about. This matters for object actions, where the wire tool may be a generic dispatcher but the real action has its own name.

**Data flow**: It reads the original call and resolved call id. If they match, it returns the original call; otherwise it rewrites the name and input to the validated object action form.

**Call relations**: TurnEngine._model_round uses this shape for activity and final-act parsing so later logic sees the action that actually ran.


##### `EffectiveCall.meter_dimensions`  (lines 476–486)

```
def meter_dimensions(self) -> dict[str, str]
```

**Purpose**: Builds the labels used for tool telemetry, such as which real action ran and where it came from. This keeps metrics grouped by meaningful action rather than by transport wrapper.

**Data flow**: It reads the resolved tool definition and extension context. It returns a small dictionary of metric labels, with extra bound-action details when applicable.

**Call relations**: TurnEngine._bind_or_error, TurnEngine._dispatch_step, and _meter_dispatch use these labels when recording tool outcomes.


##### `EffectiveCall.__repr__`  (lines 488–489)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact representation of a resolved call for logs and debugging. It shows the semantic call name and model-provided call id.

**Data flow**: It reads the call id fields and returns a short string.

**Call relations**: Used implicitly by Python debugging, logging, and error messages around resolved tool calls.


##### `_BoundToolCall.call`  (lines 498–499)

```
def call(self) -> ToolUseBlock
```

**Purpose**: Gives easy access to the original tool call inside a bound dispatch item. It hides one layer of wrapping from dispatch code.

**Data flow**: It reads the EffectiveCall stored in the bound item and returns its ToolUseBlock.

**Call relations**: TurnEngine._dispatch_step and related dispatch helpers use this property so bound and rejected calls can be treated similarly.


##### `_BoundToolCall.__repr__`  (lines 501–502)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug string for a tool call that has been bound to a context. This helps logs show which tool call is about to run.

**Data flow**: It reads the tool name and call id and formats them into a short string.

**Call relations**: Used implicitly when bound calls appear in logs, traces, or errors.


##### `_RejectedToolCall.__repr__`  (lines 513–517)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug string for a tool call rejected before real dispatch. It shows which tool failed, why category it fell into, and the error class.

**Data flow**: It reads the rejected call and stored error metadata, then returns a short string.

**Call relations**: Used implicitly when rejected calls are logged or inspected during dispatch.


##### `ModelStreamError.__init__`  (lines 576–577)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Stores a model streaming failure together with any partial text already received. This lets the caller bill usage and sometimes salvage cut-off output.

**Data flow**: It receives the provider error class, message, and optional partial output, and stores them in the exception arguments.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after a model-round step reports an error instead of raising directly.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 579–581)

```
def __str__(self) -> str
```

**Purpose**: Formats a model streaming error as a readable provider-class-plus-message string. It deliberately does not include partial output.

**Data flow**: It reads the stored error class and message and returns one combined string.

**Call relations**: Used whenever the exception is logged or converted to terminal error text.


##### `ModelStreamError.model_error_class`  (lines 584–586)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original model provider’s error class. Callers use this to tell truncation or context overflow apart from other failures.

**Data flow**: It reads the first stored exception argument and returns it.

**Call relations**: TurnEngine._model_round checks it when deciding whether a truncated response can be recovered.


##### `ModelStreamError.partial_output`  (lines 589–591)

```
def partial_output(self) -> str
```

**Purpose**: Returns text and raw tool-call fragments received before the model stream failed. This can be saved so the model can continue from what it already produced.

**Data flow**: It reads the third stored exception argument and returns it.

**Call relations**: TurnEngine._model_round uses it during truncation recovery and offloads it to a sandbox file when possible.


##### `ModelStreamError.model_error_message`  (lines 594–596)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original model provider’s error message. This preserves useful provider detail for terminal frames and overflow detection.

**Data flow**: It reads the second stored exception argument and returns it.

**Call relations**: TurnEngine._commit_once uses it when writing a failed terminal frame for a model error.


##### `TurnParked.__init__`  (lines 603–605)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates the exception used when a turn must pause because a seat, balance, or spending cap blocks further work. The turn is not failed; it is held for later resume.

**Data flow**: It receives the user-facing reason, stores it on the exception, and makes it available as message.

**Call relations**: TurnEngine._enforce_spend raises it, and TurnEngine.run catches it to park the turn durably.

*Call graph*: called by 1 (_enforce_spend).


##### `_intent_admits`  (lines 611–616)

```
def _intent_admits(tool: ToolDef) -> bool
```

**Purpose**: Decides whether a prepared member intent is allowed to call a tool. It limits this special lane to object mutations and tools/actions that declare a presentation.

**Data flow**: It receives a tool definition and checks its name and presentation metadata. It returns true when the intent lane may reach it.

**Call relations**: TurnEngine.run_intent uses this after resolving the intent call and before binding or dispatching it.

*Call graph*: called by 1 (run_intent).


##### `_dispatch_segments`  (lines 625–646)

```
def _dispatch_segments(resolved: tuple[_Resolution, ...]) -> Iterator[tuple[_Resolution, ...]]
```

**Purpose**: Splits a model’s tool calls into groups that can safely run in parallel while preserving the order of calls that must act as barriers. It is like grouping errands: independent ones can happen together, but a checkout step must happen in sequence.

**Data flow**: It receives resolved calls, groups consecutive parallel-safe calls up to a limit, and yields one segment at a time.

**Call relations**: TurnEngine._model_round uses these segments before binding and dispatching tools.

*Call graph*: called by 1 (_model_round).


##### `_parse_args`  (lines 649–651)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed pieces of a tool-call JSON argument into a Python dictionary. Empty arguments become an empty dictionary.

**Data flow**: It joins the string fragments, checks whether anything meaningful is present, and parses JSON when needed.

**Call relations**: TurnEngine._stream_once uses it when converting model tool-call deltas into ToolUseBlock objects.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 654–672)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small context block placed before a user message so the model knows when it was sent, who sent it, and what surface information came with it. This gives the model a clock and source details it otherwise lacks.

**Data flow**: It receives a message id, optional turn context, and admission time, formats the time in the sender’s timezone when known, and returns a text tag.

**Call relations**: TranscriptRepair.load_messages uses it for the founding message, and TurnEngine._render_arrival uses it for later drained messages.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 675–680)

```
def _bounded(content: str) -> str
```

**Purpose**: Limits tool-result text to the maximum size allowed back into the model context. This protects later prompts from being flooded by one huge result.

**Data flow**: It receives text, returns it unchanged if small enough, or returns a prefix plus a truncation notice.

**Call relations**: TurnEngine._dispatch_step uses it for large errors, and TurnEngine._model_round uses it for finish-tool validation errors.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_meter_dispatch`  (lines 683–717)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str, semantic: Mapping[str, str] | None=None) -> None
```

**Purpose**: Records metrics for one tool dispatch: how long it took, what ran, and how it ended. These metrics let operators see slow tools, invalid calls, hook failures, and handler errors.

**Data flow**: It receives the tool registry, call, start time, outcome, error class, profile, and semantic labels, then emits count and timing metrics.

**Call relations**: TurnEngine._bind_or_error calls it when binding fails unusually, and TurnEngine._dispatch_step calls it in its final cleanup for every dispatch body.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 720–763)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedRef, ...]]
```

**Purpose**: Finds which skill cards are already present in the current message window so they do not need to be loaded again. It reads reliable tool-call records rather than trusting prose inside skill text.

**Data flow**: It scans messages for completed load-skill calls and matching results, asks the skill registry for each closure, and yields those closures.

**Call relations**: TurnEngine._reseed_loaded_skills uses this to refresh the compaction skill tracker.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 766–788)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured payload from a successful final-act tool call when that tool was the last call in the round. This is used for things like a final question that should only count if it ended the round.

**Data flow**: It receives tool calls, tool results, a tool name, and a payload model. It checks the last call/result, parses the JSON payload from the result text, validates it, and returns it or nothing.

**Call relations**: _round_acts calls it for final-act types whose rule says only the last call counts.

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_pending_act`  (lines 791–819)

```
def _pending_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts the latest successful structured handoff of a given tool type anywhere in a round. This is used for requests that remain owed until a member answers, such as credentials or connection approval.

**Data flow**: It receives calls, results, a tool name, and a payload model. It walks calls backward, finds the matching successful result, parses and validates its JSON payload, and returns it or nothing.

**Call relations**: _round_acts calls it for final-act types whose rule says the request remains pending even if other work happened afterward.

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_round_acts`  (lines 822–853)

```
def _round_acts(resolved: tuple[_Resolution, ...], results: tuple[ToolResultBlock, ...]) -> dict[str, BaseModel]
```

**Purpose**: Collects the structured open requests produced by a tool-calling round, such as asking the user a question or requesting credentials. It normalizes object-action calls to their real semantic identity first.

**Data flow**: It receives resolved calls and result blocks, chooses the right parser for each final-act declaration, and returns a dictionary keyed by terminal-frame field name.

**Call relations**: TurnEngine._model_round uses it after tool dispatch, and TurnEngine.run_intent uses it after a direct intent tool call.

*Call graph*: calls 2 internal fn (_final_act, _pending_act); called by 2 (_model_round, run_intent).


##### `_act`  (lines 856–860)

```
def _act(acts: dict[str, BaseModel], frame_field: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Fetches one typed act payload from the dictionary produced by _round_acts. It safely returns nothing if the payload is absent or not the expected type.

**Data flow**: It receives the acts dictionary, a field name, and a model type. It reads the value and returns it only when it matches the requested model.

**Call relations**: TurnEngine._model_round and TurnEngine.run_intent use it to pull question, credential, and connection requests into terminal frames.

*Call graph*: called by 2 (_model_round, run_intent).


##### `_created_refs`  (lines 863–895)

```
def _created_refs(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> tuple[ObjectRef, ...]
```

**Purpose**: Finds object references created by successful object_apply calls in a round. This lets the terminal frame name what the turn created.

**Data flow**: It receives tool calls and results, matches object_apply results, parses their JSON, and returns valid ObjectRef records for creations only.

**Call relations**: TurnEngine._fold_created uses it during normal rounds, and TurnEngine.run_intent uses it after direct object intents.

*Call graph*: called by 2 (_fold_created, run_intent); 2 external calls (__init__, loads).


##### `_total_usage`  (lines 898–906)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many token-usage events into one total. This gives billing, cost display, and metrics a single usage number for the turn so far.

**Data flow**: It receives a list of Usage records, sums each token category, and returns a new Usage record with totals.

**Call relations**: Billing, cost publishing, spend checks, parking, cancellation billing, committing, and model metrics all call it.

*Call graph*: called by 6 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost, _stream_once); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 921–945)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal frame for a turn that already finished but whose client may not have seen the final event. This repairs the live wait without rerunning the turn.

**Data flow**: It reads the turn’s terminal from the database. If present, it validates the frame, ensures inbound messages are preserved in the transcript, publishes the terminal, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed calls it when this execution did not win the running claim.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 947–954)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed conversation transcript including the assistant’s final answer. This is the durable record future turns will read.

**Data flow**: It receives prior messages, answer text, system prompt, and injected context, appends an assistant answer message, and hands the full conversation to write_conversation.

**Call relations**: TurnEngine._persist_transcript delegates to this after a successful terminal path.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 956–975)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Writes only the user-side messages when a turn ends without a complete assistant answer. This prevents member messages from disappearing after failures, cancellations, or denials.

**Data flow**: It loads the founding inbound or builds a denial message, appends absorbed arrival messages, and writes that conversation state.

**Call relations**: TranscriptRepair.resolve calls it during repair, and TurnEngine._persist_inbound delegates to it on non-complete exits.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 977–986)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the prior transcript and appends this turn’s founding user message. For normal member turns, it prefixes the message with the context tag.

**Data flow**: It reads previous messages, optionally wraps the inbound with _context_tag, creates a user message, and returns the combined tuple.

**Call relations**: TurnEngine._load_messages delegates to this, and persist_inbound uses it when preserving inbound text.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 988–994)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the conversation transcript before this turn, while avoiding reading this turn’s own previous write during replay. That prevents duplicated context.

**Data flow**: It reads the transcript store, compares its sequence number with the current turn, and returns either stored messages or an empty tuple.

**Call relations**: TranscriptRepair.load_messages and persist_inbound call it when rebuilding the message window.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 996–1016)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a Conversation record to the transcript store with a few retries. This makes transcript persistence more tolerant of temporary storage failures.

**Data flow**: It receives messages and optional system/injected text, builds a Conversation, tries to write it, logs failures, waits briefly, and retries.

**Call relations**: persist_transcript and persist_inbound both use it as the final write path.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 1050–1067)

```
def exited(self, status: str) -> None
```

**Purpose**: Records how long this execution ran and how many model rounds it entered. It reports the first exit only, so one execution is not counted twice.

**Data flow**: It receives an exit status, checks whether it already ended, computes elapsed time, and emits timing and round metrics.

**Call relations**: TurnEngine._commit calls it after terminal commit or readback; TurnEngine.run and run_intent also trigger it on cancellation and parking paths.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 1160–1171)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the engine was wired consistently after construction. It catches mismatched audiences and illegal subagent tool names early.

**Data flow**: It reads tool extension contexts, hooks, audience, output model, and tool registry. It raises a ValueError if the setup would route data to the wrong audience or conflict with the reserved finish tool.

**Call relations**: Called automatically when a TurnEngine dataclass instance is created.


##### `TurnEngine.__repr__`  (lines 1173–1177)

```
def __repr__(self) -> str
```

**Purpose**: Provides a compact text form of the engine for logs and debugging. It identifies the turn, agent, and profile.

**Data flow**: It reads the turn id, agent id, and computed profile and returns a formatted string.

**Call relations**: Used implicitly whenever a TurnEngine is represented in logs or debugging tools.


##### `TurnEngine.profile`  (lines 1180–1183)

```
def profile(self) -> str
```

**Purpose**: Computes the telemetry profile for this turn, such as main, agent child, or a named subagent profile. Metrics use this to separate different kinds of work.

**Data flow**: It reads whether the turn is spawned and its subagent profile, then delegates to turn_profile.

**Call relations**: Many methods use this property when logging or emitting metrics.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 1185–1392)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal model-driven turn from claim to terminal result. It is the main body that loads context, calls the model, runs tools, handles arrivals, persists transcript, bills usage, and publishes live/final frames.

**Data flow**: It starts with the turn, agent, tools, model, sandbox, and services already attached. It claims the turn, prepares ToolContext and messages, loops through model rounds, commits a terminal or parks/fails/cancels, and changes database rows, transcripts, billing records, sandbox state, and hub events.

**Call relations**: This is the top-level turn workflow body. It calls most helpers in this file, including _mark_running, _model_round, _commit, _park, _persist_transcript, _publish_terminal, and cleanup paths.

*Call graph*: calls 17 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _publish, _publish_run (+7 more)); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, escape, monotonic, emit_metric (+2 more)).


##### `TurnEngine.run.rank_find`  (lines 1204–1227)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Runs the host-side model call used by the browser find tool to rank page elements. Its usage is charged to the current turn.

**Data flow**: It receives a system prompt and user prompt, builds a small ModelRequest, streams text deltas into a result string, and records usage events both globally and for the current tool dispatch.

**Call relations**: TurnEngine.run places this function into ToolContext so browser tools can call it during dispatch.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine.run_intent`  (lines 1394–1523)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a direct intent turn without asking the model. This is used for prepared UI actions or sandbox bridge calls that already name exactly one tool.

**Data flow**: It claims the turn, parses the inbound intent into one ToolUseBlock, resolves and checks it, binds requester authority, dispatches the tool through the same safe DBOS path, commits done or failed, writes transcript, and publishes the terminal.

**Call relations**: It shares many lower-level paths with model turns, including _resolve_call, _bind_or_error, _dispatch_step_recovering, _commit, and _publish_terminal.

*Call graph*: calls 17 internal fn (_bind_or_error, _commit, _dispatch_step_recovering, _load_messages, _mark_running, _persist_transcript, _publish_terminal, _rejected, _resolve_call, _resolve_unclaimed (+7 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, model_validate_json, monotonic, emit_metric (+1 more)).


##### `TurnEngine._scheduled_system`  (lines 1525–1559)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. If memory search fails or finds nothing, the turn continues with the original prompt.

**Data flow**: It receives the current system prompt, searches memory using the turn’s inbound text and audience subjects, formats matches as escaped recalled-memory bullets, and returns the augmented or original prompt.

**Call relations**: TurnEngine.run calls it only for scheduled admissions before user prompt hooks and model rounds begin.

*Call graph*: called by 1 (run); 5 external calls (__init__, timeout, escape, log, audience_subjects).


##### `TurnEngine._mark_running`  (lines 1561–1569)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn as owned by the current workflow attempt. It is the engine’s local wrapper around the database claim function.

**Data flow**: It reads the turn id and attempt id from the engine, calls _claim_turn, and returns true if the claim succeeded.

**Call relations**: TurnEngine.run and run_intent call it before doing real work; if it fails, they go to _resolve_unclaimed.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 1571–1572)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a TranscriptRepair helper for this turn. This keeps transcript repair logic separate from the main engine flow.

**Data flow**: It reads the engine’s turn, transcript, and hub, and returns a TranscriptRepair object.

**Call relations**: _load_messages, _persist_transcript, _persist_inbound, and _resolve_unclaimed all use this helper.

*Call graph*: called by 5 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed, run); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 1574–1576)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the message window for the turn while adding observability around the operation. It is the normal entry point for transcript loading inside TurnEngine.

**Data flow**: It opens a tracing span, creates a TranscriptRepair helper, and returns its loaded messages.

**Call relations**: TurnEngine.run and run_intent call it when preparing transcript context or writing the final transcript.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 1578–1810)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the repeated model/tool loop until the agent has a final answer or exhausts its round budget. This is the heart of a normal conversational turn.

**Data flow**: It receives current ToolContext, messages, usage list, system prompt, arrival and requester trackers, meter, change paths, and created objects. Each loop absorbs arrivals, checks spend, compacts context, streams one model round, dispatches tool calls, records acts and creations, and finally returns final messages, answer text, and any open user-facing requests.

**Call relations**: TurnEngine.run calls it after setup. It calls _absorb_arrivals, _stream_recovering_overflow, _dispatch_segments, _bind_or_error, _dispatch, _round_acts, _force_final, and many supporting helpers.

*Call graph*: calls 20 internal fn (_absorb_arrivals, _bind_or_error, _dispatch, _enforce_spend, _fold_created, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills (+10 more)); called by 1 (run); 10 external calls (__init__, __init__, __init__, gather, marked_replies, emit_metric, log, span, freeform_result_contract, change_targets).


##### `TurnEngine._fold_created`  (lines 1812–1840)

```
async def _fold_created(self, created: dict[ObjectRef, None], tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> None
```

**Purpose**: Adds newly created object references from a round into the durable turn row. This preserves creations even if the turn later parks, cancels, or fails.

**Data flow**: It receives the accumulator, tool calls, and results, extracts new created refs, updates the accumulator, and writes the whole set to the database for non-terminal turns.

**Call relations**: TurnEngine._model_round calls it in a dispatch cleanup block so creations are recorded even if later dispatch handling raises.

*Call graph*: calls 1 internal fn (_created_refs); called by 1 (_model_round); 2 external calls (update, workspace_tx).


##### `TurnEngine._absorb_arrivals`  (lines 1842–1902)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Pulls queued inbound messages into the current model window between rounds. This lets the agent answer messages that arrived while it was already running.

**Data flow**: It receives current messages and tracking lists, claims arrivals through _claim_arrivals, converts each into a user message or denial marker, updates absorbed ids and requesters, logs the drain, publishes Absorbed frames for member messages, and returns the expanded message tuple.

**Call relations**: TurnEngine._model_round calls it at the start of every round.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); called by 1 (_model_round); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 1904–1956)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Delivers marked mid-turn replies to members before the whole turn is finished. It writes delivery rows and publishes live Reply frames.

**Data flow**: It receives marked reply spans and a round index, skips subagents, creates stable reply ids, inserts rows if not already present, logs new rows, and publishes each reply.

**Call relations**: TurnEngine._model_round calls it when a tool-calling round produced marked member-facing text.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_model_round); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 1958–1968)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Publishes marked reply text from a closing round back onto the live text stream. This ensures terminal-style clients still see the final answer as streaming text.

**Data flow**: It receives marked reply spans and publishes each span’s text as a TextDelta.

**Call relations**: TurnEngine._model_round and _force_final call it when the turn is closing without separate mid-turn Reply frames.

*Call graph*: calls 1 internal fn (_publish); called by 2 (_force_final, _model_round); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 1970–1994)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Runs prompt-submission hooks for one queued arrival and builds the exact text the model should see. Denied messages become safe denial text instead of exposing the original body.

**Data flow**: It receives arrival identity, body, context, speaker, and time. It fires the user_prompt_submit hook, returns a denial if blocked, or returns context-tagged content with injected context if supplied.

**Call relations**: TurnEngine._claim_arrivals calls it while claiming and memoizing arrival batches.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1997–2066)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Claims pending inbound messages for this turn as a replay-safe DBOS step. It guarantees a drained batch is not lost or consumed twice across crashes.

**Data flow**: It receives already absorbed ids, stamps eligible inbound rows with this turn id, renders each row through _render_arrival, logs the claimed batch, closes adoption replay mode, and returns Arrival records.

**Call relations**: TurnEngine._absorb_arrivals calls it each round before adding arrivals to the message window.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 7 external calls (__init__, model_validate, and_, or_, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 2068–2087)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrival rows that were stamped by this turn but not fully absorbed back to the pending queue on failed or cancelled exits. This is a best-effort safety net against message loss.

**Data flow**: It receives absorbed ids, clears consumed_turn_id for rows claimed by this turn but not in that absorbed list, and logs if the cleanup fails.

**Call relations**: TurnEngine.run calls it on failure, cancellation, or preemption paths.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 2089–2127)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, requesters: dict[UUID, ActiveMessage]) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Produces a best-effort final answer after the normal round budget is spent. Instead of failing immediately, it asks the model to answer without more tools, or forces a subagent finish.

**Data flow**: It receives messages, usage, system prompt, and requesters. It emits exhaustion metrics, checks spend, compacts context, adds a force-final prompt, streams one no-tool round or forced finish, publishes cost, and returns final messages and answer.

**Call relations**: TurnEngine._model_round calls it when the loop reaches max_rounds.

*Call graph*: calls 5 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_closing_spans, _stream_recovering_overflow); called by 1 (_model_round); 4 external calls (__init__, marked_replies, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 2129–2154)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to close with the reserved finish tool so its answer matches the required output schema. A malformed forced finish fails loudly.

**Data flow**: It receives messages, usage, and system prompt, streams one round with only finish available and required, validates the finish input against the output model, and returns JSON answer text.

**Call relations**: TurnEngine._model_round uses it for prose subagent endings, and _force_final uses it when a subagent exhausts rounds.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 2156–2219)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False, active_requests: tuple[str, ...]=()
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large. It turns recorded model-step errors into ModelStreamError for the caller.

**Data flow**: It receives messages and round options, calls _stream_once, adds usage, checks for embedded errors, and returns messages plus result. If there is a context overflow, it forces compaction, reseeds skills, retries once, and returns the compacted window.

**Call relations**: _model_round, _force_final, and _force_finish all call it as their safe model-round wrapper.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 2221–2281)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks before each model round whether the turn is still allowed to spend. It protects seats, balances, and caps while preserving already-spent work by parking instead of discarding it.

**Data flow**: It receives current usage events and active requesters, checks member seats, calculates pending cost unless using a bring-your-own-key path, checks workspace balance and spend caps, and raises TurnParked when blocked.

**Call relations**: TurnEngine._model_round and _force_final call it before more model work.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 7 external calls (__init__, __init__, __init__, applicable_caps_absent, balance_absent, workspace_tx, audience_member).


##### `TurnEngine._stream_once`  (lines 2284–2526)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Performs one actual model stream as a replay-safe DBOS step. It streams visible text live, collects tool calls, reasoning blocks, and usage, and records errors as data so usage is not lost.

**Data flow**: It receives _RoundInput, builds a ModelRequest with the right tools and cache settings, streams provider events, buffers and redacts live text, collects usage and tool-call JSON, emits metrics, and returns a StreamResult.

**Call relations**: TurnEngine._stream_recovering_overflow is its only direct caller and wraps its result with overflow recovery and error raising.

*Call graph*: calls 2 internal fn (_parse_args, _total_usage); called by 1 (_stream_recovering_overflow); 14 external calls (__init__, __init__, __init__, __init__, __init__, Event, Lock, ensure_future, now, monotonic (+4 more)).


##### `TurnEngine._stream_once.flush`  (lines 2365–2374)

```
async def flush() -> None
```

**Purpose**: Flushes buffered model text to live listeners while respecting reply redaction. It prevents tiny stream deltas from being published one character at a time.

**Data flow**: It reads the local text buffer, feeds it through ReplyRedaction, clears the buffer, resets pending byte count, and publishes visible text if any.

**Call relations**: The local pace task and the main streaming loop inside _stream_once call it during and after model streaming.

*Call graph*: 1 external calls (__init__).


##### `TurnEngine._stream_once.pace`  (lines 2376–2381)

```
async def pace() -> None
```

**Purpose**: Periodically flushes streamed model text even if the byte buffer is not full. This keeps live output feeling responsive.

**Data flow**: It waits in short intervals until stopped and calls flush whenever the interval expires.

**Call relations**: _stream_once starts it as a background task for the lifetime of one model stream.

*Call graph*: 1 external calls (wait_for).


##### `TurnEngine._publish_cost`  (lines 2528–2543)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes a live estimate of the turn’s cost and token count so surfaces can show a running cost meter. Publish failures do not affect the turn.

**Data flow**: It totals usage events, computes tokens and micro-dollar cost, wraps them in a CostTick, and sends it through _publish.

**Call relations**: _model_round, _force_final, and _force_finish call it after model rounds.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2545–2556)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the record of which skills are already loaded in the current message window. This avoids repeating skill instructions that survived compaction.

**Data flow**: It scans messages through _loaded_skill_closures and reseeds the compaction tracker with those closures plus preloaded skills.

**Call relations**: _model_round calls it around compaction, and _stream_recovering_overflow calls it after forced compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._resolve_call`  (lines 2558–2572)

```
def _resolve_call(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Turns a raw model tool call into either a resolved callable tool/action or a structured rejection. This makes later dispatch logic operate on one common identity.

**Data flow**: It receives a ToolUseBlock, routes object_action calls to _resolve_action, otherwise looks up the tool by name, and returns an EffectiveCall or _RejectedToolCall.

**Call relations**: _model_round resolves every model tool call with it; run_intent uses it for direct intent calls.

*Call graph*: calls 2 internal fn (_rejected, _resolve_action); called by 2 (_model_round, run_intent); 1 external calls (__init__).


##### `TurnEngine._resolve_action`  (lines 2574–2648)

```
def _resolve_action(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Resolves a generic object_action call into the specific bound object action it names, while enforcing shape, grants, target rules, and input validation. Invalid actions become safe tool errors rather than uncontrolled dispatches.

**Data flow**: It parses the object-action input, looks up the kind and action, checks binding requirements, agent targeting, granted actions, and input schema, then returns an EffectiveCall with validated action args or a rejection.

**Call relations**: TurnEngine._resolve_call delegates object_action calls here.

*Call graph*: calls 1 internal fn (_rejected); called by 1 (_resolve_call); 3 external calls (__init__, model_validate, replace).


##### `TurnEngine._rejected`  (lines 2650–2662)

```
def _rejected(self, call: ToolUseBlock, error: Exception, dimensions: Mapping[str, str] | None=None) -> _RejectedToolCall
```

**Purpose**: Builds a standardized rejected tool call result from an exception. This lets invalid names, bad arguments, and early policy-like failures occupy their normal place in tool-result order.

**Data flow**: It receives the call, exception, and optional metric labels, formats error text, chooses an outcome category, and returns a _RejectedToolCall.

**Call relations**: _resolve_call, _resolve_action, _bind_or_error, and run_intent use it whenever a call should become an is_error tool result.

*Call graph*: called by 4 (_bind_or_error, _resolve_action, _resolve_call, run_intent); 1 external calls (__init__).


##### `TurnEngine._bind_or_error`  (lines 2664–2699)

```
async def _bind_or_error(self, context: ToolContext, item: _Resolution, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Binds a resolved call to the right requester-specific ToolContext, or turns binding problems into rejected calls. Binding decides whose authority, sandbox, and subagents the tool runs under.

**Data flow**: It receives base context, a resolved item, and active requesters. Rejected items pass through; valid calls go through _bind_requester and become _BoundToolCall; exceptions become rejection results or terminal errors.

**Call relations**: _model_round and run_intent call it before dispatching tools.

*Call graph*: calls 3 internal fn (_bind_requester, _rejected, _meter_dispatch); called by 2 (_model_round, run_intent); 5 external calls (__init__, __init__, meter_dimensions, replace, monotonic).


##### `TurnEngine._dispatch`  (lines 2701–2707)

```
async def _dispatch(self, bound: _DispatchInput, usage_events: list[Usage] | None=None) -> ToolResultBlock
```

**Purpose**: Runs a bound or rejected tool call and converts the recorded dispatch result into the model-facing ToolResultBlock. It is the normal dispatch wrapper used by model rounds.

**Data flow**: It receives a dispatch input and optional usage list, calls _dispatch_step_recovering, then rehydrates the result through _dispatch_result.

**Call relations**: TurnEngine._model_round calls it for each bound item in a dispatch segment.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step_recovering); called by 1 (_model_round).


##### `TurnEngine._dispatch_result`  (lines 2709–2741)

```
async def _dispatch_result(self, result: DispatchResult) -> ToolResultBlock
```

**Purpose**: Converts a serialized DispatchResult back into a ToolResultBlock the model can read. If images were offloaded, it loads them from blob storage at this point.

**Data flow**: It receives a DispatchResult, either creates a text-only result or fetches image blobs and builds text/image blocks, records the result for activity labeling, and returns the ToolResultBlock.

**Call relations**: TurnEngine._dispatch calls it after _dispatch_step_recovering completes.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._dispatch_step_recovering`  (lines 2743–2753)

```
async def _dispatch_step_recovering(self, bound: _DispatchInput, usage_events: list[Usage] | None) -> DispatchResult
```

**Purpose**: Repeats dispatch steps when a previous dispatch was interrupted in a way that DBOS recorded. It keeps retrying until a non-interrupted result is accepted.

**Data flow**: It receives a dispatch input and usage list, calls _dispatch_step with an optional resume target, updates the resume target from the result, and stops when _accept_dispatch_result approves it.

**Call relations**: TurnEngine._dispatch and run_intent use it to execute tool dispatches safely.

*Call graph*: calls 2 internal fn (_accept_dispatch_result, _dispatch_step); called by 2 (_dispatch, run_intent).


##### `TurnEngine._accept_dispatch_result`  (lines 2755–2766)

```
def _accept_dispatch_result(self, result: DispatchResult, usage_events: list[Usage] | None) -> bool
```

**Purpose**: Decides whether a DispatchResult should be used or whether dispatch should continue after interruption. It also adds recorded tool-side model usage exactly when appropriate.

**Data flow**: It receives a dispatch result and optional usage list, checks whether this was a live dispatch body, merges usage for replayed recorded work, raises cancellation for live interruption, and returns whether to accept the result.

**Call relations**: _dispatch_step_recovering calls it after each _dispatch_step result.

*Call graph*: called by 1 (_dispatch_step_recovering).


##### `TurnEngine._bind_requester`  (lines 2768–2811)

```
async def _bind_requester(self, context: ToolContext, item: EffectiveCall, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Applies requester identity to a tool call and selects requester-specific sandbox and subagent controls. This enforces that tools acting for a member are tied to an active inbound message.

**Data flow**: It receives base context, an EffectiveCall, and requesters. It parses and removes requested_by when present, validates the referenced message, picks the acting member, asks provider callbacks for member-specific resources when available, and returns updated context plus cleaned call.

**Call relations**: _bind_or_error calls it for every valid resolved tool call.

*Call graph*: called by 1 (_bind_or_error); 2 external calls (replace, UUID).


##### `TurnEngine._offload`  (lines 2813–2838)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text content to the sandbox runtime output directory and returns a display path. This keeps huge tool results or salvaged model output out of the prompt while still making them available.

**Data flow**: It receives a file name and content, ensures the output directory exists, writes bytes to the sandbox, logs and returns nothing on failure, or returns the display path.

**Call relations**: _dispatch_step uses it for oversized tool output, and _model_round uses it for truncated model-output salvage.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._start_activity`  (lines 2840–2845)

```
def _start_activity(self, call: ToolUseBlock, goal: str) -> None
```

**Purpose**: Starts background generation of a short human-readable activity label for a tool call. This lets live surfaces show what the agent is doing.

**Data flow**: It receives a tool call and goal text, increments an activity sequence, starts _generate_activity as an asyncio task, and tracks the task for later cleanup.

**Call relations**: _model_round and run_intent call it just before dispatching a real bound tool call.

*Call graph*: calls 1 internal fn (_generate_activity); called by 2 (_model_round, run_intent); 1 external calls (create_task).


##### `TurnEngine._generate_activity`  (lines 2847–2859)

```
async def _generate_activity(self, call: ToolUseBlock, goal: str, sequence: int) -> None
```

**Purpose**: Creates and publishes an activity summary for a tool call in the original call order. This avoids activity messages arriving out of sequence when summaries finish at different speeds.

**Data flow**: It receives the call, goal, and sequence number, asks the activity summarizer for text, stores any label by call id, then publishes ready summaries in order.

**Call relations**: _start_activity launches it as a background task; it publishes through _publish and _publish_run.

*Call graph*: calls 2 internal fn (_publish, _publish_run); called by 1 (_start_activity); 1 external calls (__init__).


##### `TurnEngine._stop_activity`  (lines 2861–2863)

```
def _stop_activity(self) -> None
```

**Purpose**: Cancels outstanding background activity-summary tasks when the turn terminal is published. This prevents leftover tasks from running after the turn is over.

**Data flow**: It reads the tracked task set and cancels each task.

**Call relations**: _publish_terminal calls it after sending the terminal and subagent status update.

*Call graph*: called by 1 (_publish_terminal).


##### `TurnEngine._dispatch_step`  (lines 2866–3123)

```
async def _dispatch_step(self, bound: _DispatchInput, resume_target: ObjectActionTarget | None=None) -> DispatchResult
```

**Purpose**: Runs one tool call as a replay-safe DBOS step. It validates inputs, fires hooks, handles object-action targets, runs the handler, bounds or offloads output, walls untrusted content, stores images separately, records usage, and returns a serialized DispatchResult.

**Data flow**: It receives a bound or rejected dispatch item and optional resume target. Rejections become error results immediately; valid calls are validated, checked through hooks, run with an idempotency key when side-effecting, converted to text/images, passed through post hooks, image-bounded and blob-offloaded, metered, and returned.

**Call relations**: _dispatch_step_recovering is its caller. It calls helpers such as _pending_member_guidance, _redoes_on_replay, _offload, _bounded_image, _bounded, and _meter_dispatch.

*Call graph*: calls 6 internal fn (_bounded_image, _offload, _pending_member_guidance, _redoes_on_replay, _bounded, _meter_dispatch); called by 1 (_dispatch_step_recovering); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, replace, monotonic (+3 more)).


##### `TurnEngine._redoes_on_replay`  (lines 3125–3132)

```
def _redoes_on_replay(self, tool: ToolDef) -> bool
```

**Purpose**: Tells whether rerunning a tool body would redo work and can therefore be preempted by newer member guidance during crash recovery. Side-effecting tools rely on idempotency instead.

**Data flow**: It receives a tool definition and returns true for non-side-effecting tools.

**Call relations**: _dispatch_step uses it during adoption replay before rerunning a dispatch body.

*Call graph*: called by 1 (_dispatch_step).


##### `TurnEngine._pending_member_guidance`  (lines 3134–3150)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether an unclaimed member message is waiting in this conversation. During crash recovery, this can stop stale non-side-effecting work from running ahead of new user guidance.

**Data flow**: It queries inbound_message for one pending member-admitted row in the turn’s conversation and returns whether one exists.

**Call relations**: _dispatch_step calls it only inside a live dispatch body during adoption replay.

*Call graph*: called by 1 (_dispatch_step); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 3152–3180)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before sending them back to the model. This avoids provider limits and wasting context on pixels the model will not use.

**Data flow**: It receives an ImageBlock, decodes and opens the image, leaves it alone if already small, otherwise thumbnails it, converts format if needed, re-encodes it, and returns a new ImageBlock; on image errors it logs and returns the original.

**Call relations**: _dispatch_step calls it before storing successful image outputs in the blob store.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 3182–3254)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Retries terminal commit until it succeeds, then records terminal metrics and execution timing. This makes a final answer durable even through temporary database trouble.

**Data flow**: It receives status, usage, meter, answer/error/request fields, created refs, and arrival guards. It repeatedly calls _commit_once with backoff, emits metrics if this call wrote the terminal, marks the meter exited, logs the terminal, and returns the frame or None when arrivals prevented closing.

**Call relations**: TurnEngine.run and run_intent use it for done and failed terminal paths.

*Call graph*: calls 2 internal fn (_commit_once, exited); called by 2 (run, run_intent); 4 external calls (sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._publish_terminal`  (lines 3256–3259)

```
async def _publish_terminal(self, frame: TerminalFrame) -> None
```

**Purpose**: Publishes the terminal frame to live listeners and mirrors subagent status if needed. It also stops activity-summary background tasks.

**Data flow**: It receives a TerminalFrame, publishes it as a Terminal live frame, publishes run status through _publish_run, and cancels activity tasks.

**Call relations**: TurnEngine.run and run_intent call it after a terminal frame has been committed.

*Call graph*: calls 3 internal fn (_publish, _publish_run, _stop_activity); called by 2 (run, run_intent); 1 external calls (__init__).


##### `TurnEngine._record_workspace_changes`  (lines 3261–3274)

```
async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None
```

**Purpose**: Scans and records workspace file changes after the turn is done. It waits until transcript and terminal publishing are complete so the user is not blocked by the scan.

**Data flow**: It receives accumulated target paths, builds a WorkspaceChangeRecorder for the sandbox’s owning conversation, and asks it to record changes.

**Call relations**: TurnEngine.run calls it after publishing the terminal for normal turns.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 3276–3390)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to bill usage and write or read the terminal frame. It also refuses to close over unabsorbed arrivals when requested.

**Data flow**: It totals usage, optionally locks the conversation and checks pending arrivals, records turn usage, reads billed cost, builds a TerminalFrame, updates the turn if still non-terminal, or reads the existing terminal if someone already ended it.

**Call relations**: _commit wraps it with retry, metrics, and logging.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 11 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx, log (+1 more)).


##### `TurnEngine._park`  (lines 3392–3430)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Durably pauses a turn when spending or seat checks say it cannot continue. It bills usage so far, releases claimed arrivals, publishes a Parked frame, and leaves the turn resumable.

**Data flow**: It receives a reason message and usage events, updates the turn to parked in the database, records usage, clears consumed arrivals, then publishes the parked notice and metrics if the update succeeded.

**Call relations**: TurnEngine.run catches TurnParked and calls this.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 3432–3441)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes one live frame to the hub without letting publish failures break the turn. The database remains the source of truth.

**Data flow**: It receives a live frame, attempts to publish it for this turn id, and logs any failure.

**Call relations**: Many methods use it for text deltas, absorbed notices, replies, cost ticks, activity, parked notices, and terminal frames.

*Call graph*: called by 8 (_absorb_arrivals, _generate_activity, _park, _publish_cost, _publish_terminal, _speak, _stream_closing_spans, run); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 3443–3465)

```
async def _publish_run(self, activity: str='', status: str='') -> None
```

**Purpose**: Mirrors subagent activity and status onto the root turn stream that user surfaces are watching. Main turns do nothing here.

**Data flow**: It receives optional activity text and status, checks lineage, builds a SubagentActivity frame, and publishes it to the root turn id while logging failures.

**Call relations**: TurnEngine.run, _generate_activity, and _publish_terminal call it for subagent lifecycle updates.

*Call graph*: called by 3 (_generate_activity, _publish_terminal, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 3467–3483)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops sandbox commands after a deliberate workflow cancellation. It avoids killing work during ordinary worker preemption, because that work may be resumed.

**Data flow**: It calls sandbox.stop_commands and logs any failure without changing the already durable cancellation outcome.

**Call relations**: TurnEngine.run and run_intent call it on DBOS workflow cancellation paths.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._bill_cancelled`  (lines 3485–3505)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for tokens consumed before cancellation or preemption. Cancellation should not wait forever on billing, but usage should still count when possible.

**Data flow**: It totals usage events, opens a database transaction, records turn usage, and logs if billing fails.

**Call relations**: TurnEngine.run calls it on DBOS cancellation and asyncio preemption paths.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 3507–3512)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles an execution that did not win the running claim. It either republishes an already committed terminal or returns nothing while another live execution owns the turn.

**Data flow**: It creates a TranscriptRepair helper and calls resolve, returning the repaired terminal frame or None.

**Call relations**: TurnEngine.run and run_intent call it when _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 3514–3540)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the final transcript while attaching generated activity labels to tool results. This gives later readers a richer conversation record.

**Data flow**: It receives messages, answer, system prompt, and injected text, copies tool-result blocks that have matching activity labels, then delegates to TranscriptRepair.persist_transcript.

**Call relations**: TurnEngine.run and run_intent call it after committing a terminal result.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_inbound`  (lines 3542–3547)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Persists only inbound messages for non-complete exits through the transcript repair helper. This keeps user messages available for future turns.

**Data flow**: It receives optional arrival messages and founding denial text, creates a TranscriptRepair helper, and delegates to persist_inbound.

**Call relations**: TurnEngine.run calls it on parked, cancelled, failed, or non-done terminal paths.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


### Conversation state shaping
This file keeps reconstructed conversation history within usable context limits while preserving recent transcript fidelity.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling, when a conversation window is near or over the model limit`

Large language models can only read a limited amount of text at once. This file solves that by compacting the transcript when it gets too large: it summarizes the older “head” of the conversation and keeps the newer “tail” unchanged. Think of it like moving old paperwork into a labeled archive box while leaving the current papers on the desk.

The process is careful because a bad summary can silently lose important facts. The file first decides whether compaction is needed. If so, it splits the transcript at safe boundaries so tool calls and tool results stay together. It sends the old part to the model with special instructions asking for a structured JSON summary. It also watches for provider errors that mean the summary request itself was too large, and retries with less old material.

After a summary comes back, the code does not trust it blindly. It checks that important “anchors” survived, such as file paths, error names, loaded skills, and active request references. It removes file paths the model invented, renders the accepted summary into one replacement user message, verifies that the new window is actually smaller, then stores compressed before/after/summary records in the workspace blob store. Hooks and metrics are fired so operators can observe what happened.

#### Function details

##### `is_context_overflow`  (lines 105–111)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: Recognizes errors that likely mean the model provider rejected a request because the prompt was too large. This lets the system shrink and retry instead of treating the problem like an ordinary failure.

**Data flow**: It receives an exception, combines the exception class name and message into lowercase text, and searches for known phrases such as “context length” or “prompt is too large”. It returns true when one of those phrases is found, otherwise false.

**Call relations**: Compaction._summarize calls this after a summary attempt fails. If this detector says the failure was due to size, the summarizer drops some old rounds and tries again.

*Call graph*: called by 1 (_summarize).


##### `harvest_anchors`  (lines 114–140)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: Finds small but important facts from the old transcript that the replacement window must carry forward exactly. These facts are used as a safety checklist for the summary.

**Data flow**: It receives rendered old transcript text, loaded skill names, and active request text. It extracts durable tool-output paths, error class names, loaded skills, and message references, trims each kind to a bounded recent set, and returns Anchor objects for them.

**Call relations**: Compaction._compact calls this while building the boundary that summaries are judged against. Later, Compaction._verify uses the resulting anchors indirectly to see what the compacted window lost.

*Call graph*: called by 1 (_compact); 1 external calls (__init__).


##### `missing_anchors`  (lines 143–147)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: Checks which required facts are absent from the text that will remain after compaction. It uses exact text containment rather than a fuzzy similarity check.

**Data flow**: It receives the required anchors and the carried-forward text. It returns only the anchors whose literal text is not present in that carried text.

**Call relations**: Compaction._verify calls this after rendering a candidate compacted window. Missing anchors can trigger one retry that explicitly tells the model what it dropped.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 185–189)

```
def __repr__(self) -> str
```

**Purpose**: Provides a short, safe display form for a compaction request. It avoids printing the full transcript while still showing useful counts.

**Data flow**: It reads the request’s number of messages, reason, and number of active requests. It returns a compact string containing those counts.

**Call relations**: This is used implicitly by Python when the request is logged, inspected, or shown in debugging output. It supports the request object that Compaction.maybe_compact passes into Compaction._compact.


##### `_InvalidSummary.__init__`  (lines 193–196)

```
def __init__(self, error_class: str, message: str, usage: Usage) -> None
```

**Purpose**: Creates a special error for cases where the model answered the summary request, but the answer could not be used as a valid compaction summary. It keeps the model usage so the caller can still account for spent tokens.

**Data flow**: It receives an error class name, a human-readable message, and usage information. It stores the class name and usage on the exception and passes the details to the base RuntimeError.

**Call relations**: Compaction._summarize_once raises this when parsing or validating the model’s summary fails. Compaction._compact decides whether to ignore it for automatic compaction or raise it for forced recovery.

*Call graph*: called by 1 (_summarize_once).


##### `_InvalidSummary.__str__`  (lines 198–199)

```
def __str__(self) -> str
```

**Purpose**: Shows only the useful validation message when the invalid-summary error is converted to text.

**Data flow**: It reads the stored exception arguments and returns the message part as a string.

**Call relations**: This affects how invalid summary failures appear in logs, warnings, and raised errors created by Compaction._summarize_once.


##### `Compaction.__repr__`  (lines 240–241)

```
def __repr__(self) -> str
```

**Purpose**: Gives a compact debug label for a Compaction object. It identifies the conversation and model without printing large internal state.

**Data flow**: It reads the conversation ID and model name from the instance. It returns a short string containing those two values.

**Call relations**: This is used implicitly when a Compaction instance is displayed during debugging or logging.


##### `Compaction.maybe_compact`  (lines 243–270)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether a transcript should be compacted now, then returns either the original messages or the smaller replacement window. It is the public gate used before sending a conversation to the model.

**Data flow**: It receives messages, a force flag, and active request text. It checks simple no-op cases, estimates token count, compares it with the trigger, and if needed builds a _CompactionRequest and calls Compaction._compact. It returns the chosen message window plus any usage records from summary model calls.

**Call relations**: This is the main entry into the file’s compaction workflow. It calls Compaction._tokens and Compaction._trigger for the decision, then hands real work to Compaction._compact.

*Call graph*: calls 3 internal fn (_compact, _tokens, _trigger); 1 external calls (__init__).


##### `Compaction._trigger`  (lines 272–278)

```
def _trigger(self) -> int
```

**Purpose**: Calculates the transcript size at which automatic compaction should begin. It leaves room for the summary response and a safety buffer inside the model’s total context window.

**Data flow**: It reads an explicit trigger override if one is set. Otherwise it subtracts the summary token reserve and buffer from the configured context window and returns that number.

**Call relations**: Compaction.maybe_compact uses this to decide when to compact. Compaction._require_budget uses the same value to make sure the result will not immediately need compaction again.

*Call graph*: called by 2 (_require_budget, maybe_compact).


##### `Compaction._compact`  (lines 281–373)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline: choose what to summarize, ask for a summary, verify it, persist the records, and return the replacement window. It is the core workflow of the file.

**Data flow**: It receives a _CompactionRequest containing the current messages and reason. It selects head and tail, summarizes the head, drains loaded skills, builds verification boundaries, retries once if important anchors are missing, checks the token budget, writes compressed before/after/summary records, logs verification, fires hooks, and returns the new messages plus usage.

**Call relations**: Compaction.maybe_compact calls this after deciding compaction is needed. This function coordinates most helpers in the file, including selection, summarization, reference harvesting, verification, persistence, and observability.

*Call graph*: calls 11 internal fn (_next_index, _persist, _record_verification, _references, _require_budget, _select, _summarize, _tokens, _verify, _window_text (+1 more)); called by 1 (maybe_compact); 7 external calls (__init__, __init__, __init__, replace, from_iterable, log_error, warn).


##### `Compaction._select`  (lines 375–391)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: Splits the transcript into old messages to summarize and recent messages to keep exactly. It preserves whole conversation rounds so related tool calls and results are not separated.

**Data flow**: It receives all messages, groups them into rounds, then keeps enough trailing rounds to cover the configured recent-message count. It returns the old head rounds and the kept tail, or nothing if there is no safe head to summarize.

**Call relations**: Compaction._compact calls this at the start of the workflow. It relies on Compaction._rounds to form safe groups.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 393–407)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Groups messages into API rounds, where each assistant message starts a new round and following user/tool-result messages stay with it. This protects the meaning of tool interactions.

**Data flow**: It receives a tuple of messages and walks through them in order. It starts a new group when it sees an assistant message after existing content, then returns all groups as tuples.

**Call relations**: Compaction._select calls this before choosing which rounds become the summarized head and which remain as the verbatim tail.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 409–436)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]=()) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: Asks the model to summarize the old transcript, with bounded retries if the summary prompt itself is too large. This keeps compaction from failing just because the old head is enormous.

**Data flow**: It receives head rounds and optional missed anchors from a previous attempt. It calls Compaction._summarize_once; if the provider reports a context overflow, it drops the oldest portion of the rounds and retries up to the configured limit. It returns a valid CompactionSummary and usage records.

**Call relations**: Compaction._compact calls this for the first summary and possibly a second anchor-repair summary. It uses is_context_overflow to distinguish size failures and Compaction._drop_oldest to shrink retries.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 438–466)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Performs one actual model call to turn rendered old transcript text into a structured compaction summary. It also captures token usage for accounting.

**Data flow**: It prepares a ModelRequest using the compaction prompt, rendered transcript text, model name, token limit, session ID, and reasoning settings. It streams text deltas from the model, records the usage event, parses the final text into a CompactionSummary, and returns the summary plus usage. If parsing fails, it raises _InvalidSummary with usage attached.

**Call relations**: Compaction._summarize calls this inside its retry loop. It delegates input creation to Compaction._prepare and output validation to Compaction._parse_summary.

*Call graph*: calls 3 internal fn (_parse_summary, _prepare, __init__); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 468–490)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: Builds the text that is sent to the summarizing model. It turns structured messages into readable transcript blocks and adds instructions when a retry must preserve missed facts.

**Data flow**: It receives rounds and any missed anchors. It renders each message as role plus text, folds large repeated runs, optionally appends a correction list of missed anchor literals, and ends with a reminder to return only the required JSON object.

**Call relations**: Compaction._summarize_once calls this before making the model request. It uses Compaction._text to render messages, Compaction._fold_repeated_runs to reduce bulk, and Compaction._bullets for retry facts.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 492–505)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Compresses long stretches of repeated text before sending them to the summarizer. This keeps spammy or stuck-tool output from making the summary request too large.

**Data flow**: It receives rendered transcript text. It replaces repeated short word sequences with one copy plus a marker saying how many times it repeated, and returns the shortened text.

**Call relations**: Compaction._prepare calls this while building the summary prompt. Its nested fold helper formats each individual replacement.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 500–503)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Formats one repeated-text match into a shorter phrase with a repetition count. It is the small replacement function used by the regular expression substitution.

**Data flow**: It receives a regex match, extracts the repeated unit, estimates how many times it appeared, and returns the unit followed by a “[repeated N times]” marker.

**Call relations**: It is used only inside Compaction._fold_repeated_runs when the regular expression finds a repeated run.


##### `Compaction._drop_oldest`  (lines 507–512)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Shrinks an oversized summary prompt by removing the oldest part of the head. This gives the retry a better chance of fitting within the provider’s limit.

**Data flow**: It receives the head rounds and removes the oldest fifth, with at least one round removed. It returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this only after is_context_overflow identifies a prompt-too-large failure.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 514–554)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Turns the model’s raw text response into a validated CompactionSummary object. It tolerates extra text around the JSON but rejects missing, malformed, schema-invalid, or empty summaries.

**Data flow**: It receives raw model output text, finds the first balanced JSON object, validates it against the CompactionSummary schema, checks that the intent is not empty, and returns the typed summary. On failure it raises RuntimeError.

**Call relations**: Compaction._summarize_once calls this after collecting the model stream. If it raises, Compaction._summarize_once wraps the problem in _InvalidSummary.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 556–573)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output file paths from the summarized head that should be mentioned after compaction. These paths let later turns reread large outputs without pasting them into the summary.

**Data flow**: It receives head rounds and the kept tail. It scans text for tool-output paths, skips paths already visible in the tail, keeps unique paths, bounds the list to the most recent few, and returns them.

**Call relations**: Compaction._compact calls this while building the boundary for the replacement message. Compaction._render later includes these references outside the model-authored summary.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 575–614)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns a validated summary plus system-carried facts into the single user message that replaces the old transcript head. The output is deterministic, so the same inputs always produce the same compacted context.

**Data flow**: It receives a CompactionSummary, durable references, and active requests. It creates titled sections only for fields that have content, appends references and active requests verbatim, prefixes the whole thing as compacted context, and returns the final text.

**Call relations**: Compaction._verify calls this to build the candidate replacement window. Compaction._require_budget also calls it with an empty summary to estimate the unavoidable carried text.

*Call graph*: calls 1 internal fn (_bullets); called by 2 (_require_budget, _verify).


##### `Compaction._bullets`  (lines 616–617)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a list of strings as markdown-style bullet lines. It gives summaries and retry instructions a consistent readable shape.

**Data flow**: It receives a tuple of text items and returns one string where each item is prefixed with “- ” and separated by newlines.

**Call relations**: Compaction._prepare uses it for missed-anchor correction instructions. Compaction._render uses it for summary sections and durable references.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 619–620)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: Renders a sequence of messages into plain text for searching and verification. It is used when the code needs to ask, “does this fact appear anywhere in this window?”

**Data flow**: It receives messages, converts each message to text with Compaction._text, joins them with newlines, and returns the combined text.

**Call relations**: Compaction._compact uses this to define what existed before compaction. Compaction._verify uses it to check what remains after compaction.

*Call graph*: calls 1 internal fn (_text); called by 2 (_compact, _verify).


##### `Compaction._verify`  (lines 622–658)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: Checks one candidate summary before it can replace live conversation history. It removes invented file paths, adds known loaded skills, renders the replacement, and records what important anchors were lost.

**Data flow**: It receives a summary, a boundary describing the original window, and whether this is a retry. It keeps only summary file references that appeared in the original text, renders the compacted message, combines it with the unchanged tail, counts tokens, finds missing anchors, builds a CompactionVerification, and returns a _Candidate containing all of that.

**Call relations**: Compaction._compact calls this after a first summary and possibly after an anchor-repair retry. It uses missing_anchors to grade exact fact preservation and Compaction._render to create the replacement text.

*Call graph*: calls 4 internal fn (_render, _tokens, _window_text, missing_anchors); called by 1 (_compact); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._require_budget`  (lines 660–698)

```
def _require_budget(self, verification: CompactionVerification, boundary: _Boundary) -> None
```

**Purpose**: Refuses to install a compacted window when it clearly failed the size goal. This prevents wasting summary calls while making the next model request no better or even worse.

**Data flow**: It receives verification data and the fixed boundary. It estimates unavoidable carried text, compares before tokens, after tokens, tail tokens, summary allowance, and the compaction trigger, then either returns silently or raises RuntimeError.

**Call relations**: Compaction._compact calls this after verification and before persistence. It uses Compaction._trigger, Compaction._render, and Compaction._tokens to apply the same budget rules as the trigger decision.

*Call graph*: calls 3 internal fn (_render, _tokens, _trigger); called by 1 (_compact); 1 external calls (__init__).


##### `Compaction._record_verification`  (lines 700–723)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], verification: CompactionVerification) -> None
```

**Purpose**: Reports the quality of a completed compaction. It makes losses visible in logs and metrics instead of hiding them inside a stored summary.

**Data flow**: It receives the compaction index, reason, and verification object. It logs token counts, missing anchors, dropped paths, and retry status, then emits a metric labeled as clean or lossy and retried or not.

**Call relations**: Compaction._compact calls this after persistence. Operators and evaluation tooling can use the log and metric output to understand how well compaction is preserving information.

*Call graph*: called by 1 (_compact); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 725–736)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Stores the full compaction record: the original window, the replacement window, and the structured summary. This creates an audit trail and lets later readers inspect exactly what changed.

**Data flow**: It receives an index, before messages, after messages, and the summary. It writes before and after windows through Compaction._write, compresses the summary JSON with lz4, and stores it in the blob store under the summary key.

**Call relations**: Compaction._compact calls this only after verification and budget checks pass. It relies on Compaction._key to place each stored piece at the correct path.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 738–742)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next available numbered slot for a compaction record in the blob store. This keeps multiple compactions in one conversation ordered and separate.

**Data flow**: It starts at index 1, checks whether an “after” record already exists for that index, and increments until it finds an unused slot. It returns that index.

**Call relations**: Compaction._compact calls this before writing records. It uses Compaction._key to ask the blob store about the expected paths.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 744–751)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a previously stored compaction record for inspection. It returns nothing if that numbered record does not exist.

**Data flow**: It receives an index, tries to read compressed before, after, and summary blobs using their keys, and if all are found passes them to decode_compaction. It returns a CompactionRecord or None when any blob is missing.

**Call relations**: This is a lookup utility for consumers that want to inspect saved compactions. It uses Compaction._key for paths and the transcript decoder to rebuild the typed record.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 753–761)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes either the “before” or “after” message window for a compaction record. It serializes the window in a stable form and compresses it before storage.

**Data flow**: It receives an index, a half name, and messages. It wraps messages in a CompactionWindow, dumps stable compact JSON, compresses the bytes with lz4, and stores them in the blob store at the computed key.

**Call relations**: Compaction._persist calls this twice, once for the original window and once for the replacement window. It uses Compaction._key to choose the storage path.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 763–764)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the blob-store path for one piece of a compaction record. It centralizes the naming rule so all reads and writes agree.

**Data flow**: It receives a compaction index and a half name such as before, after, or summary. It combines those with the conversation ID through compaction_key and returns the resulting path string.

**Call relations**: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record all call this whenever they need to locate compaction data.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 766–789)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: Estimates how many model tokens a set of messages will cost. This is used to decide when to compact and whether the compacted result is small enough.

**Data flow**: It receives messages. For each message it counts role text, rendered content text, hidden reasoning bytes, and an estimated cost for images, then sums those estimates and returns the total.

**Call relations**: Compaction.maybe_compact uses this for the initial trigger check. Compaction._compact, Compaction._verify, and Compaction._require_budget use it to measure before, after, and tail sizes.

*Call graph*: calls 3 internal fn (_image_count, _opaque_chars, _text); called by 4 (_compact, _require_budget, _verify, maybe_compact).


##### `Compaction._opaque_chars`  (lines 791–810)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Counts hidden reasoning data that is sent back to the model but not rendered as normal summary text. This prevents token estimates from accidentally treating those blocks as free.

**Data flow**: It receives one message. If the content is plain text it returns zero; otherwise it scans blocks for thinking signatures, redacted thinking data, and encrypted reasoning content, sums their byte-string lengths, and returns the total.

**Call relations**: Compaction._tokens calls this for each message so token estimates include invisible but still costly reasoning payloads.

*Call graph*: called by 1 (_tokens).


##### `Compaction._image_count`  (lines 812–822)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts inline images in a message so token estimates include their approximate cost. Images may contain little or no text, but they still consume model context.

**Data flow**: It receives one message. If the content is plain text it returns zero; otherwise it counts ImageBlock objects directly and images inside tool-result parts, then returns the total.

**Call relations**: Compaction._tokens calls this for each message and multiplies the count by a fixed image token estimate.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 824–848)

```
def _text(self, message: Message) -> str
```

**Purpose**: Converts a structured message into plain text for summarizing, scanning, and token estimation. It gives every relevant block a readable representation, including markers for images and redacted reasoning.

**Data flow**: It receives one message. Plain string content is returned directly; structured content is walked block by block, extracting text, thinking summaries, tool results, tool-use calls, image markers, and redacted markers, then joining them with newlines.

**Call relations**: Compaction._prepare uses this to render the summary prompt. Compaction._references and Compaction._window_text use it for searching, and Compaction._tokens uses it as part of size estimation.

*Call graph*: called by 4 (_prepare, _references, _tokens, _window_text); 1 external calls (dumps).


### Prompt context inputs
These files assemble the auxiliary context shown to the model, including skills, delivery rules, and hosted site links.

### `core/src/ufo/runtime/skills/selection.py`

`domain_logic` · `per-turn prompt construction`

An agent can have saved skill cards, each with a name, description, and sometimes a pinned flag. This file decides where those cards appear: either folded into the normal system prompt when the list is small, or placed in a separate saved-skills block when the list is larger. Without this, a big skill library could crowd out the actual conversation, or the model might not know that useful skills exist.

The file uses fixed character budgets, like suitcase size limits. Short lists go directly into the main skill index. Bigger lists get a special `<saved_skills>` block. That block is filled in a careful order: pinned skills first, then the whole catalog if it fits, and if not, the best-matching skills get full descriptions while the rest still appear by name. This means the model can see that a skill exists even if its full description had to be shortened away.

The matching is simple and deterministic: the query is split into meaningful words, and a skill scores higher when those words appear in its name or description. There is no file access, network access, or randomness here. The main combined helper, `member_visibility`, does the decisions and rendering together so each turn can run quickly even with many cards.

#### Function details

##### `_query_terms`  (lines 35–42)

```
def _query_terms(query: str) -> tuple[str, ...]
```

**Purpose**: Turns a user's query into a clean list of search words. It removes tiny words, ignores punctuation, limits very long input, and keeps each useful term only once.

**Data flow**: It receives a query string → cuts it down to the maximum allowed length, lowercases it in a Unicode-safe way, splits it wherever there are non-word characters, drops terms shorter than the minimum length, and removes duplicates while keeping the original order → returns a tuple of search terms.

**Call relations**: This is the shared preparation step for matching skills. `lexical_score` uses it before scoring one card, and `select_top_k` uses it once before ranking many cards.

*Call graph*: called by 2 (lexical_score, select_top_k).


##### `_term_hits`  (lines 45–47)

```
def _term_hits(terms: Sequence[str], card: SkillCard) -> int
```

**Purpose**: Counts how many prepared search terms appear in a skill card. It is the small scoring helper that says, in plain terms, how well this card matches the query words.

**Data flow**: It receives already-cleaned search terms and one skill card → combines the card's name and description into one lowercase text area → counts each query term that appears anywhere in that text → returns that count as an integer.

**Call relations**: `lexical_score` calls this after preparing the query. It is the actual comparison step between the query words and a card's visible text.

*Call graph*: called by 1 (lexical_score).


##### `lexical_score`  (lines 50–55)

```
def lexical_score(query: str, card: SkillCard) -> int
```

**Purpose**: Gives one skill card a simple match score for a query. Someone would use it to ask, “How many of the query's meaningful words show up in this skill's name or description?”

**Data flow**: It receives a query string and a skill card → uses `_query_terms` to turn the query into useful terms, then uses `_term_hits` to count matches in the card → returns the number of distinct terms found.

**Call relations**: This is the public single-card scoring function. It is built from `_query_terms` and `_term_hits`, while the larger ranking path in this file uses the same idea through `select_top_k`.

*Call graph*: calls 2 internal fn (_query_terms, _term_hits).


##### `select_top_k`  (lines 58–66)

```
def select_top_k(query: str, cards: Sequence[SkillCard]) -> tuple[SkillCard, ...]
```

**Purpose**: Chooses the best-matching unpinned saved skills for a query. It deliberately leaves pinned cards out because pinned cards are always shown first elsewhere.

**Data flow**: It receives a query and a sequence of skill cards → prepares the query terms once, removes pinned cards from consideration, sorts the remaining cards by how many terms match their names or descriptions, and keeps only the configured number of top results → returns those selected cards as a tuple.

**Call relations**: `member_visibility` calls this when the full catalog is too large to show with descriptions. The selected cards are the ones that get full lines in the saved-skills block.

*Call graph*: calls 1 internal fn (_query_terms); called by 1 (member_visibility).


##### `skill_line`  (lines 69–71)

```
def skill_line(card: SkillCard) -> str
```

**Purpose**: Formats one skill card as a compact display line. It also caps the line length so one long description cannot push out many other skills.

**Data flow**: It receives a skill card → builds text in the form `- name: description` → cuts that text to the configured maximum line length → returns the shortened line.

**Call relations**: This is the standard way this file renders a full skill card. `folds_into_prompt`, `prompt_index`, `catalog_fits`, and `member_visibility` all rely on it so size checks and final display use the same text.

*Call graph*: called by 4 (catalog_fits, folds_into_prompt, member_visibility, prompt_index).


##### `folds_into_prompt`  (lines 74–78)

```
def folds_into_prompt(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Decides whether all member skill cards are small enough to fit directly into the system prompt. This keeps small saved-skill sets looking just like built-in skills.

**Data flow**: It receives a sequence of skill cards → turns each card into a capped `skill_line`, measures the combined joined size, and compares it with the prompt-fold budget → returns true if the cards fit, otherwise false.

**Call relations**: `prompt_index` calls this before adding member skills to the main prompt index. It uses `_joined_size` for the measurement and `skill_line` for the exact text being measured.

*Call graph*: calls 2 internal fn (_joined_size, skill_line); called by 1 (prompt_index).


##### `prompt_index`  (lines 81–93)

```
def prompt_index(registry: SkillRegistry) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill index entries that go into the main prompt for a turn. It includes deployed skills always, and member saved skills only when their list is small enough.

**Data flow**: It receives a skill registry → reads the registry's member cards and deployed skill index → checks whether member cards fold into the prompt; if not, it returns only the deployed index, and if yes, it appends member skill names and capped descriptions in the same style → returns a tuple of skill index entries.

**Call relations**: This function is the bridge between the registry and prompt construction. It calls the registry's `index()` for deployed skills, uses `folds_into_prompt` to decide whether member cards join them, and uses `skill_line` so the prompt text matches the size check.

*Call graph*: calls 3 internal fn (index, folds_into_prompt, skill_line).


##### `catalog_fits`  (lines 96–99)

```
def catalog_fits(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Checks whether every saved skill can be shown as a full line inside the saved-skills block. If this is true, no search ranking is needed.

**Data flow**: It receives skill cards → renders each as a capped line, measures the total block size including the wrapper tags, and compares that to the block budget → returns true if the whole catalog fits.

**Call relations**: This is a standalone version of one decision also made inside `member_visibility`. It relies on `skill_line` for consistent text and `_block_size` for the budget calculation.

*Call graph*: calls 2 internal fn (_block_size, skill_line).


##### `member_visibility`  (lines 113–146)

```
def member_visibility(query: str, cards: Sequence[SkillCard]) -> MemberVisibility
```

**Purpose**: Makes the full saved-skill visibility decision for one turn. It says whether skills fold into the prompt, whether the whole catalog fits the saved-skills block, and what block text should be shown.

**Data flow**: It receives the current query and saved skill cards → renders each card line once, checks the prompt budget and block budget, and returns an empty block if there are no cards or the cards already fold into the prompt. If the full catalog fits, it renders pinned cards first and then unpinned cards. If it does not fit, it renders pinned cards, then top query matches with full descriptions, then the remaining unpinned skills by bare name, trimming from the end if needed and adding a count of dropped skills → returns a `MemberVisibility` result containing the decisions and final block.

**Call relations**: `member_block` calls this as the main worker. Inside, it uses `_joined_size` and `_block_size` for budget checks, `skill_line` for card text, `select_top_k` when it must choose described matches, and `_render` to wrap the final lines in saved-skills tags.

*Call graph*: calls 5 internal fn (_block_size, _joined_size, _render, select_top_k, skill_line); called by 1 (member_block); 1 external calls (__init__).


##### `member_block`  (lines 149–157)

```
def member_block(query: str, cards: Sequence[SkillCard]) -> str
```

**Purpose**: Returns just the saved-skills text block for a turn. This is the simple function to call when the caller only needs text to insert into the turn message.

**Data flow**: It receives the query and saved skill cards → asks `member_visibility` to make all placement and budget decisions → returns only the block string from that result, which may be empty.

**Call relations**: This is a convenience wrapper around `member_visibility`. It lets callers use the full saved-skill rendering behavior without needing the extra folded/catalog flags.

*Call graph*: calls 1 internal fn (member_visibility).


##### `_joined_size`  (lines 163–164)

```
def _joined_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how long a group of lines would be if joined with newline characters. It is a small helper for enforcing character budgets consistently.

**Data flow**: It receives a sequence of already-rendered lines → adds each line length plus the newline spacing between lines, with a special case for no lines → returns the total character count.

**Call relations**: `folds_into_prompt` and `member_visibility` use this to check the prompt-fold budget. `_block_size` also builds on it to measure a wrapped saved-skills block.

*Call graph*: called by 3 (_block_size, folds_into_prompt, member_visibility).


##### `_block_size`  (lines 167–168)

```
def _block_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how large a saved-skills block would be after adding its opening and closing tags. This prevents the block from exceeding the configured prompt space.

**Data flow**: It receives rendered skill lines → uses `_joined_size` to measure the body, adds the fixed wrapper tag sizes and needed newline spacing → returns the total character count.

**Call relations**: `catalog_fits` and `member_visibility` call this when deciding whether all full skill lines fit inside the saved-skills block.

*Call graph*: calls 1 internal fn (_joined_size); called by 2 (catalog_fits, member_visibility).


##### `_render`  (lines 171–172)

```
def _render(lines: tuple[str, ...]) -> str
```

**Purpose**: Wraps saved-skill lines in the `<saved_skills>` and `</saved_skills>` tags. This gives the model a clear marked section for saved skills.

**Data flow**: It receives a tuple of lines → places the opening tag before them and the closing tag after them, separated by newlines → returns one formatted string.

**Call relations**: `member_visibility` calls this after it has chosen exactly which lines fit. It is the final formatting step before the saved-skills block is returned.

*Call graph*: called by 1 (member_visibility).


### `core/src/ufo/runtime/turns/delivery_register.py`

`config` · `startup and prompt assembly`

This file is a small but important rulebook loader. The project has a shared “delivery register,” meaning a standard voice and format for any final answer an agent gives to a human member or to another agent. Rather than copying those rules into many places, this file reads them once from `delivery_register.md` and exposes them as `DELIVERY_REGISTER_BLOCK` for other parts of the system to insert into prompts.

It also defines limits for two kinds of results. `DIRECT_PROSE_RESULT_MAX_CHARS` caps short direct prose results. `SUBAGENT_RESULT_MAX_WORDS` caps the short message a subagent is allowed to send upward. The longer `SUBAGENT_RESULT_DESCRIPTION` is prompt text that tells a subagent exactly how to finish: produce one parent-visible result, keep it very short, follow the shared delivery rules, and call `finish` instead of first writing the result as ordinary assistant text.

In everyday terms, this file is like a laminated house style card placed at every workstation. Without it, different agents or extensions could answer in inconsistent formats, repeat artifact contents unnecessarily, or leak intermediate prose when they should only return a concise final result.


### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`domain_logic` · `request handling`

A conversation can have related hosted sites, but the system should not simply show every site it knows about. This file is the gatekeeper for the conversation’s Sites slot: a small panel or section that lists sites connected to the current conversation.

The important idea is authorization. The conversation context includes visible items, which are objects the current viewer is allowed to know about. The code first translates those visible object names into site names. Then it asks the hosted-sites store for matching site records in this workspace and conversation. After that, it double-checks that each returned site still matches the visible authorization object and its generation number. A generation is like a version stamp; it helps prevent showing a stale or replaced authorization.

Once the allowed sites are known, the file builds `ConversationSite` entries. Each entry includes the site name, timestamps, the authorization information, and a public URL made from the public base address, workspace, conversation, and site name. It only returns up to the configured maximum, and marks the result as truncated if more were available.

At the bottom, `SITES_SLOT` registers this behavior with the larger conversation system. Without this file, sites might not appear in conversations, or worse, the system could risk showing site links that the viewer is not meant to see.

#### Function details

##### `_read`  (lines 13–46)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: This function builds the full Sites slot content for one conversation. It finds the sites that are both connected to the conversation and authorized by the viewer’s visible items, then returns display-ready site records with URLs.

**Data flow**: It receives a conversation slot context containing the conversation ID, visible authorization items, workspace/store access, transaction, and public base URL. It translates visible item names into expected site names, reads matching hosted-site rows from storage, filters out any row whose authorization name or generation does not match, converts the remaining rows into `ConversationSite` objects with public URLs, and returns a `SitesSlotPayload`. The output contains only the allowed sites up to the maximum limit, plus a flag saying whether extra allowed sites were left out.

**Call relations**: The conversation slot provider calls `_read` when the system needs the actual Sites content to show. Inside, it relies on `site_name_from_object` and `site_object_name` to move between authorization-object names and site names, uses `HostedSites` to fetch stored site records, calls `site_url` to make browser-usable links, and packages the result with `ConversationSite` and `SitesSlotPayload`.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 49–51)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Sites slot without reading full site details. It is used when the interface only needs to know whether there are sites, and roughly how many can be shown.

**Data flow**: It receives the conversation slot context and looks only at the number of visible items. It caps that number at the configured maximum for conversation sites. If the result is zero, it returns `None` so the slot can be treated as empty; otherwise it returns the count.

**Call relations**: The conversation slot provider calls `_summarize` when it needs a lightweight summary instead of the full list. Unlike `_read`, it does not go to storage or build URLs; it simply uses the visible items already present in the context to support quick slot display decisions.


### System prompt rendering
This file turns the assembled context into the final checked and digestible system prompt sent to the model.

### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `turn setup before sending a prompt to the model`

A system prompt is the instruction sheet the project gives to the model before a turn begins. This file is the prompt assembly station. It starts from core Markdown templates on disk, such as the outer shell, citation text, knowledge-cutoff wording, and compaction prompt. Then it inserts the agent’s own instructions, available skills, contributed capability sections, the shared citation block, and the model’s knowledge cutoff date.

The important idea is that prompt holes must be filled deliberately. Placeholders like `{{agent-prompt}}` or `{{sections}}` are treated like labeled blanks on a form. If a required blank is left behind, or if an agent prompt asks for a variable that was not supplied, the code raises an error instead of quietly sending curly-brace text to the model. This protects the model from seeing broken instructions and protects developers from subtle prompt mistakes.

The file also normalizes extra blank lines and computes a SHA-256 digest, which is a short fingerprint of the final prompt text. That fingerprint helps observability: if behavior changes, people can tell exactly which prompt version was used.

#### Function details

##### `rendered_prompt`  (lines 54–55)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This wraps finished prompt text together with a content fingerprint. The fingerprint lets the rest of the system record exactly which prompt text was used without storing or comparing the whole text every time.

**Data flow**: It receives the final prompt string. It hashes that string with SHA-256, prefixes the hash with `sha256:`, and returns a `RenderedPrompt` object containing both the digest and the original content. It does not change any outside state.

**Call relations**: After `render_template` has filled every slot and cleaned up the text, it calls `rendered_prompt` as the final packaging step. `rendered_prompt` hands back the finished prompt object that can be sent onward and logged by digest.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 58–75)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This builds the normal system prompt for the main agent. It combines the shared shell template with the agent prompt, skill list, capability sections, citation text, and a human-readable knowledge cutoff date.

**Data flow**: It takes an agent prompt, a list of section name/body pairs, an optional list of skill name/description pairs, and a machine-formatted knowledge cutoff such as `2026-02`. It turns that date into a readable month like `February 2026`, inserts it into the knowledge-cutoff wording, places that into the shell template, and then asks `render_template` to fill the remaining slots. The result is a `RenderedPrompt` with content and digest.

**Call relations**: This is the higher-level entry for normal prompt rendering in this file. When code needs the main agent’s system prompt, it calls `render_system_prompt`, which prepares the knowledge-cutoff block and then delegates the detailed slot filling and validation to `render_template`.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 78–96)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This is the main template-filling routine. It replaces the known slots in a prompt template and refuses to return a prompt if any placeholder is missing, extra, or left unresolved.

**Data flow**: It receives a template, an agent prompt, a mapping of variable names to values, a skill list, and section bodies. First it fills variables inside the agent prompt using `_substitute_vars`. If there is agent text but the template has no `{{agent-prompt}}` slot, it raises an error. Then it replaces the skill, citation, sections, and agent-prompt slots. It checks the filled text for any leftover `{{name}}` placeholders, collapses long runs of blank lines, trims the end, and returns a digested `RenderedPrompt`.

**Call relations**: `render_system_prompt` calls this after preparing the shell. Inside, `render_template` calls `_substitute_vars` to safely fill agent-specific variables, `render_skill_index` to format the skills block, and `rendered_prompt` to package the final text with its digest.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 99–108)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This turns the list of available skills into the small prompt block the model can read. If there are no skills, it returns an empty string so the prompt does not include a pointless empty section.

**Data flow**: It receives a sequence of skill names and descriptions. With no skills, it outputs `""`. With skills, it creates an `<available_skills>` block where each skill appears as a bullet with its description, then returns that text.

**Call relations**: `render_template` calls this while filling the `{{skill_index}}` slot. The formatted block is inserted into the larger prompt so the model knows which loadable skills are available.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 111–118)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This safely fills variable placeholders inside the agent prompt. It checks both sides: every placeholder in the prompt must have a value, and every supplied value must correspond to a placeholder.

**Data flow**: It receives a prompt fragment and a mapping of variable names to replacement strings. It scans the fragment for placeholders like `{{user_name}}`, compares them with the supplied keys, and raises an error if anything is missing or extra. If the sets match, it replaces each placeholder with its supplied value and returns the filled text.

**Call relations**: `render_template` calls this before inserting the agent prompt into the larger shell. This makes variable mistakes fail early, before the prompt is packaged by `rendered_prompt` or sent to the model.

*Call graph*: called by 1 (render_template).

## 📊 State Registers Touched

- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-workflow-claims` — The workflow attempt and run-claim state that prevents two workers from running the same turn, scheduled task, listener, or cleanup job at once.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-subagent-tasks` — The parent-child delegation state that tracks spawned helper agents, their inputs, outputs, names, costs, and undelivered results.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-turn-resource-budget` — In-flight per-turn resource budget and usage accumulator for spend, model tokens, cache reads, sandbox tokens, egress, retries, and stop conditions before final ledger reconciliation.
- `reg-human-question-state` — Pending human-question and answer state used when tools or workflows ask a member for input and later resume the affected turn.
- `reg-conversation-slot-provider-registry` — Registered providers that summarize and read extension conversation slots such as artifacts, sources, sites, automations, and task panels.
- `reg-durable-workflow-store` — Serialized durable workflow/checkpoint objects used to reload or resume long-running turns, jobs, and recovery work after crashes or code changes.
- `reg-rendered-prompt-audit` — Per-turn rendered prompt metadata, slot validation results, and prompt digests used to trace or replay the exact model prompt later.
