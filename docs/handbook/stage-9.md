# Turn Claim, Context Assembly, Skills, and Prompt Construction  `stage-9`

This stage prepares one unit of agent work before and during a turn. A “turn” is one pass where the agent reads the latest conversation, thinks, may use tools, and writes back. The queue runner claims pending work so two workers do not handle the same turn. The turn engine then gathers the needed context, runs the model and tools, watches for new messages, records costs, and saves either the result or a safe failure.

Several helpers assemble what the model will see. Conversation compaction shortens old chat history into a checked summary so it fits inside the model’s reading limit, while keeping recent messages intact. Skill loading reads built-in and user-created skills, resolves their dependencies, and puts selected ones into the sandbox. Skill selection keeps the list small and relevant. The skill store preserves user-made skills and prevents unsafe names, overwrites, and conflicts.

Other files add live reference material: spawn catalogs describe which subagents can be started, the model catalog lists available AI models, automations summarize scheduled tasks, and the delivery register supplies reply-format rules. Finally, prompt rendering fills templates and checks the completed system prompt before the model sees it.

## Files in this stage

### Turn Orchestration
These files claim a unit of queued work and drive the agent turn through context setup, execution, failure handling, and result delivery.

### `core/src/ufo/loop/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that a folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Here, the drawer is `ufo.loop`, which likely holds code related to the project’s loop or event-processing behavior elsewhere in the package. Because this file is empty, importing `ufo.loop` does not run setup code, create objects, or expose helper functions directly. Its main value is structural: it gives the surrounding codebase a stable package path to import from. Without it, depending on the Python version and packaging setup, imports involving `ufo.loop` could be less predictable or fail in environments that require explicit package markers.


### `core/src/ufo/loop/queue.py`

`orchestration` · `turn queue processing`

Think of this file as the dispatch desk and staging area for agent work. A turn may need a model, tools, a sandbox, credentials, skills, billing rules, subagents, and live progress updates. This file gathers those pieces in the right order and hands them to the TurnEngine, which does the actual model-and-tool loop.

It is designed for reliability. Turns are run through DBOS workflows, which are durable workflows that can replay after a crash. The file first claims a turn so two workers do not run the same work at once. If another turn should run next for the same conversation, it enqueues that handoff. If setup fails before the engine can write a final result, a backstop writes a failed terminal message so clients are not left waiting forever.

The file also decides which tools and skills are visible. Main agents get normal member-facing tools unless configured otherwise. Subagents get tools from their profile and grants. Workspace skills can be placed into prompts, loaded into the sandbox, or silently tested through a “shadow” selector that logs what retrieval would have chosen without slowing the turn.

Without this file, queued turns would not reliably become running agent work, failures could leave conversations stuck, subagent answers might not return to parents, and each process would lack the runtime wiring needed to serve many workspaces safely.

#### Function details

##### `_without_workspace_skills`  (lines 159–162)

```
async def _without_workspace_skills(name: str) -> None
```

**Purpose**: This is a placeholder skill loader used when an agent has workspace skills turned off. It deliberately loads nothing, so saved workspace skills do not enter the prompt or tool set through this path.

**Data flow**: It receives a skill name, ignores it, and returns nothing. No database, sandbox, or registry state is changed.

**Call relations**: During turn setup, _run_turn may use this as the materializer for member skills when workspace skills are disabled. It acts like a locked door: even if something asks for a workspace skill by name, nothing is opened.


##### `_member_skill_turn`  (lines 165–177)

```
def _member_skill_turn(turn: Turn) -> bool
```

**Purpose**: This decides whether a turn is the kind of turn where member workspace skills should be considered. It filters out prepared intents and a special internal root turn that has no real user topic.

**Data flow**: It reads the turn’s admission source, speaker, parent, and message shape. It returns true for ordinary conversational turns and false for turns where injecting saved skills would not match real user input.

**Call relations**: _member_skill_block uses this before adding a saved-skills prompt block, and _run_turn uses it before launching shadow skill selection. It keeps skill behavior aligned with the turns that actually have meaningful user text.

*Call graph*: called by 2 (_member_skill_block, _run_turn).


##### `_member_skill_block`  (lines 180–187)

```
def _member_skill_block(turn: Turn, view: MemberVisibility, enabled: bool) -> str
```

**Purpose**: This chooses the saved-skills text block that should be added to a turn’s founding prompt. It only includes the block when member skills are enabled and the turn is eligible for them.

**Data flow**: It receives a turn, a prepared visibility view of matching skills, and a feature switch. If the switch is off or the turn is not eligible, it returns an empty string; otherwise it returns the view’s prompt block.

**Call relations**: _run_turn calls this while preparing prompts for both main-agent and subagent turns. It relies on _member_skill_turn so the prompt does not mention workspace skills on turns where they should not apply.

*Call graph*: calls 1 internal fn (_member_skill_turn); called by 1 (_run_turn).


##### `_prompt_skill_index`  (lines 190–194)

```
def _prompt_skill_index(skills: SkillRegistry, enabled: bool) -> tuple[tuple[str, str], ...]
```

**Purpose**: This builds the skill index text that goes into a system prompt. When member skill prompt blocks are enabled, it uses the fold-aware prompt view; otherwise it falls back to the deploy-time skill index only.

**Data flow**: It receives the current skill registry and a boolean switch. It returns a tuple of skill index entries, either including prompt-aware member skill presentation or only the registry’s normal index.

**Call relations**: _run_turn calls this while rendering main-agent and subagent system prompts. It hands off to the skill registry or the skill selection helper depending on the configuration.

*Call graph*: calls 1 internal fn (index); called by 1 (_run_turn); 1 external calls (prompt_index).


##### `_fire_shadow_selection`  (lines 200–207)

```
def _fire_shadow_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: This starts a background experiment that compares two ways of choosing relevant workspace skills. It does not delay the user’s turn; it only keeps the task alive long enough to log its result.

**Data flow**: It receives the search index, embedding client, turn, and candidate skill cards. It creates an asynchronous task and stores it in a module-level set until it finishes.

**Call relations**: _run_turn calls this only for eligible member-skill turns where the whole catalog does not fit in the prompt. It hands the real comparison work to _shadow_skill_selection while the turn continues running.

*Call graph*: calls 1 internal fn (_shadow_skill_selection); called by 1 (_run_turn); 1 external calls (create_task).


##### `_shadow_skill_selection`  (lines 210–236)

```
async def _shadow_skill_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: This quietly tests skill retrieval quality for one turn. It compares lexical matching, which looks at words directly, with vector search, which searches by meaning using an embedding, and logs both results.

**Data flow**: It takes the turn’s inbound text and skill cards. It embeds a shortened version of the text, asks the index for vector matches, also computes lexical top matches, and writes a log entry; on any error or timeout it logs a failure instead of affecting the turn.

**Call relations**: _fire_shadow_selection launches this in the background. It calls the embedding service, vector index, and lexical selector, but because it is best-effort, its failures never bubble back into _run_turn.

*Call graph*: calls 2 internal fn (embed, vector); called by 1 (_fire_shadow_selection); 3 external calls (timeout, log, select_top_k).


##### `_agent_tools`  (lines 239–267)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> tuple[ToolDef, ...]
```

**Purpose**: This decides which tools a normal agent is allowed to expose to the model for a turn. It protects profile-only tools from ordinary agents unless a profile explicitly names them.

**Data flow**: It receives all registered tools, an optional allowlist, the turn’s admission source, and optionally the speaking member. If there is no allowlist, or a speaking prepared intent is being admitted, it returns normal non-profile-only tools. If there is an allowlist, it returns only named live tools, adding skill_search when load_skill is allowed.

**Call relations**: _run_turn uses this while building the ToolRegistry for main-agent turns. It is one of the gates that keeps powerful or specialized tools from being accidentally exposed.

*Call graph*: called by 1 (_run_turn).


##### `_resolve_profile`  (lines 270–285)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: This looks up the subagent profile named on a turn. If the profile has disappeared, it logs useful details before letting the failure continue.

**Data flow**: It receives the subagent registry, turn id, and requested profile name. It returns the matching SubagentProfile, or logs the requested and registered names and re-raises the unknown-profile error.

**Call relations**: _run_turn calls this before setting up a subagent turn. It delegates lookup to the SubagentRegistry and adds clearer observability if a queued child turn refers to an extension profile that is no longer deployed.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 288–301)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: This decides which tools a subagent can use. It starts with the profile’s declared tools, optionally adds granted tools, and includes default subagent tools when the profile is not isolated.

**Data flow**: It receives all tools, a subagent profile, and the grant names available to that profile. It builds an allowed-name set, adds skill_search if load_skill is present, then returns the registered tools that match those rules.

**Call relations**: _run_turn calls this when preparing a subagent ToolRegistry. It mirrors _agent_tools for the subagent case, but obeys profile isolation and subagent grants.

*Call graph*: called by 1 (_run_turn).


##### `_apply_provisions`  (lines 307–316)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: This applies extension-shipped agent provisioning for a workspace once per process. It makes sure new or existing workspaces receive agents bundled by active extensions without repeating the work every turn.

**Data flow**: It receives the runtime and workspace id. If this process has already provisioned that workspace, it returns; otherwise it runs AgentProvisioning using the active manifests and records the workspace as done.

**Call relations**: _execute_turn calls this near the start of a turn. It uses AgentProvisioning so onboarding and first-turn setup can converge on the same workspace state.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `init_runtime`  (lines 358–369)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: This installs the process-wide Runtime object that all queued turn workflows will use. It also seeds sandbox carriers with bundled system skills so sandboxes can start with those skills available.

**Data flow**: It receives a Runtime containing configuration, registries, stores, clients, and services. If runtime was already installed, it raises an error; otherwise it builds a system skill bundle, seeds eligible sandbox carriers, and stores the runtime in the module global.

**Call relations**: The serving process calls this before any turn workflow runs. Later, _execute_turn reads the installed runtime instead of rebuilding all shared services for every turn.

*Call graph*: calls 1 internal fn (from_skills).


##### `reset_runtime`  (lines 372–377)

```
def reset_runtime() -> None
```

**Purpose**: This clears the installed process runtime. It exists mainly for tests, where one test may need to replace the runtime with another.

**Data flow**: It takes no input, sets the module-level runtime back to None, and returns nothing.

**Call relations**: Normal server startup installs runtime once and does not call this. Test code can call it before init_runtime to avoid the single-initialization guard.


##### `_execute_turn`  (lines 380–432)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: This is the outer body of a durable turn workflow. It binds the workspace, loads just enough turn information for tracing, applies workspace provisioning, runs the turn, catches setup failures, and finally tries to deliver child results to their parent.

**Data flow**: It receives workspace and turn ids as strings from the workflow system. It reads the runtime and turn metadata from the database, enters the workspace scope, runs _run_turn, writes a failed terminal if something outside the engine crashes, triggers parent delivery, and returns a status string such as failed, parked, or superseded.

**Call relations**: turn_workflow calls this as the workflow entry. It calls _apply_provisions before running, _run_turn for the main work, _commit_failed_terminal for emergency failure reporting, and _deliver_to_parent after completion.

*Call graph*: calls 4 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _run_turn); called by 1 (turn_workflow); 6 external calls (select, agent, workspace_tx, turn_span, ws, UUID).


##### `_deliver_to_parent`  (lines 435–467)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: This sends a finished child or subagent turn’s durable terminal result back to the parent conversation that spawned it. It intentionally treats delivery failure as a delay, not as a reason to mark the child turn failed.

**Data flow**: It receives runtime and child turn id. It rereads the child turn from the database, exits early if there is no parent or no terminal result, then asks SubagentResult to deliver the saved result; if delivery fails, it logs that delivery is deferred.

**Call relations**: _execute_turn calls this after _run_turn finishes or fails. It uses runtime.invoker_for and the subagent registry to route the result back, while swallowing non-cancellation errors so the already-finished turn is not mislabeled.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_enqueue_handoff`  (lines 470–505)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: This enqueues another turn that should run after the current claim handoff. It also clears the turn’s dispatch marker if enqueueing fails, so another dispatcher can try again later.

**Data flow**: It receives the DBOS client, workspace id, turn id, conversation id, and workflow id. It builds queue options using the conversation as the partition key, submits the workflow to DBOS, and on cancellation or failure updates the queued turn to show it is no longer dispatched.

**Call relations**: _run_turn calls this after claiming a turn if the claim process reports a handoff. It hands the next turn to DBOS while preserving queue recovery if the enqueue attempt does not complete.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 508–831)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: This is the main staging function for a turn. It claims the turn, loads its records, chooses tools and skills, prepares billing and sandbox access, builds the TurnEngine, and runs either a normal model loop or an intent action.

**Data flow**: It receives the runtime and turn id. It claims the turn, loads the turn, agent, and audience, gathers extension tools and hooks, resolves main-agent or subagent settings, freezes billing and bring-your-own-key decisions, prepares sandbox authorization, loads preloaded skills when needed, constructs the engine with all services, and returns the engine’s final status. If the turn parks, it returns parked; if setup fails, it commits a failed terminal and returns failed.

**Call relations**: _execute_turn calls this as the core work. Inside, it coordinates many helpers in this file, including _load_turn, _agent_tools, _subagent_tools, _member_skill_block, _frozen_billing_identity, _frozen_byok, _open_sandbox through a late opener, and _commit_failed_terminal. It then hands the fully assembled state to TurnEngine.run or TurnEngine.run_intent.

*Call graph*: calls 14 internal fn (_agent_tools, _commit_failed_terminal, _enqueue_handoff, _fire_shadow_selection, _frozen_billing_identity, _frozen_byok, _load_turn, _member_skill_block, _member_skill_turn, _previous_turn_ended_at (+4 more)); called by 1 (_execute_turn); 35 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 547–551)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: This small inner helper authorizes subagent spawning for a particular acting member. It returns both the spawn function and the authorized subagent controller for that member.

**Data flow**: It receives an acting member id or None. It asks the Subagents object to authorize that actor, then returns the authorized spawn callable together with the authorized Subagents object.

**Call relations**: _run_turn defines this while building the TurnEngine. The engine can later use it when a tool or model action needs to spawn work under a specific member’s authority.


##### `_commit_failed_terminal`  (lines 834–895)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: This is the safety backstop that makes sure a turn ends visibly as failed when setup or outer orchestration crashes. It keeps retrying until it can write or publish the failure, so clients waiting on the turn are released.

**Data flow**: It receives the hub, turn id, and exception. It builds a terminal failure frame from the error class and shortened message, tries to update queued or running turns to failed, emits metrics and logs the stack only if it actually performed the transition, publishes the terminal to subscribers, and retries with backoff if the database or publish path fails.

**Call relations**: _execute_turn and _run_turn both call this when errors escape setup or engine construction. It publishes through the Hub so listeners see a final frame, and it avoids double-counting if the engine already wrote its own terminal.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 10 external calls (__init__, __init__, sleep, update, workspace_tx, emit_metric, formatted_stack, log, log_error, turn_profile).


##### `turn_workflow`  (lines 899–900)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: This is the DBOS workflow entry for running one queued turn. DBOS uses it as the durable function name that can be enqueued, replayed, and recovered.

**Data flow**: It receives workspace and turn ids as strings from DBOS, passes them to _execute_turn, and returns the resulting status string.

**Call relations**: DBOS calls this when a turn workflow is dequeued from TURN_QUEUE. It is intentionally thin so _execute_turn contains the actual orchestration.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 903–990)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: This loads the database records needed to run a turn and converts them into typed Python objects. It also derives the audience for the conversation, meaning who the turn is meant to speak to or represent.

**Data flow**: It receives a turn id. It joins the turn, agent, and conversation tables, reads fields such as prompt, model, tools, context, terminal state, sandbox conversation, and audience, then returns a Turn object, an Agent object, and a parsed Audience object.

**Call relations**: _run_turn calls this after claiming the turn, and sometimes again when a claim is superseded. The typed objects it returns become the foundation for prompt rendering, tool selection, sandbox setup, and engine construction.

*Call graph*: called by 1 (_run_turn); 8 external calls (__init__, __init__, model_validate, model_validate, model_validate, select, workspace_tx, parse_audience).


##### `_run_lineage`  (lines 993–1025)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: This finds where a spawned turn’s live activity should be published. For nested child turns, it walks up to the root turn so the user interface can follow one coherent activity stream.

**Data flow**: It receives a Turn. If the turn has no parent, it returns None. If it is a child, it follows parent links in the database until it reaches the root, determines the profile name to display, and returns a RunLineage object.

**Call relations**: _run_turn calls this before constructing TurnEngine. The engine uses the lineage to publish child or subagent activity under the right parent stream.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 1028–1040)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: This finds when the previous turn in the same conversation ended. That timing can be used by the engine for context-sensitive behavior, such as activity summaries or recency decisions.

**Data flow**: It receives the current Turn. If it is the first turn, it returns None. Otherwise it reads the previous sequence number’s updated_at timestamp from the database and ensures the result has a timezone.

**Call relations**: _run_turn calls this for non-intent turns before building the engine. Intent turns skip it because they do not follow the normal model-round flow.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_frozen_billing_identity`  (lines 1043–1064)

```
async def _frozen_billing_identity(turn_id: UUID, candidate: _BillingIdentity) -> _BillingIdentity
```

**Purpose**: This freezes the model and price information used to bill one workflow attempt. Freezing matters because crash recovery may replay setup, and the replay must use the same billing facts as the original attempt.

**Data flow**: It receives a turn id and a candidate billing identity. It locks the turn row, checks whether the same attempt already stored billing data, returns the stored data if present, or writes and returns the candidate if not.

**Call relations**: _run_turn calls this after resolving the model and current price table. The returned identity is then used to build the Pricing object and to force the agent’s model name for this attempt.

*Call graph*: called by 1 (_run_turn); 4 external calls (model_dump, select, update, workspace_tx).


##### `_frozen_byok`  (lines 1067–1116)

```
async def _frozen_byok(workspace_id: UUID, turn_id: UUID, key_slot: str | None, attempt: str) -> bool
```

**Purpose**: This decides, once per attempt, whether the workspace is using its own model provider key. BYOK means “bring your own key,” and the decision affects billing because calls paid by the workspace’s own key are treated differently.

**Data flow**: It receives workspace id, turn id, required key slot, and attempt id. It reads any stored BYOK decision for that attempt; if none exists, it checks whether the workspace owns the needed key, writes the decision if the attempt is still unset or different, rereads the settled row, and returns the settled boolean.

**Call relations**: _run_turn calls this before constructing the TurnEngine. It relies on workspace_owns_the_key for the actual key check, but stores the result on the turn so recovery and concurrent replay do not bill one attempt two different ways.

*Call graph*: called by 1 (_run_turn); 5 external calls (or_, select, update, workspace_owns_the_key, workspace_tx).


##### `_open_sandbox`  (lines 1119–1182)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: This opens or attaches to the sandbox container where a turn can run commands and access files. It prepares the signed run token and environment variables for git, tool bridge access, connector grants, and credential-backed providers.

**Data flow**: It receives sandbox services, token codec, the turn, optional grants and credential stores, CLI credential mappings, credential slots, and a cache rewrite flag. It builds a RunToken, optionally adds git cache configuration, derives credential and grant environment variables only at open time, and calls the sandbox service to open the conversation’s sandbox session.

**Call relations**: _run_turn passes this as the late opener for _LateSandbox, so a sandbox is only opened if the engine actually needs it. It calls token encoding and sandbox environment helpers, then hands the final request to ConversationSandbox.open.

*Call graph*: calls 2 internal fn (open, encode); 7 external calls (__init__, span, cache_git_config, _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env).


##### `SandboxAuthorizer.authorize`  (lines 1193–1205)

```
async def authorize(self, acting_member_id: UUID | None) -> Sandbox
```

**Purpose**: This re-authorizes sandbox access for a specific acting member. It creates a fresh run token that includes that member and updates grant-related environment variables for the sandbox session.

**Data flow**: It receives an acting member id or None. It encodes a RunToken with workspace, turn, and actor information, derives grant CLI environment variables for that actor, and returns a sandbox object authorized with the new token and environment changes.

**Call relations**: _run_turn creates a SandboxAuthorizer and gives its authorize method to the TurnEngine for normal, non-intent turns. When a tool needs member-scoped sandbox authority, the engine calls this method to get the correctly authorized sandbox.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### `core/src/ufo/loop/engine.py`

`orchestration` · `per-turn execution, from claim through model/tool loop to terminal commit`

A “turn” is one unit of work where an agent responds to a user message, scheduled prompt, subagent request, or prepared tool intent. This file is the conductor for that work. It makes sure only one worker owns the turn, loads the transcript, adds context like time and speaker, calls the model, streams partial text to listeners, runs any tools the model asks for, and feeds tool results back into later model rounds until there is a final answer.

The file is careful because a turn can last a while and can do real things: spend money, write files, call connectors, spawn child agents, and ask users for credentials. It uses DBOS steps, meaning selected actions are recorded so that after a crash they replay from saved results instead of happening twice. Think of it like a kitchen ticket with checkmarks: if the cook crashes after making the sauce, the replacement does not make a second sauce; they continue at the next unchecked step.

It also watches for new messages that arrive mid-turn, billing caps, revoked seats, model context overflow, overlarge tool outputs, screenshots, and cancellation. At the end it writes a durable terminal frame, updates the transcript, publishes live events, and records workspace changes. Without this file, the system would not have a reliable, resumable way to turn user input plus model/tool work into a safe final result.

#### Function details

##### `_claim_turn`  (lines 230–277)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued or parked turn for one workflow attempt, or reclaims it if the same attempt is being replayed after a crash. This prevents two workers from running the same turn at the same time.

**Data flow**: It receives a turn id and an attempt id. It reads the turn and locks its conversation, then updates the turn to running if it is available or already running under the same attempt. It returns whether the claim was fresh, adopted from the same attempt, or lost.

**Call relations**: TurnEngine._mark_running calls this at the start of normal and intent turns. _claim_turn_with_handoff also uses it before deciding whether to enqueue the next waiting turn.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 288–348)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[str | None, _TurnHandoff | None]
```

**Purpose**: Claims a turn and, if possible, marks the next queued turn in the same conversation as ready to dispatch. This helps the worker hand off work without leaving the conversation idle.

**Data flow**: It takes a turn id and attempt id, first tries to claim the current turn, then checks the same conversation for the next queued turn. If it stamps a next turn for dispatch, it returns a small handoff record with ids needed to start it.

**Call relations**: It builds on _claim_turn. It is designed for the dispatch layer outside this file, which needs both the claim result and possible next-turn handoff.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `_activity_goal`  (lines 417–418)

```
def _activity_goal(requesters: Mapping[UUID, ActiveMessage]) -> str
```

**Purpose**: Builds a short description of what active user messages are asking for. This text helps summarize tool activity in human-friendly words.

**Data flow**: It receives active requester messages, extracts the member-visible message text from each, and joins them with newlines. The result is a plain text goal string.

**Call relations**: TurnEngine._model_round and TurnEngine.run_intent use it before starting activity summaries for tool calls.

*Call graph*: called by 2 (_model_round, run_intent); 1 external calls (member_message_text).


##### `_RoundInput.__repr__`  (lines 430–435)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug string for a model round input. It avoids printing the full prompt and instead shows useful sizes and flags.

**Data flow**: It reads the stored messages, system prompt length, and round options, then returns a short string summary. It does not change anything.

**Call relations**: Python uses this when the object is logged or displayed, especially around DBOS step inputs.


##### `_ResolvedToolCall.__repr__`  (lines 443–444)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug label for a tool call that has been successfully bound to its execution context.

**Data flow**: It reads the tool name and call id and returns a concise string. No state changes.

**Call relations**: Used implicitly by logging/debugging when resolved tool calls are shown.


##### `_BoundToolCall.__repr__`  (lines 452–453)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug label for a tool call ready to dispatch.

**Data flow**: It reads the tool name and call id and returns a concise string. No state changes.

**Call relations**: Used implicitly by logging/debugging when bound dispatch inputs are shown.


##### `_RejectedToolCall.__repr__`  (lines 463–467)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug label for a tool call that could not be prepared.

**Data flow**: It reads the tool name, call id, outcome, and error class, then returns a compact string. No state changes.

**Call relations**: Used implicitly by logging/debugging when rejected dispatch inputs are shown.


##### `ModelStreamError.__init__`  (lines 525–526)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Stores a model streaming failure with its original error class, message, and any partial output already received.

**Data flow**: It receives the model error name, message, and optional partial text. It stores all three in the exception arguments so crash recovery and later error handling can read them.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after a recorded model round reports an error instead of raising inside the DBOS step.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 528–530)

```
def __str__(self) -> str
```

**Purpose**: Formats the model stream error as a readable string that includes the original model-side error class.

**Data flow**: It reads the stored class and message and returns them as one string. It deliberately leaves out partial output.

**Call relations**: Used whenever the exception is converted to text, including terminal error reporting and overflow checks.


##### `ModelStreamError.model_error_class`  (lines 533–535)

```
def model_error_class(self) -> str
```

**Purpose**: Exposes the original error class reported by the model provider.

**Data flow**: It reads the first stored exception argument and returns it. Nothing is changed.

**Call relations**: TurnEngine._model_round and TurnEngine._commit_once use this to distinguish truncation from other model failures and to report the right terminal error.


##### `ModelStreamError.partial_output`  (lines 538–540)

```
def partial_output(self) -> str
```

**Purpose**: Exposes the partial text and tool-call fragments received before a model stream failed.

**Data flow**: It reads the stored partial output string and returns it. Nothing is changed.

**Call relations**: TurnEngine._model_round uses it when recovering from model truncation by saving partial content to a workspace file.


##### `ModelStreamError.model_error_message`  (lines 543–545)

```
def model_error_message(self) -> str
```

**Purpose**: Exposes the original error message from the model provider.

**Data flow**: It reads the stored message and returns it. Nothing is changed.

**Call relations**: TurnEngine._commit_once uses it when building the terminal frame for a failed turn.


##### `TurnParked.__init__`  (lines 552–554)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates an exception that means the turn must pause, not fail, because a seat, balance, or spend limit stopped it.

**Data flow**: It receives the member-facing reason and stores it both as the exception text and as a message field. The turn can later be resumed.

**Call relations**: TurnEngine._enforce_spend raises it. TurnEngine.run catches it and parks the turn durably.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 563–587)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits model-requested tool calls into groups that can safely run together while preserving the order the model chose.

**Data flow**: It receives the tool registry and a tuple of tool calls. It yields small tuples: consecutive parallel-safe tools are grouped, while unsafe or unknown tools become ordering barriers.

**Call relations**: TurnEngine._model_round uses these segments before binding and dispatching tools, so concurrent work does not reorder important actions.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 590–592)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed tool-call JSON fragments into a Python dictionary of arguments.

**Data flow**: It receives a list of partial JSON strings, joins them, and parses the JSON. Blank input becomes an empty dictionary.

**Call relations**: TurnEngine._stream_once uses it after the model stream finishes, when assembling final ToolUseBlock objects.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 595–613)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Creates the small context block placed before a member message so the model knows when, who, and where the message came from.

**Data flow**: It receives a message id, optional turn context, and admission time. It formats the time in the sender’s timezone when available and returns a text tag with metadata.

**Call relations**: TranscriptRepair.load_messages uses it for the founding message. TurnEngine._render_arrival uses it for messages that arrive while a turn is running.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 616–621)

```
def _bounded(content: str) -> str
```

**Purpose**: Cuts very large tool text down to the maximum size allowed in model context and adds a note saying how much was removed.

**Data flow**: It receives a string. If it is short enough, it returns it unchanged; otherwise it returns the prefix plus a truncation notice.

**Call relations**: TurnEngine._dispatch_step uses it for large error output, and TurnEngine._model_round uses it for forced finish validation errors.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_meter_dispatch`  (lines 624–651)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str) -> None
```

**Purpose**: Records metrics for one tool call: how it ended and how long it took. This gives operators a clear view of slow or failing tools.

**Data flow**: It receives the registry, call, start time, outcome, error class, and profile. It normalizes unknown tool names, emits a count, and emits a duration measurement.

**Call relations**: TurnEngine._bind_or_error records failures that happen before dispatch. TurnEngine._dispatch_step records the actual dispatch outcome.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 654–697)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedRef, ...]]
```

**Purpose**: Finds which skill packages are already present in the recent conversation window, so the engine does not reload the same skill instructions unnecessarily.

**Data flow**: It scans model messages for completed load-skill tool calls, reads the requested skill names from the original call input, and asks the registry for each skill’s closure. It yields only complete, readable skill closures.

**Call relations**: TurnEngine._reseed_loaded_skills uses this before and after compaction to keep the loaded-skill tracker accurate.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 700–722)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Reads a structured payload from the last successful tool call in a round, when that tool is meant to be the round’s final action.

**Data flow**: It receives the round’s tool calls, their results, a tool name, and a validation model. If the last call matches and its result contains valid JSON, it returns the parsed payload; otherwise it returns nothing.

**Call relations**: TurnEngine._model_round uses it to detect a final ask_user request, where only a final question should pause the turn for a user answer.

*Call graph*: called by 1 (_model_round); 1 external calls (loads).


##### `_pending_act`  (lines 725–753)

```
def _pending_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Reads the most recent successful structured request of a given kind from anywhere in a round.

**Data flow**: It receives tool calls, results, a tool name, and a validation model. It searches backward, parses the handler’s JSON result, and returns the newest valid payload or nothing.

**Call relations**: TurnEngine._model_round uses it for credential and account-connection requests. TurnEngine.run_intent uses it after a direct intent tool call.

*Call graph*: called by 2 (_model_round, run_intent); 1 external calls (loads).


##### `_created_refs`  (lines 756–788)

```
def _created_refs(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> tuple[ObjectRef, ...]
```

**Purpose**: Extracts object references for objects actually created by object_apply tool results.

**Data flow**: It receives tool calls and results, looks for successful object_apply results, parses their JSON, and returns valid object references whose result says created.

**Call relations**: TurnEngine._fold_created accumulates these during model turns. TurnEngine.run_intent uses it for direct intent turns.

*Call graph*: called by 2 (_fold_created, run_intent); 2 external calls (__init__, loads).


##### `_total_usage`  (lines 791–799)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many model usage records into one total usage record.

**Data flow**: It receives a list of usage events and sums input, output, and cache token fields. It returns a single Usage object with totals.

**Call relations**: Billing, spend enforcement, live cost publishing, parking, cancellation billing, committing, and model metrics all call this to price the turn consistently.

*Call graph*: called by 6 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost, _stream_once); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 814–838)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal result for a turn that already finished, usually after a duplicate delivery or crash recovery.

**Data flow**: It reads the stored terminal frame from the database. If there is one, it persists the inbound transcript if needed, publishes the terminal live, and returns the frame; if not, it returns nothing.

**Call relations**: TurnEngine._resolve_unclaimed uses this when a worker cannot claim the turn, so duplicate workers do not overwrite live work.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 840–847)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed conversation transcript, including the assistant’s final answer.

**Data flow**: It receives the model messages, final answer, system prompt, and injected context. It appends the final assistant message and passes the full conversation to write_conversation.

**Call relations**: TurnEngine persists successful turns through its wrapper, which delegates here.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 849–868)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves user messages when a turn ends without a normal assistant answer, such as failure, cancellation, denial, or parked state.

**Data flow**: It loads the founding message or builds a denied version, appends any absorbed arrivals, and writes that conversation without assistant error text.

**Call relations**: TranscriptRepair.resolve may call it before republishing a terminal. TurnEngine uses its wrapper on non-done exits.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 870–879)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads prior conversation messages and appends this turn’s founding inbound message in the form the model should see.

**Data flow**: It reads earlier transcript messages, adds a context tag for normal member turns, and returns the combined message tuple. Spawned turns keep their promised raw payload.

**Call relations**: TurnEngine._load_messages delegates here. TranscriptRepair.persist_inbound also uses it when preserving inbound-only transcripts.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 881–887)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the stored conversation before this turn, without accidentally reading this turn’s own replayed write.

**Data flow**: It asks the transcript store for the saved conversation. If there is no older transcript, or it is already at this turn’s sequence or later, it returns an empty tuple; otherwise it returns stored messages.

**Call relations**: TranscriptRepair.load_messages and persist_inbound use this as the base for transcript writes.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 889–909)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation transcript with a few retries, so temporary storage errors do not immediately lose the record.

**Data flow**: It receives messages and optional system/injected text, builds a Conversation record, and tries to write it. On failure it logs and sleeps before retrying.

**Call relations**: TranscriptRepair.persist_transcript and persist_inbound both use this as the actual durable transcript writer.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 943–960)

```
def exited(self, status: str) -> None
```

**Purpose**: Records how long this execution of a turn took and how many model rounds it ran.

**Data flow**: It receives an exit status. If the meter has not already ended, it emits duration and round-count metrics and marks itself ended.

**Call relations**: TurnEngine._commit calls it after terminal commit or terminal readback. TurnEngine.run also calls it on parked, cancelled, and preempted exits.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 1051–1062)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the engine was wired with compatible audiences and no forbidden finish-tool conflict.

**Data flow**: After the dataclass is built, it compares tool and hook audiences with the turn audience. For subagent output contracts, it also rejects a normal tool named finish.

**Call relations**: Python dataclass construction calls this automatically before any turn runs.


##### `TurnEngine.__repr__`  (lines 1064–1068)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug label for this engine instance.

**Data flow**: It reads the turn id, agent id, and profile, then returns a compact string. It does not change state.

**Call relations**: Used implicitly by logging, tracing, or debugging when the engine object is displayed.


##### `TurnEngine.profile`  (lines 1071–1074)

```
def profile(self) -> str
```

**Purpose**: Returns the telemetry profile name for this turn, such as main, agent, or a subagent profile.

**Data flow**: It reads whether the turn is spawned and its subagent profile, then delegates to the shared profile formatter. It returns a string.

**Call relations**: Many engine methods use this when emitting metrics and logs so main and subagent work can be separated.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 1076–1281)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal agent turn from claim to final terminal result. This is the main path for answering a prompt with model rounds and tools.

**Data flow**: It starts metrics, claims the turn, builds ToolContext, fires prompt hooks, loads messages, loops through model/tool rounds, commits the terminal, writes transcripts, publishes live frames, and cleans up. On park, cancel, preemption, or failure, it records the appropriate durable state or billing and re-raises where needed.

**Call relations**: This is the central orchestration method. It calls helpers for claiming, loading, scheduled memory, model rounds, commits, publishing, billing, parking, arrival release, transcript persistence, sandbox stopping, and workspace-change recording.

*Call graph*: calls 17 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _publish, _publish_run (+7 more)); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, escape, monotonic, emit_metric (+2 more)).


##### `TurnEngine.run.rank_find`  (lines 1095–1117)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Runs the host-side model call used by the browser find tool to rank page elements.

**Data flow**: It receives a system prompt and user prompt, sends a small model request, accumulates streamed text, and records usage into the current turn and dispatch-local usage list.

**Call relations**: TurnEngine.run places this function into ToolContext as find, so tools can call it during dispatch and have its cost attached to the same turn.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine.run_intent`  (lines 1283–1412)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a prepared intent turn, which is a direct tool call rather than a model conversation.

**Data flow**: It claims the turn, parses the inbound intent or bridge request, builds one ToolUseBlock, binds and dispatches it, commits success or failure, writes the transcript, publishes the terminal, and cleans up.

**Call relations**: It shares the same claiming, binding, dispatch recovery, commit, transcript, terminal publishing, and sandbox-cancel helpers as normal TurnEngine.run, but skips model rounds.

*Call graph*: calls 13 internal fn (_bind_or_error, _commit, _dispatch_step_recovering, _load_messages, _mark_running, _persist_transcript, _publish_terminal, _resolve_unclaimed, _start_activity, _stop_sandbox_commands (+3 more)); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, model_validate_json, monotonic (+2 more)).


##### `TurnEngine._scheduled_system`  (lines 1414–1448)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns, when memory search is available.

**Data flow**: It receives the base system prompt, searches memory using the scheduled inbound text, formats matches as escaped bullet points, and returns an expanded prompt. If search fails or finds nothing, it returns the original prompt.

**Call relations**: TurnEngine.run calls this only for scheduled admissions before the model sees the prompt.

*Call graph*: called by 1 (run); 5 external calls (__init__, timeout, escape, log, audience_subjects).


##### `TurnEngine._mark_running`  (lines 1450–1458)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn for the engine’s workflow attempt.

**Data flow**: It passes the turn id and attempt id into _claim_turn and returns true if the claim succeeded. It does not do extra work itself.

**Call relations**: TurnEngine.run and TurnEngine.run_intent call it at startup. If it fails, they use _resolve_unclaimed instead of running the turn.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 1460–1461)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates the small TranscriptRepair helper tied to this turn, transcript store, and hub.

**Data flow**: It reads engine fields and returns a TranscriptRepair object. It changes nothing.

**Call relations**: TurnEngine uses it from loading, transcript persistence, inbound persistence, and unclaimed-turn repair paths.

*Call graph*: called by 5 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed, run); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 1463–1465)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the transcript messages for this turn while wrapping the operation in a trace span.

**Data flow**: It creates a repair helper and asks it to load messages. The returned tuple becomes the model’s starting conversation window.

**Call relations**: TurnEngine.run and run_intent call it before writing transcripts or starting model work.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 1467–1688)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the model/tool loop until there is a final answer, a pending user request, or the round budget is exhausted.

**Data flow**: It receives current context, messages, usage lists, request trackers, and accumulators. Each loop absorbs arrivals, checks spend, compacts context, streams one model round, dispatches tool calls if present, records created objects and pending acts, and appends assistant/tool-result messages. It returns final messages, answer text, and any open question or credential/account request.

**Call relations**: TurnEngine.run calls this after setup. It coordinates arrival absorption, spend enforcement, compaction, streaming, speech, dispatching, forced final answers, forced finish, created-object folding, and cost publishing.

*Call graph*: calls 19 internal fn (_absorb_arrivals, _bind_or_error, _dispatch, _enforce_spend, _fold_created, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills (+9 more)); called by 1 (run); 10 external calls (__init__, __init__, __init__, __init__, gather, marked_replies, emit_metric, log, span, change_targets).


##### `TurnEngine._fold_created`  (lines 1690–1718)

```
async def _fold_created(self, created: dict[ObjectRef, None], tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> None
```

**Purpose**: Adds newly created object references from a round to the turn’s durable created list.

**Data flow**: It reads tool results, extracts created refs, compares them with the accumulator, updates the accumulator, and writes the full set back to the turn row if there are new ones.

**Call relations**: TurnEngine._model_round calls it after tool dispatch, even on unwind, so creations are not lost if later work fails.

*Call graph*: calls 1 internal fn (_created_refs); called by 1 (_model_round); 2 external calls (update, workspace_tx).


##### `TurnEngine._absorb_arrivals`  (lines 1720–1780)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Pulls newly queued inbound messages into the current model conversation between rounds.

**Data flow**: It asks _claim_arrivals for a recorded batch, appends each rendered or denied arrival to messages and the arrival log, updates active requesters, tracks absorbed ids, and publishes an absorbed notice for member messages.

**Call relations**: TurnEngine._model_round calls it at the start of each round so new user guidance is seen before the next model call.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); called by 1 (_model_round); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 1782–1834)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Delivers mid-turn reply spans that the model explicitly marked as messages to members.

**Data flow**: It receives marked reply spans and a round number. For main turns, it writes idempotent mid-turn reply rows and publishes live Reply frames; for subagent turns or empty spans, it does nothing.

**Call relations**: TurnEngine._model_round calls it when a tool-calling round has marked replies before continuing with tools.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_model_round); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 1836–1846)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Streams marked reply text from the final round back to live listeners, because final-round spans are part of the final answer rather than separate mid-turn replies.

**Data flow**: It receives marked spans and publishes each span’s text as a TextDelta. It does not write delivery rows.

**Call relations**: TurnEngine._model_round and _force_final call it when a closing answer is ready.

*Call graph*: calls 1 internal fn (_publish); called by 2 (_force_final, _model_round); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 1848–1872)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Turns one queued inbound message into exactly the text the model should see, or into a safe denial.

**Data flow**: It receives the message id, body, context, speaker, and created time. It fires the user_prompt_submit hook, adds a context tag and injected context if allowed, or returns the denial text.

**Call relations**: TurnEngine._claim_arrivals calls it inside the DBOS step so replay uses the same rendered text without firing hooks again.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1875–1944)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Atomically claims pending inbound messages for this turn and records their rendered form as a replayable DBOS step.

**Data flow**: It receives ids already absorbed, marks unconsumed or recoverable inbound rows as consumed by this turn, renders them in sequence order, and returns Arrival records. For spawned turns, it claims only internal arrivals.

**Call relations**: TurnEngine._absorb_arrivals calls this. Because it is a DBOS step, crash recovery reuses the same claimed batch instead of consuming messages twice.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 7 external calls (__init__, model_validate, and_, or_, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 1946–1965)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrival rows that were claimed but not safely absorbed, so a later turn can read them.

**Data flow**: It receives absorbed ids, clears consumed_turn_id for this turn’s other stamped arrivals, and logs any failure without stopping the exit path.

**Call relations**: TurnEngine.run calls it on cancellation, preemption, and failure paths where unfinished arrival claims should not be lost.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1967–2005)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, requesters: dict[UUID, ActiveMessage]) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Gets a best-effort final answer when the model has used all allowed tool rounds.

**Data flow**: It records exhaustion, checks spend, compacts context, then either forces a subagent finish tool or asks the model for one no-tool final answer. It publishes cost and returns final messages plus answer text.

**Call relations**: TurnEngine._model_round calls it after the round limit. It uses _force_finish for subagents and _stream_recovering_overflow for normal forced answers.

*Call graph*: calls 5 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_closing_spans, _stream_recovering_overflow); called by 1 (_model_round); 4 external calls (__init__, marked_replies, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 2007–2032)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to close through the finish tool so its answer matches the required output schema.

**Data flow**: It requires an output model, runs one model round with only finish available and selected, validates the finish arguments, and returns canonical JSON. If the model does not comply, it raises an error.

**Call relations**: TurnEngine._model_round uses it when a subagent stops with prose, and _force_final uses it when a subagent exhausts its round budget.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 2034–2097)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False, active_requests: tuple[str, ...]=()
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large.

**Data flow**: It builds a RoundInput and calls _stream_once. It adds usage, turns recorded stream errors into ModelStreamError, and on context overflow asks compaction to shrink the messages before retrying.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish use this instead of calling _stream_once directly so overflow recovery is consistent.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 2099–2159)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks before each model round that the turn is still allowed to continue under seats, balance, and spending caps.

**Data flow**: It receives usage so far and active requesters. It checks member seats, computes pending cost unless the user brings their own key, checks workspace balance, and checks spend caps. If blocked, it raises TurnParked with a reason.

**Call relations**: TurnEngine._model_round and _force_final call it before spending more model tokens. TurnEngine.run catches TurnParked and parks the turn.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 7 external calls (__init__, __init__, __init__, applicable_caps_absent, balance_absent, workspace_tx, audience_member).


##### `TurnEngine._stream_once`  (lines 2162–2404)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Performs one actual streamed model call and records its output as a DBOS step.

**Data flow**: It receives round input, builds the model request with tools and reasoning settings, streams events, publishes visible text deltas, gathers tool-call fragments, reasoning blocks, and usage, records metrics, and returns a StreamResult. If the stream errors, it returns the error and partial output in the result.

**Call relations**: TurnEngine._stream_recovering_overflow is its caller. Because this is a DBOS step, crash recovery replays the same model output without calling the model again.

*Call graph*: calls 2 internal fn (_parse_args, _total_usage); called by 1 (_stream_recovering_overflow); 14 external calls (__init__, __init__, __init__, __init__, __init__, Event, Lock, ensure_future, now, monotonic (+4 more)).


##### `TurnEngine._stream_once.flush`  (lines 2241–2250)

```
async def flush() -> None
```

**Purpose**: Flushes buffered streamed text to live listeners while respecting reply redaction.

**Data flow**: It takes text chunks accumulated in the enclosing _stream_once call, feeds them through the redactor, clears the buffer, and publishes visible text if any.

**Call relations**: Only TurnEngine._stream_once uses this helper, both when the buffer is large enough and when the stream ends.

*Call graph*: 1 external calls (__init__).


##### `TurnEngine._stream_once.pace`  (lines 2252–2257)

```
async def pace() -> None
```

**Purpose**: Periodically flushes streamed text so live users see progress even when chunks are small.

**Data flow**: It waits in short intervals until the stream stops, calling flush after each timeout. It returns when the stop event is set.

**Call relations**: TurnEngine._stream_once starts this as a background task during model streaming and stops it when streaming finishes.

*Call graph*: 1 external calls (wait_for).


##### `TurnEngine._publish_cost`  (lines 2406–2421)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes a live cost update showing the turn’s spend so far.

**Data flow**: It totals usage events, computes token count and micro-dollar cost, and sends a CostTick frame through the hub. Publish failures are swallowed by _publish.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish call it after model rounds so surfaces can show a live cost meter.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2423–2434)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skills currently present in the model’s conversation window.

**Data flow**: It scans messages through _loaded_skill_closures and reseeds the compaction tracker, including preloaded skills. It changes only the loaded-skills tracker.

**Call relations**: TurnEngine._model_round calls it around compaction, and _stream_recovering_overflow calls it after forced compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._bind_or_error`  (lines 2436–2462)

```
async def _bind_or_error(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> _BindResult
```

**Purpose**: Prepares a tool call for dispatch, turning ordinary binding problems into model-visible tool errors.

**Data flow**: It receives a context, call, and active requesters. It tries to bind the requester and adjusted context; on success it returns a resolved call, while validation or requester problems become a rejected call with error text.

**Call relations**: TurnEngine._model_round and run_intent call it before dispatch. Serious cancellation or missing-terminal cases are metered and re-raised.

*Call graph*: calls 2 internal fn (_bind_requester, _meter_dispatch); called by 2 (_model_round, run_intent); 4 external calls (__init__, __init__, __init__, monotonic).


##### `TurnEngine._dispatch`  (lines 2464–2470)

```
async def _dispatch(self, bound: _DispatchInput, usage_events: list[Usage] | None=None) -> ToolResultBlock
```

**Purpose**: Runs one prepared dispatch input and converts its stored dispatch result into a model-facing tool result block.

**Data flow**: It receives a bound or rejected tool call plus optional usage list. It runs the recovering DBOS dispatch step, then rehydrates images or text into a ToolResultBlock.

**Call relations**: TurnEngine._model_round calls this for each tool in a dispatch segment.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step_recovering); called by 1 (_model_round).


##### `TurnEngine._dispatch_result`  (lines 2472–2504)

```
async def _dispatch_result(self, result: DispatchResult) -> ToolResultBlock
```

**Purpose**: Converts a DispatchResult into the ToolResultBlock that is fed back to the model.

**Data flow**: It receives serialized result text, error state, activity flag, and optional image references. If images are present, it reads their blobs and builds image blocks; otherwise it builds a plain text result.

**Call relations**: TurnEngine._dispatch calls it after _dispatch_step_recovering returns.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._dispatch_step_recovering`  (lines 2506–2514)

```
async def _dispatch_step_recovering(self, bound: _DispatchInput, usage_events: list[Usage] | None) -> DispatchResult
```

**Purpose**: Keeps running dispatch steps until it gets a non-interrupted result accepted by the live execution.

**Data flow**: It receives one dispatch input and an optional usage accumulator. It calls _dispatch_step, passes the result to _accept_dispatch_result, and repeats if the result represented an interrupted checkpoint.

**Call relations**: TurnEngine._dispatch and run_intent use this around the DBOS dispatch step.

*Call graph*: calls 2 internal fn (_accept_dispatch_result, _dispatch_step); called by 2 (_dispatch, run_intent).


##### `TurnEngine._accept_dispatch_result`  (lines 2516–2527)

```
def _accept_dispatch_result(self, result: DispatchResult, usage_events: list[Usage] | None) -> bool
```

**Purpose**: Decides whether a dispatch result should be used, retried, or treated as a live cancellation.

**Data flow**: It receives a DispatchResult and optional usage list. It checks whether the step body ran live, adds usage when appropriate, removes the call from live tracking, raises cancellation for live interrupted work, and returns whether the result is acceptable.

**Call relations**: TurnEngine._dispatch_step_recovering calls it after every _dispatch_step result.

*Call graph*: called by 1 (_dispatch_step_recovering).


##### `TurnEngine._bind_requester`  (lines 2529–2574)

```
async def _bind_requester(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Applies the model’s requested-by message reference to a tool call, selecting the right member authority and sandbox where needed.

**Data flow**: It copies the tool input, validates and removes the requested-by marker when present, finds the member requester, asks optional factories for member-specific sandbox and subagent controls, and returns an updated context plus cleaned call.

**Call relations**: TurnEngine._bind_or_error calls this before a tool can be dispatched.

*Call graph*: called by 1 (_bind_or_error); 3 external calls (model_copy, replace, UUID).


##### `TurnEngine._offload`  (lines 2576–2601)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text content into the sandbox tool-output directory and returns a path the model can use instead of inline content.

**Data flow**: It receives a file name and content, ensures the output directory exists, writes the bytes, and returns a display path. On failure it logs, records a metric, and returns nothing.

**Call relations**: TurnEngine._dispatch_step uses it for large tool output. TurnEngine._model_round uses it to save partial model output after truncation.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._start_activity`  (lines 2603–2608)

```
def _start_activity(self, call: ToolUseBlock, goal: str) -> None
```

**Purpose**: Starts a background task that summarizes what a tool call is doing for live activity updates.

**Data flow**: It increments an activity sequence number, creates an async task for _generate_activity, and tracks the task until it finishes.

**Call relations**: TurnEngine._model_round and run_intent call it after a tool call is successfully resolved.

*Call graph*: calls 1 internal fn (_generate_activity); called by 2 (_model_round, run_intent); 1 external calls (create_task).


##### `TurnEngine._generate_activity`  (lines 2610–2622)

```
async def _generate_activity(self, call: ToolUseBlock, goal: str, sequence: int) -> None
```

**Purpose**: Generates and publishes a short human-friendly activity label for a tool call.

**Data flow**: It asks the activity summarizer for text, stores the label by call id, and publishes ready labels in original sequence order. It also mirrors subagent activity when relevant.

**Call relations**: TurnEngine._start_activity launches it as a background task.

*Call graph*: calls 2 internal fn (_publish, _publish_run); called by 1 (_start_activity); 1 external calls (__init__).


##### `TurnEngine._stop_activity`  (lines 2624–2626)

```
def _stop_activity(self) -> None
```

**Purpose**: Cancels any unfinished background activity-summary tasks.

**Data flow**: It loops over tracked activity tasks and cancels each one. It does not wait for new summaries.

**Call relations**: TurnEngine._publish_terminal calls it once the turn has ended, because live activity is no longer needed.

*Call graph*: called by 1 (_publish_terminal).


##### `TurnEngine._dispatch_step`  (lines 2629–2832)

```
async def _dispatch_step(self, bound: _DispatchInput) -> DispatchResult
```

**Purpose**: Runs one tool call as a recorded DBOS step, including validation, hooks, handler execution, output bounding, image offload, and metrics.

**Data flow**: It receives a bound or rejected dispatch input. Rejections become error results; valid calls pass through pre-tool hooks, run the tool handler with an idempotency key when needed, collect text/images and find usage, bound or offload large output, wall off untrusted text, fire post hooks, store images in blobs, and return a DispatchResult.

**Call relations**: TurnEngine._dispatch_step_recovering calls this. Because it is a DBOS step, completed tool calls replay without running again after a crash.

*Call graph*: calls 6 internal fn (_bounded_image, _offload, _pending_member_guidance, _redoes_on_replay, _bounded, _meter_dispatch); called by 1 (_dispatch_step_recovering); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, replace, monotonic (+3 more)).


##### `TurnEngine._redoes_on_replay`  (lines 2834–2843)

```
def _redoes_on_replay(self, name: str) -> bool
```

**Purpose**: Tells whether rerunning a tool after crash recovery would redo work in a way that should yield to pending member guidance.

**Data flow**: It receives a tool name, looks it up, and returns true for registered non-side-effecting tools. Unknown tools return false.

**Call relations**: TurnEngine._dispatch_step uses it during adoption replay before deciding to preempt a live re-executed call.

*Call graph*: called by 1 (_dispatch_step).


##### `TurnEngine._pending_member_guidance`  (lines 2845–2861)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether an unconsumed member message is waiting in the conversation.

**Data flow**: It queries inbound messages for the turn’s conversation looking for an unconsumed member admission and returns a boolean.

**Call relations**: TurnEngine._dispatch_step uses it during crash-recovery adoption to avoid redoing read-like work ahead of new user guidance.

*Call graph*: called by 1 (_dispatch_step); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 2863–2891)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before sending them back to the model.

**Data flow**: It receives an ImageBlock with base64 data, opens it, checks its dimensions, creates a thumbnail if needed, re-encodes it, and returns a new ImageBlock. If image processing fails, it logs and returns the original image.

**Call relations**: TurnEngine._dispatch_step calls it before writing tool images to blob storage.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 2893–2965)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Retries until the turn’s terminal state is durably written or a pending-arrival guard asks the model loop to continue.

**Data flow**: It receives the desired status, usage, answer or error, pending requests, created refs, and guard options. It repeatedly calls _commit_once with backoff, emits terminal metrics if it wrote the transition, records execution timing, logs the outcome, and returns the terminal frame or none.

**Call relations**: TurnEngine.run and run_intent call it for success and failure paths. It delegates the database work to _commit_once.

*Call graph*: calls 2 internal fn (_commit_once, exited); called by 2 (run, run_intent); 4 external calls (sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._publish_terminal`  (lines 2967–2970)

```
async def _publish_terminal(self, frame: TerminalFrame) -> None
```

**Purpose**: Publishes the final terminal frame to live listeners and stops activity summaries.

**Data flow**: It receives a TerminalFrame, publishes it on the turn stream, mirrors subagent status if needed, and cancels outstanding activity tasks.

**Call relations**: TurnEngine.run and run_intent call it after a terminal frame is committed.

*Call graph*: calls 3 internal fn (_publish, _publish_run, _stop_activity); called by 2 (run, run_intent); 1 external calls (__init__).


##### `TurnEngine._record_workspace_changes`  (lines 2972–2985)

```
async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None
```

**Purpose**: Scans and records sandbox workspace changes after the turn is finished enough that the user is no longer waiting on the answer.

**Data flow**: It receives accumulated change targets, builds a WorkspaceChangeRecorder with sandbox and conversation identifiers, and asks it to record changes.

**Call relations**: TurnEngine.run calls it after publishing the terminal for normal turns.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 2987–3101)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs the actual database transaction that bills usage and writes the terminal frame.

**Data flow**: It totals usage, optionally locks the conversation and refuses to close if arrivals are still pending, records billing, reads final cost, builds a TerminalFrame, and updates the turn if it is still non-terminal. If another path already ended it, it reads and returns the existing terminal.

**Call relations**: TurnEngine._commit wraps this with retry, metrics, and logging.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 11 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx, log (+1 more)).


##### `TurnEngine._park`  (lines 3103–3141)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Puts a turn into the parked state when it hits a spend, balance, or seat gate, preserving work so it can resume later.

**Data flow**: It marks the turn parked, records usage for the attempt, releases consumed inbound messages for future re-drain, then publishes a Parked frame and emits metrics if the update happened.

**Call relations**: TurnEngine.run catches TurnParked from _enforce_spend and calls this before re-raising.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 3143–3152)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes one live frame while ensuring live-publish failure never fails the turn.

**Data flow**: It receives a live frame, asks the hub to publish it for this turn, and logs any exception. It returns nothing.

**Call relations**: Many helpers use it for text deltas, cost ticks, replies, absorbed notices, parked notices, activity, and terminal frames.

*Call graph*: called by 8 (_absorb_arrivals, _generate_activity, _park, _publish_cost, _publish_terminal, _speak, _stream_closing_spans, run); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 3154–3176)

```
async def _publish_run(self, activity: str='', status: str='') -> None
```

**Purpose**: Mirrors subagent activity and status onto the root turn stream that user surfaces are watching.

**Data flow**: If lineage exists, it builds a SubagentActivity frame with parent/root context, optional activity text, and status, then publishes it to the root turn. Without lineage it does nothing.

**Call relations**: TurnEngine.run announces subagent start, _generate_activity announces subagent activity, and _publish_terminal announces subagent completion.

*Call graph*: called by 3 (_generate_activity, _publish_terminal, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 3178–3194)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops running sandbox commands when a user deliberately cancels the workflow.

**Data flow**: It asks the sandbox to stop commands and logs any failure. It does not change the already-durable cancellation result.

**Call relations**: TurnEngine.run and run_intent call it on DBOS workflow cancellation, not on ordinary executor preemption.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._bill_cancelled`  (lines 3196–3216)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for tokens already consumed by a cancelled or preempted normal turn.

**Data flow**: It totals usage and tries to record it in a database transaction. If billing fails, it logs and lets cancellation continue.

**Call relations**: TurnEngine.run calls it on DBOS cancellation and asyncio preemption paths.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 3218–3223)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution did not win ownership of the turn.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the situation. The result is either a republished committed terminal or nothing if another live execution is still running.

**Call relations**: TurnEngine.run and run_intent call it after _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 3225–3251)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed transcript, adding activity labels to tool result blocks before handing it to TranscriptRepair.

**Data flow**: It receives messages, answer, system prompt, and injected context. It copies tool result blocks that have recorded activity labels, then delegates to TranscriptRepair.persist_transcript.

**Call relations**: TurnEngine.run and run_intent call it after committing a terminal result.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_inbound`  (lines 3253–3258)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Persists only the inbound side of the conversation for non-normal exits.

**Data flow**: It receives optional arrival messages and an optional founding denial marker, creates a repair helper, and delegates to TranscriptRepair.persist_inbound.

**Call relations**: TurnEngine.run calls it on parked, cancelled, failed, or non-done terminal paths where assistant output should not be written as a normal answer.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


### Skill Assembly
These files define the skill runtime, persist user-created skills, choose relevant skills for a turn, and generate built-in skill guides from live system registries.

### `core/src/ufo/skills/__init__.py`

`other` · `import/package discovery`

This is an intentionally empty package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.skills` using normal import paths.

Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the workshop know that this drawer exists and can be opened by name. Without this file, depending on the Python version and project setup, imports involving `ufo.skills` might fail or behave differently.

There are no functions, classes, settings, or side effects here. Its value is structural: it helps organize the codebase and gives the `skills` area a clear place in the project’s module hierarchy.


### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading during turns`

A skill is a small folder that teaches the agent how to do something. Its main file, SKILL.md, contains a short metadata header plus the actual instructions. This file is the bridge between those folders on disk, the registry of skills available at runtime, the text shown to the agent, and the files copied into the sandbox.

The file first defines the shapes used by the system: a lightweight SkillCard for search and dependency lookup, a RuntimeSkill for the full parsed skill with instructions and assets, and LoadedSkill for a skill chosen for one conversation turn. It can parse skills from folders, including nested child skills. Child skills get path-like names such as parent/child, but they are not automatically pulled in; only an explicit depends list does that.

SkillRegistry is the main switchboard. It knows about built-in deploy skills and optional member-saved skills. When asked to load a skill, it expands the request into the full dependency closure, like making sure a recipe includes every ingredient it depends on. It then materializes those references into real RuntimeSkill objects.

Finally, the file formats the skill instructions for the model, avoids repeating instructions already in the conversation, builds a readable tree of loaded files, and sends the skill files to the sandbox. Without this file, the agent could not reliably find, verify, de-duplicate, or install the instructions and assets that skills provide.

#### Function details

##### `skill_root`  (lines 51–53)

```
def skill_root(name: str) -> str
```

**Purpose**: Builds the stable sandbox path where a named skill should live. This gives every skill a predictable home under $UFO_HOME/skills.

**Data flow**: It receives a skill name, joins it to the common skills root path, and returns that path as text. It does not read or change anything else.

**Call relations**: RuntimeSkill.root uses this helper whenever another part of the runtime needs to know where a skill's files will appear inside the sandbox.

*Call graph*: called by 1 (root).


##### `RuntimeSkill.all_files`  (lines 89–90)

```
def all_files(self) -> dict[str, bytes]
```

**Purpose**: Returns every file that belongs to a skill, including SKILL.md and any bundled asset files. This is the complete file set used for hashing and sandbox installation.

**Data flow**: It reads the skill's stored raw SKILL.md text and asset file list, turns SKILL.md into bytes, combines them into one path-to-bytes dictionary, and returns it.

**Call relations**: RuntimeSkill.content_digest uses this complete file map to make a stable fingerprint, and _wire_skill uses it to prepare files for transfer into the sandbox.

*Call graph*: called by 2 (content_digest, _wire_skill).


##### `RuntimeSkill.root`  (lines 92–93)

```
def root(self) -> str
```

**Purpose**: Returns the sandbox directory path for this particular skill. It is a small convenience method so callers do not have to know the path formula.

**Data flow**: It reads the skill's name, passes it to skill_root, and returns the resulting $UFO_HOME/skills/... path.

**Call relations**: _wire_skill calls this when it is making sandbox-safe file paths for a skill's files.

*Call graph*: calls 1 internal fn (skill_root); called by 1 (_wire_skill).


##### `RuntimeSkill.card`  (lines 95–103)

```
def card(self) -> SkillCard
```

**Purpose**: Creates the lightweight routing view of a full skill. The card keeps only the information needed for search and dependency resolution, not the instruction body.

**Data flow**: It reads the skill's name, description, dependencies, and target agents, then returns a new SkillCard with those fields.

**Call relations**: The registry uses cards so it can resolve skill names and dependencies without always loading or carrying the full instruction text.

*Call graph*: 1 external calls (__init__).


##### `RuntimeSkill.content_digest`  (lines 105–111)

```
def content_digest(self) -> str
```

**Purpose**: Computes a stable fingerprint for a skill's contents. The fingerprint changes when any file path or file content changes, which helps caches and sandboxes know exactly what version they have.

**Data flow**: It gathers all skill files, sorts them for repeatable ordering, hashes each path and each file's bytes, combines those hashes, and returns a sha256:... digest string.

**Call relations**: System skill bundles, _wire_skill, and sandbox loading use this digest as a compact identity for the exact skill contents.

*Call graph*: calls 1 internal fn (all_files); called by 1 (_wire_skill); 1 external calls (sha256).


##### `SystemSkillBundle.from_skills`  (lines 123–148)

```
def from_skills(cls, skills: Iterable[RuntimeSkill]) -> 'SystemSkillBundle'
```

**Purpose**: Packages a set of deploy-time system skills into a deterministic ZIP archive and manifest. This gives servers, terminals, and sandboxes one fixed bundle to share and cache.

**Data flow**: It receives RuntimeSkill objects, checks that duplicate names do not disagree in content, builds a JSON manifest with each skill's digest and file list, computes a bundle digest, writes the manifest and files into a ZIP archive, and returns a SystemSkillBundle.

**Call relations**: Startup and serving code call this when they need the system skill bundle for runtime initialization, shared surfaces, or sandbox image building.

*Call graph*: called by 4 (init_runtime, _mount_shared_surfaces, run, system_skill_bundle); 4 external calls (sha256, BytesIO, dumps, ZipFile).


##### `SystemSkillBundle._write`  (lines 151–154)

```
def _write(archive: zipfile.ZipFile, path: str, content: bytes) -> None
```

**Purpose**: Writes one file into a ZIP archive in a repeatable way. It fixes timestamps and permissions so the same inputs produce the same archive bytes.

**Data flow**: It receives an open ZIP archive, a path, and file bytes, creates a ZIP entry with a fixed date and normal file permissions, then writes the bytes into the archive.

**Call relations**: SystemSkillBundle.from_skills uses this helper for both the manifest and every skill file it places in the bundle.

*Call graph*: 2 external calls (writestr, ZipInfo).


##### `LoadedSkill.prompt_body`  (lines 167–177)

```
def prompt_body(self) -> str
```

**Purpose**: Turns one loaded skill into the text block shown to the agent. It labels whether the agent asked for the skill directly or it arrived as a dependency.

**Data flow**: It reads the loaded skill's name, dependency marker, and instruction body, builds a markdown header, appends the instructions, and returns the resulting text.

**Call relations**: loaded_context uses this when assembling the full skill text that will be put in front of the model.


##### `LoadedSkills.reseed`  (lines 203–222)

```
def reseed(self, loads: Iterable[tuple[LoadedRef, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the memory of which skill instructions are already in the model's conversation context. This prevents the system from paying to show the same long instructions again.

**Data flow**: It receives past resolved loads and optional preloaded skills, clears the current sets, records every skill now considered in context, and separately records which ones the agent directly asked for.

**Call relations**: It calls reset first to avoid stale state, then rebuilds the tracker from resolved load records rather than trusting loose text in the transcript.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 224–229)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of skills the agent directly requested, then clears the tracker. This is useful at a boundary where old instruction bodies are dropped but the system wants to remember what should be re-loadable later.

**Data flow**: It reads the asked_for set, sorts it into a tuple, clears both tracking sets, and returns the tuple of names.

**Call relations**: It relies on reset for clearing, just like reseed does, so the tracker has one consistent way to empty itself.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 231–233)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered loaded-skill state. After this, the tracker behaves as if no skill instructions are currently in context.

**Data flow**: It empties the in_context set and the asked_for set. It returns nothing.

**Call relations**: LoadedSkills.reseed calls it before rebuilding state, and LoadedSkills.drain calls it after handing names across a boundary.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 236–242)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates the metadata header from the markdown instructions inside SKILL.md. The metadata is YAML, meaning a simple structured text format, and the body is the workflow the agent reads.

**Data flow**: It receives raw SKILL.md text, checks that it starts with the expected --- fence, finds the closing fence, and returns the metadata text and body text. If the fences are missing, it raises an error.

**Call relations**: parse_skill_content calls this before interpreting a skill, so malformed skill files fail early and clearly.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 245–250)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate subfolders that are themselves skills. A child skill is recognized by having its own SKILL.md.

**Data flow**: It receives a directory path, looks at its direct children, keeps only child directories containing SKILL.md, sorts them, and returns the list.

**Call relations**: parse_skill uses it to keep child skill files out of the parent's asset bundle, and discover_skills uses it to recursively register child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 253–290)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Builds a RuntimeSkill from files already held in memory. This lets the system validate a saved or sandbox-read skill the same way it validates one from disk.

**Data flow**: It receives a directory name, a map of file paths to bytes, and optional registry naming details. It reads SKILL.md, splits and parses the YAML metadata, checks that the declared name matches the folder name, extracts description, dependencies, agent targeting, and asset files, then returns a RuntimeSkill.

**Call relations**: parse_skill gathers files from disk and hands them here. This function is the central parser that turns raw bytes into the runtime object the rest of the skill system understands.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 293–302)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill directory from disk and parses it. It treats nested child skill folders as separate skills, not as ordinary assets of the parent.

**Data flow**: It receives a filesystem path, finds child skill directories, reads all ordinary files that belong to the current skill, and passes those bytes to parse_skill_content. It returns one RuntimeSkill.

**Call relations**: discover_skills calls this for each skill folder it visits while flattening a tree of parent and child skills into registry entries.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 305–323)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, returning them in one flat name-to-skill map. This turns a folder tree into the registry names the runtime can load.

**Data flow**: It receives a skill directory and optional parent naming information, parses the current skill, records it under its registry name, then recursively discovers each immediate child skill under a path-like name such as parent/child.

**Call relations**: _load_core_skills uses this when reading the built-in skills. The function also calls parse_skill for the current folder and _child_skill_dirs to decide what children to recurse into.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 326–333)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads all built-in skills shipped beside this runtime file. These core skills define the baseline abilities and house style available in every deployment.

**Data flow**: It receives the root directory, scans visible child directories, discovers skills inside each one, merges the results into a dictionary, and returns that dictionary by skill name.

**Call relations**: The module calls this during import to build CORE_SKILLS_BY_NAME, which then feeds the default CORE_SKILL_REGISTRY.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.__post_init__`  (lines 360–362)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the default set of bundled skill names after a SkillRegistry is created. If no explicit bundled set is provided, every deploy skill is treated as bundled.

**Data flow**: It checks whether bundled_names is missing. If so, it stores a frozen set of the current deploy skill names on the registry.

**Call relations**: This runs automatically after SkillRegistry construction, including registries created by merged_with and with_member.


##### `SkillRegistry.named`  (lines 364–368)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a deploy skill by name and returns the full RuntimeSkill. It gives a helpful error if the name is not known.

**Data flow**: It receives a name, checks the deploy-skill dictionary, and returns the matching RuntimeSkill. If the name is absent, it asks _unknown to build an error with close-name suggestions.

**Call relations**: This is the direct lookup path for code that specifically needs a deploy skill, rather than the mixed deploy/member card lookup used during closure resolution.

*Call graph*: calls 1 internal fn (_unknown).


##### `SkillRegistry._unknown`  (lines 370–373)

```
def _unknown(self, name: str) -> ValueError
```

**Purpose**: Creates a clear error for an unknown skill name, optionally suggesting similar known names. This helps users recover from typos.

**Data flow**: It receives the missing name, gathers all known names, finds a few close matches, and returns a ValueError message containing the missing name and any suggestions.

**Call relations**: SkillRegistry.named and SkillRegistry._card use this when a requested skill cannot be found.

*Call graph*: calls 1 internal fn (known_names); called by 2 (_card, named); 1 external calls (get_close_matches).


##### `SkillRegistry._card`  (lines 375–382)

```
def _card(self, name: str) -> SkillCard
```

**Purpose**: Gets the lightweight routing card for a skill name, whether it is a deploy skill or a member-saved skill. This gives dependency resolution one common shape to work with.

**Data flow**: It receives a name, first checks deploy skills and converts a found RuntimeSkill to a card, then checks member cards. If neither exists, it raises the unknown-skill error.

**Call relations**: SkillRegistry.closure and its internal add step call this whenever they need to resolve a requested skill or dependency name.

*Call graph*: calls 1 internal fn (_unknown); called by 2 (closure, add).


##### `SkillRegistry.known_names`  (lines 384–387)

```
def known_names(self) -> frozenset[str]
```

**Purpose**: Returns every skill name this registry can currently resolve. That includes deploy skills and member-saved skill cards.

**Data flow**: It reads the deploy-skill names and member-card names, combines them into one frozen set, and returns it.

**Call relations**: SkillRegistry._unknown uses this list to produce typo suggestions and to decide whether any close matches exist.

*Call graph*: called by 1 (_unknown).


##### `SkillRegistry.all_cards`  (lines 389–394)

```
def all_cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: Returns the searchable card view of every loadable skill. Search and selection can use this without loading full instruction bodies.

**Data flow**: It converts deploy skills to cards, appends member cards, and returns them as one tuple.

**Call relations**: This method supports the skill-selection side of the system: it gives search a compact catalog of names, descriptions, dependencies, and targeting.


##### `SkillRegistry.bundled_skills`  (lines 396–399)

```
def bundled_skills(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Returns the deploy skills that are part of the static bundle. These are the skills expected to be shipped with the terminal archive or sandbox image.

**Data flow**: It reads the bundled_names set, filters the deploy-skill dictionary to names in that set, and returns the matching RuntimeSkill objects as a tuple.

**Call relations**: Serving code uses this when mounting shared runtime surfaces, so the sandbox and clients can rely on the same bundled skill contents.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.closure`  (lines 401–425)

```
def closure(self, *names: str) -> tuple[LoadedRef, ...]
```

**Purpose**: Expands requested skill names into the full ordered set of skills that must be loaded, including dependencies. A dependency is another skill explicitly named in metadata.depends.

**Data flow**: It receives one or more requested names, creates direct LoadedRef entries for each unique requested skill, then walks each dependency chain once, recording which skill pulled each dependency. It returns the ordered tuple of LoadedRef objects.

**Call relations**: The engine calls this when figuring out what a load request means. It uses _card for every name so deploy and member skills are resolved uniformly.

*Call graph*: calls 1 internal fn (_card); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 415–420)

```
def add(card: SkillCard, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency and its own dependencies to the closure, while avoiding duplicates and cycles. It is the recursive worker inside closure.

**Data flow**: It receives a SkillCard and the name of the skill that required it. If the skill is already recorded, it stops; otherwise it records a LoadedRef and repeats the process for each dependency listed on that card.

**Call relations**: SkillRegistry.closure calls this while walking dependencies. It hands off each dependency name to _card so the registry can find the correct routing card.

*Call graph*: calls 1 internal fn (_card); 1 external calls (__init__).


##### `SkillRegistry.materialize`  (lines 427–449)

```
async def materialize(self, refs: Sequence[LoadedRef]) -> tuple[LoadedSkill, ...]
```

**Purpose**: Turns resolved skill references into full loaded skills with instruction bodies and files. This is where member-saved skills are actually read, instead of just represented by cards.

**Data flow**: It receives a sequence of LoadedRef objects. For each one, it gets the RuntimeSkill directly from deploy skills or asks the async materializer to load a member skill, checks that the returned name matches, wraps it as a LoadedSkill with dependency and bundled flags, and returns the tuple.

**Call relations**: After closure has decided what names are needed, materialize supplies the real content that loaded_context and sandbox installation use.

*Call graph*: 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 451–460)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill index inserted into prompts: top-level deploy skill names and descriptions. It intentionally leaves out child skills and member skills.

**Data flow**: It reads deploy skills in registry order, keeps only those with no parent, and returns name-description pairs.

**Call relations**: Prompt-building code calls this when rendering the {{skill_index}} slot and when creating skill-selection prompts.

*Call graph*: called by 2 (_prompt_skill_index, prompt_index).


##### `SkillRegistry.merged_with`  (lines 462–483)

```
def merged_with(self, generated: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Returns a new registry with generated deploy-controlled skills appended. If a generated or member skill would shadow a deploy skill name, it is refused and logged.

**Data flow**: It copies the deploy skill map, adds each generated skill whose name is not already taken, copies member cards, removes any member card now hidden by deploy skills, and returns a new SkillRegistry with the same materializer and bundled set.

**Call relations**: This is used when a turn composes the base registry with generated skills such as setup or spawn-catalog skills, while preserving the rule that deploy-controlled skills win name conflicts.

*Call graph*: 2 external calls (__init__, log).


##### `SkillRegistry.with_member`  (lines 485–504)

```
def with_member(self, cards: Sequence[SkillCard], materialize: SkillMaterializer) -> 'SkillRegistry'
```

**Purpose**: Returns a new registry that includes the bound agent's saved member skills. Member skills are allowed to be loadable, but they may not replace deploy skills with the same name.

**Data flow**: It receives member SkillCard objects and a materializer function, filters out any card whose name collides with a deploy skill while logging that refusal, then returns a new SkillRegistry containing those member cards.

**Call relations**: Turn setup uses this shape so closure can resolve both deploy and member skill names, while materialize later knows how to fetch the member skill bodies.

*Call graph*: 2 external calls (__init__, log).


##### `_loaded_tree`  (lines 510–528)

```
def _loaded_tree(loaded: Sequence[LoadedSkill]) -> str
```

**Purpose**: Builds a compact text tree of every file included in a resolved skill load. This lets the agent see where files are available without dumping their contents.

**Data flow**: It receives LoadedSkill entries, gathers every loaded file path under each skill name, sorts them, adds directory lines only once, and returns an indented tree rooted at $UFO_HOME/skills.

**Call relations**: loaded_context appends this tree after the instruction blocks so the model can refer to bundled files by path.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 531–544)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Creates the full text shown to the model for one skill load. It includes new skill instructions, a note for instructions already present, and a file tree for everything loaded.

**Data flow**: It receives loaded skills and a set of skill names already in context. It renders prompt bodies only for new skills, adds one “already loaded” note for repeated ones, appends the loaded file tree, and returns the combined text.

**Call relations**: Both normal load_skill behavior and subagent preloading use this so skills look the same whether loaded by a tool result or preloaded into a child agent prompt.

*Call graph*: calls 1 internal fn (_loaded_tree).


##### `_wire_skill`  (lines 547–554)

```
def _wire_skill(skill: RuntimeSkill) -> dict[str, object]
```

**Purpose**: Converts a RuntimeSkill into the wire format expected by the sandbox loader. “Wire format” here means a safe dictionary of file paths and base64-encoded bytes suitable for transport.

**Data flow**: It receives a RuntimeSkill, gathers all files, makes each path safe relative to the skill root, base64-encodes each file's bytes, computes the skill digest, and returns a dictionary containing the digest and encoded files.

**Call relations**: install_skill and load_skills call this before asking Sandbox.load_skills to place user-provided or non-bundled skill files in the runtime directory.

*Call graph*: calls 3 internal fn (all_files, content_digest, root); called by 2 (install_skill, load_skills); 2 external calls (urlsafe_b64encode, contained_relative).


##### `install_skill`  (lines 557–561)

```
async def install_skill(sandbox: Sandbox, skill: RuntimeSkill) -> None
```

**Purpose**: Installs one materialized skill into the sandbox under $UFO_HOME/skills. This is the single-skill version of sandbox loading.

**Data flow**: It receives a Sandbox and a RuntimeSkill, converts the skill with _wire_skill, sends it to the sandbox as a user skill, then checks that the sandbox returned a path for that skill. If not, it raises an error.

**Call relations**: It hands the prepared skill package to Sandbox.load_skills, which performs the actual sandbox-side loading.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


##### `load_skills`  (lines 564–571)

```
async def load_skills(sandbox: Sandbox, loaded: Sequence[LoadedSkill]) -> None
```

**Purpose**: Installs a whole resolved skill load into the sandbox. Bundled deploy skills are identified by digest, while non-bundled skills are sent with their file contents.

**Data flow**: It receives a Sandbox and LoadedSkill entries, separates bundled skills from user/network-loaded ones, prepares non-bundled skills with _wire_skill, calls Sandbox.load_skills with both groups, then verifies every requested skill got a returned root path.

**Call relations**: This is the final handoff after closure and materialization: the registry decides what skills are needed, and this function makes those files available inside the sandbox.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and skill loading`

A “skill” here is a small bundle of files, usually including instructions in `SKILL.md`, that an agent can load and use later. This file is the workspace’s skill cabinet: it saves each skill under one name, lists what is available, loads the files back, and deletes skills when asked.

The important safety rule is that saving is not just “write this over whatever is there.” Each saved skill has a `generation`, which works like a version sticker on a document. If someone reads version A, but another writer saves version B before they submit changes, this file refuses the older save instead of silently overwriting the newer work.

It also keeps a compact “card” for each skill: the name, description, dependencies, target agents, and whether it is pinned. Agents can read these cards quickly without unpacking the full stored files.

Skill file bytes are stored in the database as base64 text, which is a safe text form for raw bytes. When loading, the file turns that text back into bytes and parses the skill again. Bulk loading is tolerant: if one stored skill is corrupt, it logs the problem and keeps loading the others. But loading one named skill fails loudly, because silently pretending it does not exist would hide data damage.

#### Function details

##### `_save_lock_key`  (lines 54–56)

```
def _save_lock_key(workspace_id: UUID) -> int
```

**Purpose**: This helper turns a workspace ID into a stable number used as a database lock key. The lock makes saves for the same workspace line up instead of racing each other.

**Data flow**: It receives a workspace identifier → hashes its text form with SHA-256, a standard way to make a fixed-size fingerprint → takes part of that fingerprint and turns it into an integer. The result is only a lock label; it does not reveal or change the workspace ID.

**Call relations**: When `UserSkillStore.save` is about to write a skill, it asks this helper for the workspace’s lock key. On PostgreSQL, that key is handed to the database so only one save for that workspace can pass through the critical section at a time.

*Call graph*: called by 1 (save); 1 external calls (sha256).


##### `UserSkillStore.save`  (lines 123–238)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str], pinned: bool=False, generation: UUID | None=None) -> RuntimeSkill
```

**Purpose**: This saves a new or edited workspace skill after checking that it is valid and safe to store. It prevents bad skill names, overwriting someone else’s newer edit, shadowing built-in skills, exceeding workspace limits, and pinning too many skills.

**Data flow**: It receives a skill name, a set of file paths with byte contents, the names already used by built-in or pack skills, a pinned flag, and optionally the generation that the caller previously read → checks the name, parses the files into a real runtime skill, encodes the files into database-safe text, computes a digest fingerprint, and opens a database transaction → compares the caller’s generation with the stored row, checks workspace and pinned-skill limits, then inserts or updates the row with a fresh generation → returns the parsed runtime skill that was saved. The database row is changed only if all checks pass.

**Call relations**: This is the main write path for the store. It calls `_save_lock_key` before writing so concurrent saves are serialized where needed. It calls `_count` when adding a brand-new skill, and `_pinned_count` when a save would add another pinned skill. If any safety check fails, it raises a specific error so the caller can explain the problem rather than corrupting or overwriting data.

*Call graph*: calls 3 internal fn (_count, _pinned_count, _save_lock_key); 16 external calls (__init__, __init__, __init__, __init__, __init__, __init__, b64encode, sha256, dumps, cast (+6 more)).


##### `UserSkillStore.cards`  (lines 240–281)

```
async def cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: This returns lightweight routing cards for every saved skill in the current workspace. Agents can use these cards to decide what skills are available without loading every file bundle.

**Data flow**: It reads the current workspace from the agent context → queries the database for each saved skill’s name, description, dependencies, agent list, and pinned flag → skips rows with an empty description because those cannot form useful routing cards → turns the remaining rows into `SkillCard` objects and returns them as an immutable tuple.

**Call relations**: This is a read path used when the system wants the workspace’s skill catalog. It does not call the heavier file-loading paths, so a corrupt stored bundle can still leave its card visible unless the description is missing. JSON text stored in the database is decoded back into dependency and agent lists before each card is returned.

*Call graph*: 4 external calls (__init__, loads, select, agent_current).


##### `UserSkillStore.listing`  (lines 283–307)

```
async def listing(self) -> tuple[SkillListing, ...]
```

**Purpose**: This returns the simple list shown to users or tools: each skill’s name, description, and pinned status. It is meant for browsing, not for loading the skill’s full contents.

**Data flow**: It gets the current workspace → reads matching rows from the database in name order → ignores rows with no description → wraps each remaining row in a `SkillListing` object → returns the listings as a tuple.

**Call relations**: This is the display-oriented sibling of `cards`. Where `cards` builds objects for agent routing, `listing` builds smaller objects for a human-facing or object-list view.

*Call graph*: 3 external calls (__init__, select, agent_current).


##### `UserSkillStore.record`  (lines 309–340)

```
async def record(self, name: str) -> SkillRecord | None
```

**Purpose**: This fetches the full saved record for one skill, including its files, description, generation, pin state, and timestamps. It is the read path a caller uses before editing, because the generation it returns is needed for a safe later save.

**Data flow**: It receives a skill name → looks up that name in the current workspace → if there is no row, returns `None` → otherwise validates the stored JSON bundle, decodes each base64 file back into bytes, and builds a `SkillRecord` containing the files and metadata. If the stored bundle is corrupt, the error is allowed to surface.

**Call relations**: This supports detailed object reads. Its returned generation connects directly to `UserSkillStore.save`: callers pass that generation back when editing so the save can detect whether someone else changed the skill in between.

*Call graph*: 4 external calls (__init__, b64decode, select, agent_current).


##### `UserSkillStore.materialize`  (lines 342–349)

```
async def materialize(self, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one named skill into the runtime form that an agent can actually use. It is for going from stored files back to an executable or loadable skill object.

**Data flow**: It receives a skill name → asks `files` for that skill’s stored file bytes → if there are no files, returns `None` → otherwise parses the files as skill content and returns a `RuntimeSkill`. The database is not changed.

**Call relations**: This is a small bridge between raw storage and runtime use. It delegates the database read and decoding to `UserSkillStore.files`, then hands the resulting files to the shared skill parser so the same validation rules are used when loading as when saving.

*Call graph*: calls 1 internal fn (files); 1 external calls (parse_skill_content).


##### `UserSkillStore.materialize_all`  (lines 351–379)

```
async def materialize_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads every saved skill in the workspace into runtime form. It is useful when an agent needs all workspace skills, while making sure one damaged row does not stop the rest from loading.

**Data flow**: It reads the current workspace → queries every saved skill’s name and stored content → for each row, validates the stored JSON, decodes file contents from base64, and parses the skill → collects successfully parsed skills into a tuple. If one row fails to decode or parse, it logs a warning and continues with the next row.

**Call relations**: This is the bulk-loading path. Unlike `materialize`, which is strict for one named skill, this function is deliberately forgiving so a single bad saved skill does not hide all the healthy workspace skills from the agent.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 381–396)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: This returns the raw files for one saved skill as normal byte contents. It is useful when another part of the system wants the stored bundle without immediately turning it into a runtime skill.

**Data flow**: It receives a skill name → reads the matching stored content for the current workspace → returns `None` if no row exists → otherwise validates the stored JSON and decodes each base64 string back into bytes. The output is a dictionary from file path to file bytes.

**Call relations**: This is the lower-level read helper used by `UserSkillStore.materialize`. It stops at recovering the files, while `materialize` takes the next step and parses those files into a runtime skill.

*Call graph*: called by 1 (materialize); 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 398–420)

```
async def delete(self, name: str) -> None
```

**Purpose**: This removes one saved skill from the workspace and also cleans up its search/index data if an index is available. The order is careful so a crash does not leave hard-to-clean leftover indexed chunks.

**Data flow**: It receives a skill name → gets the current workspace → if an index service exists, first marks the skill’s indexed digest as stale in the database, then asks the index to delete entries for that skill → finally deletes the skill row from the database. After it succeeds, the skill is no longer saved for the workspace.

**Call relations**: This is the store’s delete path. It coordinates with the optional index subsystem by creating an `IndexScope` for the skill name, so the external searchable copy is pruned before the database row disappears.

*Call graph*: 4 external calls (__init__, delete, update, agent_current).


##### `UserSkillStore._count`  (lines 422–430)

```
async def _count(self, connection: AsyncConnection) -> int
```

**Purpose**: This counts how many user-created skills are currently saved in the workspace. It is used to enforce the maximum number of saved skills.

**Data flow**: It receives an open database connection → reads the current workspace from context → asks the database to count rows in `user_skill` for that workspace → returns the count as an integer. It does not change anything.

**Call relations**: `UserSkillStore.save` calls this only when creating a new skill. That lets `save` refuse the insert if the workspace has already reached its skill limit.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


##### `UserSkillStore._pinned_count`  (lines 432–444)

```
async def _pinned_count(self, connection: AsyncConnection, excluding: str) -> int
```

**Purpose**: This counts pinned skills in the workspace, excluding one named skill. It helps decide whether pinning another skill would go over the allowed pinned-skill limit.

**Data flow**: It receives an open database connection and a skill name to exclude → reads the current workspace → counts rows in that workspace where `pinned` is true and the name is not the excluded one → returns that number. It only reads the database.

**Call relations**: `UserSkillStore.save` calls this when a save would make a skill pinned and it was not already pinned. Excluding the current skill means re-saving an already selected skill does not incorrectly count against itself.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


### `core/src/ufo/skills/selection.py`

`domain_logic` · `request handling`

An agent may have many saved skills, each with a name, description, and sometimes a “pinned” flag meaning it should be favored. The model needs to know these skills exist, but there is limited room in the prompt. This file is the rulebook for fitting those skill cards into that limited space.

It uses two places. If the saved skill list is small, it “folds” into the normal system prompt beside built-in skills, so the model sees everything in the usual skill index. If the list is too large, it is moved into a separate saved-skills block in the turn message. That block has its own size limit.

The file follows a ladder. First, pinned skills get full name-and-description lines. If the whole catalog fits, all skills get full lines. If not, it chooses a small top set using simple word matching against the user’s current query, gives those full descriptions, and still lists the remaining skills by name. If even names do not fit, it drops lines from the end and adds a note saying how many were left out and that skill_search can find them.

Everything here is pure calculation: no files, network, database, or random choices. That matters because this runs every turn and must be predictable and cheap.

#### Function details

##### `_query_terms`  (lines 35–42)

```
def _query_terms(query: str) -> tuple[str, ...]
```

**Purpose**: Turns a user query into a clean list of searchable words. It removes punctuation-like separators, ignores very short words, removes duplicates, and caps very long input so a pasted wall of text cannot make scoring too expensive.

**Data flow**: It takes a query string in. It reads only the first allowed number of characters, lowercases it in a language-safe way, splits it into word-like pieces, keeps pieces long enough to matter, preserves first-seen order while removing repeats, and returns those terms as a tuple.

**Call relations**: This is the shared first step for word matching. lexical_score uses it when scoring one card, and select_top_k uses it once before ranking many cards, so both paths interpret the query the same way.

*Call graph*: called by 2 (lexical_score, select_top_k).


##### `_term_hits`  (lines 45–47)

```
def _term_hits(terms: Sequence[str], card: SkillCard) -> int
```

**Purpose**: Counts how many query terms appear in a skill card’s name or description. It is a small scoring helper for deciding whether a card matches the current request.

**Data flow**: It takes already-prepared query terms and one SkillCard. It combines the card name and description into one lowercase search string, checks each term against it, and returns the number of distinct terms found.

**Call relations**: lexical_score calls this after preparing terms. select_top_k uses the same idea through its ranking key, so the visible top skills are based on this simple “how many words matched” measure.

*Call graph*: called by 1 (lexical_score).


##### `lexical_score`  (lines 50–55)

```
def lexical_score(query: str, card: SkillCard) -> int
```

**Purpose**: Gives one skill card a simple match score for a query. Someone would use it to ask, “How many meaningful words from this request show up in this saved skill?”

**Data flow**: It takes a query and a SkillCard. It converts the query into cleaned search terms with _query_terms, counts matches in the card through _term_hits, and returns that count as an integer score.

**Call relations**: This is the public, single-card version of the matching logic. It delegates query cleanup to _query_terms and matching to _term_hits, while select_top_k uses the same style of matching to choose several cards for display.

*Call graph*: calls 2 internal fn (_query_terms, _term_hits).


##### `select_top_k`  (lines 58–66)

```
def select_top_k(query: str, cards: Sequence[SkillCard]) -> tuple[SkillCard, ...]
```

**Purpose**: Chooses the best few unpinned skills for the current query. It is used when there are too many saved skills to show every description, so only the most relevant unpinned cards get full detail.

**Data flow**: It takes the query and a sequence of SkillCards. It prepares the query terms once, removes pinned cards from consideration, sorts the remaining cards by how many terms appear in each card, keeps the original order for ties, and returns up to the configured top count.

**Call relations**: member_visibility calls this only after deciding the full catalog is too large for the saved-skills block. The chosen cards then receive full name-and-description lines, while other unpinned cards may be shown only by name.

*Call graph*: calls 1 internal fn (_query_terms); called by 1 (member_visibility).


##### `skill_line`  (lines 69–71)

```
def skill_line(card: SkillCard) -> str
```

**Purpose**: Formats one skill card as a prompt-friendly line. It includes the skill name and description, but cuts the line off at a fixed length so one long description cannot consume all the space.

**Data flow**: It takes a SkillCard. It builds text like “- name: description”, trims it to the maximum allowed line length, and returns that string.

**Call relations**: This is the common renderer for full skill entries. folds_into_prompt, prompt_index, catalog_fits, and member_visibility all call it so size checks and final display are based on the same text.

*Call graph*: called by 4 (catalog_fits, folds_into_prompt, member_visibility, prompt_index).


##### `folds_into_prompt`  (lines 74–78)

```
def folds_into_prompt(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Answers whether all member saved skills are small enough to be placed directly in the system prompt. This is the first placement decision: small lists stay with the normal skill index; larger lists move elsewhere.

**Data flow**: It takes a sequence of SkillCards. It renders each card with skill_line, measures the combined size using _joined_size, compares that to the prompt-fold limit, and returns true or false.

**Call relations**: prompt_index calls this before deciding whether to add member cards to the system skill index. It relies on skill_line and _joined_size so the decision matches what would actually be shown.

*Call graph*: calls 2 internal fn (_joined_size, skill_line); called by 1 (prompt_index).


##### `prompt_index`  (lines 81–93)

```
def prompt_index(registry: SkillRegistry) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill index entries that belong in the system prompt for a turn. It always includes deployed, built-in skills, and adds member saved skills only when the saved list is small enough.

**Data flow**: It takes a SkillRegistry, reads its member cards, and asks folds_into_prompt whether they fit. If they do not fit, it returns the registry’s normal deployed-skill index. If they do fit, it appends each member skill with the same capped description used for the size check.

**Call relations**: This function is the bridge between the skill registry and the prompt. It calls SkillRegistry.index for the deploy-tier skills, uses folds_into_prompt for the placement decision, and uses skill_line so member entries match the measured text.

*Call graph*: calls 3 internal fn (index, folds_into_prompt, skill_line).


##### `catalog_fits`  (lines 96–99)

```
def catalog_fits(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Checks whether every saved skill can be shown as a full line inside the saved-skills block. It is used to decide whether retrieval ranking is needed at all.

**Data flow**: It takes a sequence of SkillCards. It renders all cards with skill_line, measures the wrapped block size with _block_size, compares that to the block limit, and returns true or false.

**Call relations**: This standalone check mirrors one of the decisions made inside member_visibility. It uses skill_line and _block_size to answer, “Can we show the whole catalog fully?”

*Call graph*: calls 2 internal fn (_block_size, skill_line).


##### `member_visibility`  (lines 113–146)

```
def member_visibility(query: str, cards: Sequence[SkillCard]) -> MemberVisibility
```

**Purpose**: Makes the full saved-skill visibility decision for one turn. It decides whether skills fold into the system prompt, whether the full catalog fits in the saved-skills block, and what block text should be sent if a block is needed.

**Data flow**: It takes the current query and the saved skill cards. It renders each full line once, checks the system-prompt budget with _joined_size, checks the saved-block budget with _block_size, and returns a MemberVisibility result. If the cards are folded or absent, the block is empty. If the full catalog fits, it renders pinned cards first and then the rest. If the catalog is too large, it calls select_top_k, shows pinned and selected cards with descriptions, lists remaining unpinned cards by name, trims from the end until the block fits, and may add a final count of dropped skills.

**Call relations**: member_block calls this as the main decision-maker. Inside, member_visibility coordinates the small helpers: skill_line creates display lines, _joined_size and _block_size measure budgets, select_top_k chooses relevant full-description cards when needed, and _render wraps the final lines in saved-skills tags.

*Call graph*: calls 5 internal fn (_block_size, _joined_size, _render, select_top_k, skill_line); called by 1 (member_block); 1 external calls (__init__).


##### `member_block`  (lines 149–157)

```
def member_block(query: str, cards: Sequence[SkillCard]) -> str
```

**Purpose**: Returns just the saved-skills text block that should be attached to a turn message. It is the simple public entry point for callers that only need the rendered block, not the extra decision details.

**Data flow**: It takes the query and saved skill cards. It asks member_visibility to make all placement and budget decisions, then returns the block field from that result, which may be an empty string if no separate block is needed.

**Call relations**: This function sits on top of member_visibility. It does not repeat the logic; it hands off the real work and exposes the final text for the turn message.

*Call graph*: calls 1 internal fn (member_visibility).


##### `_joined_size`  (lines 163–164)

```
def _joined_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how many characters a set of lines will take when joined with newline characters. It is a tiny budgeting helper used before wrapping text in any tags.

**Data flow**: It takes a sequence of already-rendered strings. It adds each line’s length plus one newline character, subtracts the extra newline at the end when there is at least one line, and returns the total size.

**Call relations**: folds_into_prompt and member_visibility use this to check the system-prompt budget. _block_size also calls it as the base measurement before adding saved-skills wrapper text.

*Call graph*: called by 3 (_block_size, folds_into_prompt, member_visibility).


##### `_block_size`  (lines 167–168)

```
def _block_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how large a saved-skills block would be after adding its opening and closing tags. This keeps the block under the fixed turn-message budget.

**Data flow**: It takes already-rendered skill lines. It asks _joined_size for the body size, adds the characters needed for the saved-skills wrapper tags and spacing, and returns the total block size.

**Call relations**: catalog_fits and member_visibility use this to decide whether the whole catalog can be shown in the saved-skills block. It builds directly on _joined_size so the measuring rules stay consistent.

*Call graph*: calls 1 internal fn (_joined_size); called by 2 (catalog_fits, member_visibility).


##### `_render`  (lines 171–172)

```
def _render(lines: tuple[str, ...]) -> str
```

**Purpose**: Wraps saved-skill lines in the saved-skills opening and closing tags. This creates the exact block text the model will see.

**Data flow**: It takes a tuple of lines that have already been chosen and ordered. It places the opening tag before them, the closing tag after them, joins everything with newlines, and returns one string.

**Call relations**: member_visibility calls this at the end of the block-building path. By keeping rendering here, the larger visibility function can focus on choosing and trimming lines before handing them off for final formatting.

*Call graph*: called by 1 (member_visibility).


### `core/src/ufo/loop/spawn_catalog.py`

`domain_logic` · `per-turn skill assembly`

This file solves a simple but important problem: an agent needs to know who it can delegate work to, and what information to send. Instead of keeping a separate, hand-written list that could become stale, this file builds that list fresh each turn.

It combines two kinds of spawn targets. First are subagent profiles, which are fixed definitions loaded into the running system. Second are workspace agents, which are rows stored in the database and can differ by workspace, owner, and permissions. The file checks who the current member is, whether they are an admin, and then reads only the workspace agents that member is allowed to spawn. Admins can see every active agent in the workspace; non-admins see their own active agents.

For each target, it describes the payload, meaning the named pieces of input that must or may be provided. If a workspace agent has the same name as a profile, the workspace agent is shown with an `agent:` prefix, because that is the exact name needed to spawn it without ambiguity.

The result is returned as a `RuntimeSkill`: a piece of instructions available to the agent during the turn. Like a current menu in a restaurant, it shows what can actually be ordered right now, not what used to exist at startup.

#### Function details

##### `_profile_payload`  (lines 29–36)

```
def _profile_payload(profile: SubagentProfile) -> str
```

**Purpose**: This helper turns a subagent profile's input definition into a short human-readable list of payload fields. It marks which fields are optional so an agent can build the right input before spawning that profile.

**Data flow**: It receives a `SubagentProfile`, reads the fields from its input model, and sorts them by name. If there are no fields, it returns `(no fields)`; otherwise it returns text such as `` `task` `` or `` `note` (optional) `` for each field.

**Call relations**: It is used by `spawn_catalog_skill` while building the table of spawn targets. For every registered profile, `spawn_catalog_skill` asks this helper to describe that profile's expected payload.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `_schema_payload`  (lines 39–49)

```
def _schema_payload(schema: Mapping[str, object] | None) -> str
```

**Purpose**: This helper turns a workspace agent's stored input schema into a short human-readable list of payload fields. A schema is a structured description of expected input, and this function makes it readable in the catalog.

**Data flow**: It receives either a schema mapping or `None`. If there is no schema, it falls back to the standard `TaskInput` fields. If the schema has no usable properties, it returns `(no fields)`. Otherwise it reads the schema's properties and required list, then returns sorted field names, marking fields not listed as required as optional.

**Call relations**: It is used by `spawn_catalog_skill` for each workspace agent fetched from the database. This lets the final catalog show the payload expected by database-backed agents as well as fixed profile-backed agents.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `spawn_catalog_skill`  (lines 52–104)

```
async def spawn_catalog_skill(registry: SubagentRegistry, member_id: UUID | None) -> RuntimeSkill
```

**Purpose**: This async function builds the actual `spawn-catalog` runtime skill for the current turn. It lists every target that `spawn` can dispatch to, using the same live registry and workspace records that spawning itself depends on.

**Data flow**: It receives the live `SubagentRegistry` and the current member's ID, if there is one. It gathers profile names from the registry, opens a workspace database transaction, checks whether the member is an admin, and queries active workspace agents visible to that member. It then formats profiles and agents into a Markdown table, adds explanatory text and front matter, and returns a `RuntimeSkill` containing those instructions.

**Call relations**: This is the main builder in the file. It calls `_profile_payload` to describe fixed subagent profiles and `_schema_payload` to describe workspace agents. It also relies on `workspace_tx` for a database connection, `ws_current` to know the active workspace, `member_is_admin` to decide visibility, and SQLAlchemy's `select` to read agent rows. The finished `RuntimeSkill` is what an agent loads when it wants to learn how to delegate with `spawn`.

*Call graph*: calls 2 internal fn (_profile_payload, _schema_payload); 5 external calls (__init__, select, workspace_tx, member_is_admin, ws_current).


### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a simple but important problem: people need a trustworthy list of available models, but hand-written lists easily go stale. Instead of maintaining a separate document, this file turns the system’s live model registry into a readable skill. The registry is the source of truth for model facts such as provider, knowledge cutoff, context window size, price, reasoning support, and API surface.

At startup, `model_catalog_skill` receives a `ModelRegistry`. It sorts all registered model specifications by model id, then writes them into a Markdown table. Think of it like printing a menu from the restaurant’s actual kitchen inventory, rather than from a separate brochure that someone might forget to update.

Prices are stored internally as tiny units called micro-dollars. The helper `_per_mtok` converts those internal values into normal dollar text, shown as price per million tokens. A token is a small piece of text used for model input and output billing.

Finally, the file wraps the Markdown table in a `RuntimeSkill`, including front-matter metadata with the skill name and description. Without this file, users or agents choosing a model would lose an automatically accurate, runtime-backed catalog and might rely on outdated or incomplete information.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns an internal price value into a friendly dollar amount. It exists so the model table can show prices in a way people understand instead of exposing low-level billing units.

**Data flow**: It receives a price stored as micro-dollars per million tokens. It divides that by the number of micro-dollars in one dollar, formats the result with two decimal places, and returns text like `$1.25`.

**Call relations**: When `model_catalog_skill` is building each row of the catalog table, it calls `_per_mtok` for the input price and again for the output price. `_per_mtok` does only this formatting step, then hands the readable price text back for inclusion in the Markdown table.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the complete model catalog skill from the live model registry. Someone would use it during startup to make a readable, built-in reference showing every model the deployment can run.

**Data flow**: It receives a `ModelRegistry`, which contains the current model specifications. It reads each model’s id, provider, knowledge cutoff, context window, prices, reasoning support, and API surface; formats those facts into a Markdown table; wraps that table with the skill name and description; and returns a `RuntimeSkill` containing both the user-facing instructions and the raw Markdown form.

**Call relations**: This is the main builder in the file. As it loops through registry entries, it asks `_per_mtok` to turn stored price numbers into dollar text. At the end, it passes the finished name, description, instructions, and raw Markdown into `RuntimeSkill.__init__` so the rest of the skill runtime can load it like any other skill.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### Conversation Context
These files keep the model-facing conversation context compact and enrich it with safe summaries of scheduled automations.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling`

A running assistant conversation can become too large to send back to the model. This file solves that by doing “compaction”: it compresses the older part of the transcript into one structured summary, keeps the recent tail exactly as it was, checks that important facts survived, and saves both the old and new versions for later inspection. Think of it like archiving old email threads into a careful digest while leaving the latest replies in your inbox.

The main `Compaction` class first decides whether compaction is needed by estimating the token count, where tokens are the chunks of text a model reads. If the conversation is too large, it splits messages into safe conversation rounds so tool calls and their results are not separated. It sends the older rounds to a model with special instructions asking for a JSON summary, then validates that JSON into a `CompactionSummary` object.

The file is cautious about trust. It harvests “anchors,” meaning exact important strings such as file paths, error names, loaded skills, and active request references, from the original text rather than from the model summary. It then verifies whether those anchors are still present in the replacement window. If the summary dropped them, it retries once with explicit correction instructions. Finally, it enforces that the new window is actually smaller when the pipeline could reasonably make it smaller, records logs and metrics, and writes compressed before/after/summary records to blob storage.

#### Function details

##### `is_context_overflow`  (lines 105–111)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: This function recognizes errors that mean a model request was too large for the provider to accept. It lets the system shrink and retry instead of treating that situation like an ordinary failure.

**Data flow**: It receives an exception, combines the exception's class name and message into lowercase text, and searches for phrases such as “context length” or “prompt is too large.” It returns `true` when the error looks like a context overflow, otherwise `false`.

**Call relations**: During summarization, `Compaction._summarize` calls this after a failed model request. If it says the prompt was too large, summarization drops some old rounds and tries again.

*Call graph*: called by 1 (_summarize).


##### `harvest_anchors`  (lines 114–140)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: This function collects exact facts that must survive compaction, such as durable tool-output file paths, error class names, loaded skill names, and active request references. These are used as a fairness test for the summary.

**Data flow**: It receives the old head text, the skills currently loaded, and active request text. It scans those inputs for known patterns, removes duplicates while preserving recent items, limits how many of each kind it keeps, and returns `Anchor` objects containing the kind of fact and its exact text.

**Call relations**: `Compaction._compact` calls this after choosing the part of the transcript to summarize. The resulting anchors are later used by verification to decide whether the replacement window carried forward enough of the old context.

*Call graph*: called by 1 (_compact); 1 external calls (__init__).


##### `missing_anchors`  (lines 143–147)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: This function finds which required anchor facts are absent from the replacement text. It uses exact containment, not a fuzzy similarity check.

**Data flow**: It receives a set of anchors and a text string that represents what will remain after compaction. It keeps only the anchors whose literal text does not appear in that carried-forward text, and returns them.

**Call relations**: `Compaction._verify` calls this while grading a candidate summary. Missing anchors can cause a correction retry and are also recorded in the compaction verification.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 185–189)

```
def __repr__(self) -> str
```

**Purpose**: This gives a short debug-friendly label for a compaction request without printing the entire transcript. It helps logs or developer tools show what kind of request is being processed.

**Data flow**: It reads the number of messages, the compaction reason, and the number of active requests from the object. It returns a compact string such as a summary card, not the full data.

**Call relations**: This is used automatically by Python when the request object is printed or logged. It does not drive the compaction flow itself.


##### `Compaction.__repr__`  (lines 223–224)

```
def __repr__(self) -> str
```

**Purpose**: This gives a short readable name for a `Compaction` object. It identifies the conversation and model without exposing large internal state.

**Data flow**: It reads the conversation id and model name from the object and returns a concise string representation.

**Call relations**: Python uses this automatically when a `Compaction` instance is displayed in debugging or logs. The rest of the pipeline does not call it directly.


##### `Compaction.maybe_compact`  (lines 226–246)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This is the public decision point: given the current messages, it either returns them unchanged or runs compaction. It can also be forced, for example after the model provider says the request is too large.

**Data flow**: It receives the current message window, a force flag, and active requests. It checks whether there are enough messages to compact, estimates token size, compares it with the trigger threshold unless forced, and either returns the original messages with no usage records or passes a compaction request into the full compaction pipeline.

**Call relations**: The conversation loop calls this before sending a large window to the model or after an overflow recovery path. When compaction is needed, it creates a `_CompactionRequest` and hands control to `Compaction._compact`.

*Call graph*: calls 3 internal fn (_compact, _tokens, _trigger); 1 external calls (__init__).


##### `Compaction._trigger`  (lines 248–254)

```
def _trigger(self) -> int
```

**Purpose**: This calculates the token limit at which automatic compaction should start. It leaves room for the summary output and a safety buffer so the next model call does not run right up against the provider limit.

**Data flow**: It reads either a manually supplied trigger value or the model context window, summary budget, and buffer constants. It returns the token threshold used for decisions and budget checks.

**Call relations**: `Compaction.maybe_compact` uses this to decide whether to compact. `Compaction._require_budget` uses the same threshold to confirm the compacted window will not immediately need compaction again when that is avoidable.

*Call graph*: called by 2 (_require_budget, maybe_compact).


##### `Compaction._compact`  (lines 257–335)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This is the main compaction workflow. It selects what to summarize, asks the model for a structured summary, verifies the replacement, persists records, fires observation hooks, and returns the new message window.

**Data flow**: It receives a `_CompactionRequest` containing messages, reason, and active requests. It splits the transcript into old head and recent tail, records pre-compaction information, summarizes the head, drains loaded skills into the summary, gathers references and anchors, verifies the result, retries once if important anchors were missed, enforces size rules, writes compressed before/after/summary records, logs verification, fires hooks, and returns the compacted messages plus model usage records.

**Call relations**: `Compaction.maybe_compact` calls this only after the guard checks say compaction should happen. Inside, it coordinates helpers for selection, summarization, verification, budget enforcement, persistence, and reporting.

*Call graph*: calls 11 internal fn (_next_index, _persist, _record_verification, _references, _require_budget, _select, _summarize, _tokens, _verify, _window_text (+1 more)); called by 1 (maybe_compact); 6 external calls (__init__, __init__, __init__, replace, from_iterable, warn).


##### `Compaction._select`  (lines 337–353)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: This chooses the older messages to summarize and the newest messages to keep exactly as written. It keeps whole conversation rounds so a tool call is not separated from its result.

**Data flow**: It receives all messages, groups them into rounds, then walks backward until it has kept at least the configured number of recent messages. It returns the older rounds as the head and the flattened recent messages as the tail, or `None` if nothing is safe to summarize.

**Call relations**: `Compaction._compact` calls this at the beginning. It depends on `Compaction._rounds` to make safe groups before deciding where the boundary goes.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 355–369)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This groups transcript messages into conversation rounds that should stay together. The main reason is to keep assistant tool-use messages with the user/tool-result messages that answer them.

**Data flow**: It receives a sequence of messages and walks through them in order. Each assistant message starts a new round when there is already content in the current round; all other messages are added to the current round. It returns a tuple of message groups.

**Call relations**: `Compaction._select` calls this before picking the compacted head and kept tail. It is the grouping rule that protects tool-call structure during compaction.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 371–397)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]=()) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: This runs one bounded attempt sequence to turn old transcript rounds into a validated summary. If the summary request itself is too large, it drops some oldest rounds and retries a limited number of times.

**Data flow**: It receives the head rounds and, optionally, anchors missed by a previous summary. It calls one summarize attempt; if the provider rejects the prompt as too large, it removes the oldest portion and tries again until success or the retry limit is reached. It returns a `CompactionSummary` and the usage records from the successful call.

**Call relations**: `Compaction._compact` calls this first for the normal summary and sometimes again for an anchor-correction retry. It delegates each actual model call to `Compaction._summarize_once`, uses `is_context_overflow` to classify failures, and uses `Compaction._drop_oldest` to shrink oversized prompts.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 399–420)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: This performs one actual model call asking for a compaction summary. It streams the model's text response, captures usage information, and parses the result as structured data.

**Data flow**: It receives the rounds to summarize and any missed anchors to mention. It builds a `ModelRequest` with the compaction system prompt and prepared transcript text, streams text chunks from the model client, records the usage event, joins the chunks, validates them as a summary, and returns the summary plus usage.

**Call relations**: `Compaction._summarize` calls this for each attempt. It uses `Compaction._prepare` to build the prompt and `Compaction._parse_summary` to turn the model's response into a trusted object.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 422–444)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: This turns old transcript rounds into the text prompt given to the summarizing model. It also adds correction instructions when a previous summary missed required anchor facts.

**Data flow**: It receives grouped messages and missed anchors. It renders each message as `role: text`, converts non-text blocks into readable markers, folds long repeated runs into a short count marker, optionally appends a list of missed anchors, and ends with a reminder to return only JSON. The result is one prompt string.

**Call relations**: `Compaction._summarize_once` calls this before sending the model request. It relies on `Compaction._text` for message rendering, `Compaction._fold_repeated_runs` to reduce spam-like repetition, and `Compaction._bullets` to format missed anchors.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 446–459)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: This shrinks text that repeats the same short phrase many times in a row. It preserves the information that repetition happened without sending all repeated copies to the summarizer.

**Data flow**: It receives a text string, searches for long consecutive repeated word sequences, and replaces each run with one copy plus a marker saying how many times it repeated. It returns the shortened text.

**Call relations**: `Compaction._prepare` calls this while building the summarizer prompt. It helps compaction succeed on transcripts bloated by loops, pasted spam, or repeated tool output.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 454–457)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: This small inner helper builds the replacement text for one repeated run. It calculates the repeat count and formats the marker.

**Data flow**: It receives a regular-expression match for a repeated text run. It extracts the repeated unit, estimates how many copies were present, and returns one unit followed by a `[repeated N times]` marker.

**Call relations**: It is used internally by `Compaction._fold_repeated_runs` as the replacement function passed to the regular-expression engine.


##### `Compaction._drop_oldest`  (lines 461–466)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This removes the oldest slice of the head when the summarizer prompt is too large. It is a controlled way to shrink the prompt rather than failing immediately.

**Data flow**: It receives the current head rounds, drops at least one round and generally about the oldest fifth, and returns the remaining newer rounds.

**Call relations**: `Compaction._summarize` calls this only after `is_context_overflow` says the provider rejected the summary prompt for size. The smaller set of rounds is then retried.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 468–509)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: This turns the model's raw text response into a validated `CompactionSummary`. It is deliberately strict enough to avoid installing an unusable or empty summary.

**Data flow**: It receives raw text from the model, finds the first balanced JSON object inside it, validates that object against the expected summary schema, and checks that the intent field is not blank. It returns a typed summary or raises a `RuntimeError` if parsing or validation fails.

**Call relations**: `Compaction._summarize_once` calls this after collecting the streamed model response. A failure stops compaction rather than letting bad text replace real conversation history.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 511–528)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: This finds durable tool-output files from the part of the transcript being summarized and carries their paths forward. These files can be reread later, so the compacted message can reference them instead of copying their full contents.

**Data flow**: It receives the head rounds and the kept tail. It scans the tail for paths already still visible, then scans the head for tool-output paths that are not already visible, removes duplicates, keeps only the most recent few, and returns those paths.

**Call relations**: `Compaction._compact` calls this while building the boundary facts for verification and rendering. It uses `Compaction._text` to inspect each message's readable content.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 530–569)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: This converts a validated summary plus carried references and active requests into the single replacement user message. It creates a deterministic, readable compacted-context block.

**Data flow**: It receives a `CompactionSummary`, durable reference paths, and active request strings. It adds sections only when they contain content, includes active requests verbatim, and returns one string beginning with the compacted-context prefix.

**Call relations**: `Compaction._verify` calls this to build the candidate replacement message. `Compaction._require_budget` also calls it with an empty summary to calculate the unavoidable minimum size of the compacted block.

*Call graph*: calls 1 internal fn (_bullets); called by 2 (_require_budget, _verify).


##### `Compaction._bullets`  (lines 571–572)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: This formats a list of strings as simple markdown-style bullet lines. It keeps repeated rendering code out of the larger prompt and summary builders.

**Data flow**: It receives a tuple of strings and returns one text block where each item is prefixed with `- ` and separated by newlines.

**Call relations**: `Compaction._prepare` uses this for missed-anchor correction instructions. `Compaction._render` uses it for summary sections such as concepts, errors, pending tasks, skills, and references.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 574–575)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: This flattens a message window into plain text for searching and verification. It gives other checks one combined string to inspect.

**Data flow**: It receives messages, converts each message to text with `Compaction._text`, joins them with newlines, and returns the combined text.

**Call relations**: `Compaction._compact` uses this to capture the before-window text and head text. `Compaction._verify` uses it to check whether anchors appear in the candidate after-window.

*Call graph*: calls 1 internal fn (_text); called by 2 (_compact, _verify).


##### `Compaction._verify`  (lines 577–613)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: This grades one candidate summary before it can replace the old transcript head. It removes invented file paths, renders the replacement, counts missing anchors, and records size information.

**Data flow**: It receives a summary, a fixed boundary describing the original window, and a flag saying whether this is a retry. It keeps only summary file references that appeared in the original text, injects pipeline-known loaded skills, renders the compacted message, joins it with the kept tail, estimates token counts, finds missing anchors, and returns a `_Candidate` containing the checked summary, rendered text, new messages, and verification record.

**Call relations**: `Compaction._compact` calls this after each successful summarization attempt. It uses `Compaction._render`, `Compaction._tokens`, `Compaction._window_text`, and `missing_anchors` to decide whether the candidate is good enough or needs a retry.

*Call graph*: calls 4 internal fn (_render, _tokens, _window_text, missing_anchors); called by 1 (_compact); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._require_budget`  (lines 615–653)

```
def _require_budget(self, verification: CompactionVerification, boundary: _Boundary) -> None
```

**Purpose**: This enforces the key size promise of compaction: when the pipeline had enough room to make things smaller, the replacement must actually be smaller and must not immediately retrigger compaction. It prevents paying for a summary that makes the situation worse.

**Data flow**: It receives a verification record and the original boundary. It computes the trigger threshold, the fixed cost of the compacted block with an empty summary, the old head size, and the room available for summary text. If the candidate violates the size rules in a situation the pipeline could have fixed, it raises an error; otherwise it returns nothing.

**Call relations**: `Compaction._compact` calls this after verification and before persistence. It relies on `Compaction._trigger`, `Compaction._render`, and `Compaction._tokens` to compare old and new window sizes.

*Call graph*: calls 3 internal fn (_render, _tokens, _trigger); called by 1 (_compact); 1 external calls (__init__).


##### `Compaction._record_verification`  (lines 655–678)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], verification: CompactionVerification) -> None
```

**Purpose**: This reports the quality of a completed compaction. It records whether anchors were lost, whether paths were dropped, and whether a retry was used.

**Data flow**: It receives the compaction index, reason, and verification record. It writes a structured log with token counts and loss details, then emits a metric labeled as clean or lossy and retried or not.

**Call relations**: `Compaction._compact` calls this after persistence succeeds. The emitted log and metric let operators monitor compaction quality across many conversations.

*Call graph*: called by 1 (_compact); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 680–691)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: This saves the evidence of a compaction: the full message window before compaction, the replacement window after compaction, and the structured summary. That makes the transformation auditable later.

**Data flow**: It receives an index, before messages, after messages, and the summary. It writes the before and after windows through `Compaction._write`, compresses the summary JSON with LZ4 compression, and stores it in the blob store under the compaction key.

**Call relations**: `Compaction._compact` calls this once a candidate passes verification and budget checks. It uses `Compaction._key` to place all three records in the conversation's compaction area.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 693–697)

```
async def _next_index(self) -> int
```

**Purpose**: This finds the next available compaction number for the conversation. Each compaction is stored under a numbered slot.

**Data flow**: It starts at index 1 and checks whether an `after` record already exists at that index. It increments until it finds a free index and returns that number.

**Call relations**: `Compaction._compact` calls this before writing records. It uses `Compaction._key` to ask the blob store about each possible location.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 699–706)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: This reads back one saved compaction record if it exists. It is useful for inspection, replay, or evaluation tools that need the before window, after window, and summary.

**Data flow**: It receives a compaction index. It tries to fetch the compressed before, after, and summary blobs; if any are missing, it returns `None`. If all are present, it decodes them into a `CompactionRecord` and returns it.

**Call relations**: This is the read-side companion to `Compaction._persist`. It uses `Compaction._key` to find the blobs and hands the raw data to the transcript decoding helper.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 708–716)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: This writes either the before or after message window to blob storage in a compact, deterministic form. It is used for audit records of the compaction boundary.

**Data flow**: It receives an index, a label saying `before` or `after`, and messages. It wraps the messages in a `CompactionWindow`, converts that to sorted compact JSON, compresses it with LZ4, and stores it under the right key.

**Call relations**: `Compaction._persist` calls this twice, once for the original window and once for the replacement window. It uses `Compaction._key` to compute the storage path.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 718–719)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: This builds the blob-storage key for one compaction artifact. It keeps all before, after, and summary files in the same naming scheme.

**Data flow**: It receives a compaction index and a part name such as `before`, `after`, or `summary`. It combines those with the conversation id through the shared compaction-key helper and returns the storage key string.

**Call relations**: `Compaction._next_index`, `Compaction._persist`, `Compaction._write`, and `Compaction.read_record` all use this so reads and writes agree on the exact storage location.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 721–744)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: This estimates how many model tokens a message window costs. The estimate includes text, hidden reasoning payloads, and images, so image-heavy or reasoning-heavy windows still trigger compaction.

**Data flow**: It receives messages. For each one, it counts role text, rendered text, opaque reasoning bytes, and a fixed token estimate for each image, converts character cost into token cost, and sums everything into one integer.

**Call relations**: `Compaction.maybe_compact` uses this to decide whether to compact. `Compaction._compact`, `Compaction._verify`, and `Compaction._require_budget` use it to record and compare before, tail, and after sizes.

*Call graph*: calls 3 internal fn (_image_count, _opaque_chars, _text); called by 4 (_compact, _require_budget, _verify, maybe_compact).


##### `Compaction._opaque_chars`  (lines 746–765)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: This counts hidden reasoning data that is sent back to the model but is not useful readable transcript text. Without this, the system could badly undercount some model messages.

**Data flow**: It receives one message. If the content is plain text, it returns zero; otherwise it scans structured blocks and adds the lengths of thinking signatures, redacted reasoning data, and encrypted reasoning content. It returns that extra character count.

**Call relations**: `Compaction._tokens` calls this while estimating the cost of each message. It complements `Compaction._text`, which renders human-readable parts only.

*Call graph*: called by 1 (_tokens).


##### `Compaction._image_count`  (lines 767–777)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: This counts images inside a message so the token estimate can include their cost. Images do not have much text, but they still consume model context.

**Data flow**: It receives one message. If the content is plain text, it returns zero; otherwise it scans top-level image blocks and images nested inside tool-result blocks, counts them, and returns the total.

**Call relations**: `Compaction._tokens` calls this for each message. Its count is multiplied by a fixed image token estimate.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 779–803)

```
def _text(self, message: Message) -> str
```

**Purpose**: This turns one message into readable text for prompts, searching, token estimates, and verification. It gives every supported content block a plain-text representation.

**Data flow**: It receives a message. If the content is already a string, it returns it; otherwise it walks through structured blocks, keeping text and thinking summaries, replacing images and redacted reasoning with markers, rendering tool results, and formatting tool calls with their JSON arguments. It returns the joined text.

**Call relations**: `Compaction._prepare`, `Compaction._references`, `Compaction._tokens`, and `Compaction._window_text` all call this. It is the common translation layer between rich message objects and the plain text that compaction can inspect.

*Call graph*: called by 4 (_prepare, _references, _tokens, _window_text); 1 external calls (dumps).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`domain_logic` · `conversation context rendering`

This file is the bridge between the scheduled-tasks extension and the conversation “slot” system. A slot is a named bundle of information attached to a conversation, like a side panel that says, “Here are the automations connected to this chat.” Without this file, scheduled tasks might still exist, but the conversation would not have a standard way to show them, count them, or hide sensitive parts.

The main flow starts from the conversation context. The context says which scheduled-task records are currently visible and authorized for this conversation. The file opens a ScheduleStore, asks it for matching tasks, and then double-checks that each stored task still matches a visible item and the expected authorization generation. That check matters because it stops stale or unauthorized task data from leaking into the conversation.

For each allowed task, it fetches inspection details such as the next run time, last run time, last status, and last response. It then trims long text to fixed limits so the slot stays small and predictable. If anything had to be omitted or shortened, it marks the result as truncated. It also respects content visibility: when task content is not visible, descriptions and latest responses are hidden.

At the bottom, `AUTOMATIONS_SLOT` registers this behavior under the id `automations`, with a label, icon, summary function, and full read function.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This helper creates the schedule storage object used to read scheduled tasks. It also makes sure the conversation context actually contains the scheduled-tasks extension state needed to open that storage.

**Data flow**: It receives a conversation slot context. If the context has no extension object, it stops with an error because there is nowhere to read schedules from. If the extension object is present, it uses it to create and return a `ScheduleStore`, which is the object that knows how to query scheduled tasks.

**Call relations**: _conversation` and `_read` both call this when they need access to stored schedules. It is the small doorway from the generic conversation-slot world into the scheduled-tasks storage world.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function asks the schedule store for the scheduled tasks that belong to the current conversation and match the visible item names. It fetches one more than the display limit so the caller can tell whether there are too many to show fully.

**Data flow**: It receives a conversation slot context, pulls the names from `ctx.visible_items`, opens the schedule store through `_scheduler`, and asks for tasks with the current conversation id and those names. It returns the matching scheduled task records as a tuple.

**Call relations**: _read` calls this as its first task-listing step. `_conversation` relies on `_scheduler` to get the store, then hands the raw scheduled task rows back to `_read` for authorization checks, inspection, trimming, and packaging.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main reader for the Automations slot. It builds the full slot payload: a safe list of visible automations, their schedule details, recent run information, and a flag saying whether anything was left out or shortened.

**Data flow**: It receives a conversation slot context. It opens the schedule store, loads candidate tasks for the conversation, and compares them against the visible items in the context. Only tasks whose name and authorization generation match are kept. It inspects the kept tasks to learn run status and timing, hides content when the visible item says content is not allowed, cuts long fields down to fixed lengths, and notes whether truncation happened. It returns an `AutomationsSlotPayload` containing `ConversationAutomation` entries plus the truncation flag.

**Call relations**: This function is registered as the slot’s full `read` callback in `AUTOMATIONS_SLOT`, so the conversation-slot system calls it when it needs the detailed Automations content. Inside the flow, it calls `_scheduler` to reach storage, `_conversation` to load candidate tasks, asks the schedule store for inspection details, and then creates the payload objects that the rest of the system can display or consume.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Automations slot without loading all task details. It returns how many visible automations should be advertised, capped at the maximum the slot can show.

**Data flow**: It receives a conversation slot context and counts `ctx.visible_items`, but never above `CONVERSATION_AUTOMATIONS_MAX`. If the count is zero, it returns `None` instead of `0`, which likely means “do not show a summary badge.” Otherwise it returns the count.

**Call relations**: This function is registered as the slot’s `summarize` callback in `AUTOMATIONS_SLOT`. The conversation-slot system can use it for a lightweight preview before calling the heavier `_read` function that queries storage and inspection details.


### Prompt Construction
These files expose shared reply-shaping instructions and render the final checked system prompt sent to the model.

### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import time`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes `core/src/ufo/loop/prompts` available as a named place where prompt-related code can live. Think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it lets the rest of the system find that drawer reliably. Without this file, depending on the Python version and packaging setup, imports that expect `ufo.loop.prompts` to be a package could fail or behave differently. Since the file is empty, it performs no setup, exposes no shortcuts, and changes no data at runtime.


### `core/src/ufo/turns/delivery_register.py`

`config` · `prompt assembly`

This file is a small bridge between a Markdown rule document and the Python code that builds agent prompts. The real content lives next to it in `delivery_register.md`. When this Python file is imported, it reads that Markdown file from disk, removes extra whitespace at the ends, and stores the result in `DELIVERY_REGISTER_BLOCK`.

The “delivery register” is described as the house set of writing rules for anything an agent says to a user or another agent, including text inside schema-owned fields. In plain terms, it is like a style and safety notice that gets stapled into every relevant instruction packet before an agent writes. Without this file, the rest of the system would not have a simple, reliable Python name to use when it needs that shared instruction block.

The file does not define any functions or classes. Its main behavior happens immediately at import time: find the neighboring Markdown file, read its full text, clean the edges, and expose it as a constant. Other prompt-building code can then reuse the exact same delivery rules instead of copying the text in multiple places.


### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `turn setup before sending a prompt to the model`

A language model is guided by a system prompt: a block of instructions that tells it how to behave. This file is the prompt assembly station. It starts with core prompt files such as the shell template, citation text, knowledge-cutoff text, and compaction prompt, then fills named slots like the agent's own instructions, available skills, contributed sections, citation rules, and the model's knowledge cutoff date.

The important safety feature is strict placeholder checking. Placeholders use double braces, such as {{name}}. If an agent prompt declares a variable, the caller must supply it. If the caller supplies a variable the prompt did not declare, that is also an error. After all substitutions, any leftover {{...}} placeholder causes a loud failure. This prevents broken prompts from reaching the model with raw template markers still inside them.

The file also normalizes extra blank lines and computes a SHA-256 digest, which is a short fingerprint of the final prompt content. That digest lets logs and observability tools tell exactly which prompt text was used. In everyday terms, this file is like a careful print shop: it merges form letters, refuses missing fields, tidies the final page, and stamps each finished copy with a unique tracking code.

#### Function details

##### `rendered_prompt`  (lines 54–55)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This wraps finished prompt text in a small result object and adds a digest, which is a fingerprint of the exact text. Someone uses it when they need both the prompt content and a stable way to identify that content in logs or tracing.

**Data flow**: It receives the final prompt text as a string. It encodes that text, computes a SHA-256 hash from it, prefixes the hash with "sha256:", and returns a RenderedPrompt containing both the digest and the original content. It does not change any outside state.

**Call relations**: After render_template has filled every prompt slot and cleaned up spacing, it calls rendered_prompt as the final packaging step. rendered_prompt hands back the object that the rest of the turn can send to the model and record for observability.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 58–75)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This builds the main agent system prompt from the standard shell template. It adds the agent instructions, contributed capability sections, available skills, and the model's knowledge cutoff in a human-readable form.

**Data flow**: It receives the agent prompt, a list of section name/body pairs, an optional list of skill name/description pairs, and a required knowledge cutoff like "2026-02". It turns that cutoff into a readable month and year such as "February 2026", inserts it into the knowledge-cutoff block, places that block into the shell template, and then passes the result to render_template. The output is a RenderedPrompt with final text and digest.

**Call relations**: This is the higher-level entry for normal system prompt creation in this file. It prepares the model-specific knowledge cutoff and then delegates the detailed slot filling and validation to render_template.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 78–96)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This is the main template-filling function. It combines a prompt template, agent-specific instructions, variables, skills, sections, and shared citation text into one final prompt, while refusing to let unresolved placeholders slip through.

**Data flow**: It receives a template, an agent prompt, a mapping of variable names to values, a list of skills, and a list of sections. First it asks _substitute_vars to fill variables inside the agent prompt. If there is agent text but the outer template has no agent-prompt slot, it raises an error. Then it replaces the skill, citation, section, and agent-prompt slots. It scans the finished text for any remaining {{...}} placeholders; if any are found, it raises an error. Otherwise it collapses long blank-line runs, trims the end, and returns a RenderedPrompt through rendered_prompt.

**Call relations**: render_system_prompt calls this after preparing the standard shell prompt. Inside this function, _substitute_vars checks the agent prompt variables, render_skill_index formats the skill list, and rendered_prompt packages the finished prompt with its digest.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 99–108)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This turns the list of available skills into a small prompt block the model can read. If there are no skills, it returns an empty string so the prompt does not include an empty skills section.

**Data flow**: It receives a sequence of skill names and descriptions. With no skills, it outputs an empty string. With skills, it creates a text block wrapped in <available_skills> and </available_skills>, with one bullet line per skill. It does not modify the input list.

**Call relations**: render_template calls this while filling the skill-index slot. The resulting text becomes the part of the final prompt that tells the model which loadable skills are available.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 111–118)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This fills variables inside the agent prompt, but only when the prompt and the supplied variable values match exactly. It exists to catch mistakes early, such as forgetting to provide a value or providing a value the prompt never uses.

**Data flow**: It receives a template string and a mapping of variable names to replacement strings. It finds all {{variable}} names declared in the template and compares them with the keys supplied by the caller. If any declared variable is missing, it raises an error. If any supplied variable was not declared, it raises an error. If both sides match, it replaces each placeholder with its supplied value and returns the filled string.

**Call relations**: render_template calls this before placing the agent prompt into the larger shell. Its job is to make sure the agent-specific part is already complete and trustworthy before the rest of the prompt is assembled.

*Call graph*: called by 1 (render_template).

## 📊 State Registers Touched

- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-transcript-store` — The saved conversation history and compacted summaries that later turns, portals, and auditors read back.
- `reg-live-update-stream` — The temporary live feed of progress messages that open clients and other server processes can follow.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-tool-catalog` — The current list of tools the agent may call, with their names, inputs, permissions, and implementations.
- `reg-skill-store` — The shared library of built-in and user-created skills that can be selected, checked, and loaded into a turn.
- `reg-model-catalog` — The shared list of available AI models, their abilities, prices, limits, and required credentials.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-search-indexes` — The shared searchable indexes and embeddings that let turns, research tools, and memory lookup find relevant text.
- `reg-memory-store` — The long-term saved facts, profiles, notes, and summaries that can be recalled in later conversations.
- `reg-subagent-objectives` — The shared plan and delegation state for child agents, objectives, steps, evidence, attempts, and result delivery.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-conversation-todo-store` — Durable conversation checklist/todo state exposed as workspace objects and updated by tools or agents across turns.
- `reg-delivery-format-registry` — Shared per-surface reply and delivery-format rules used when constructing prompts and shaping delivered responses.
- `reg-turn-created-reference-store` — Saved references created by a turn, linking its work to newly produced artifacts, objects, sources, sites, or other records for later display, replay, and cleanup.
- `reg-content-provenance-trust-labels` — Visibility, provenance, and trust labels attached to messages, source content, and external text so prompt construction and policy checks can separate trusted instructions from untrusted content.
