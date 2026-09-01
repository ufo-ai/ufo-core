# Admission and Queue Coordination  `stage-7.1`

This stage is the traffic control system for conversation work. It sits at the front of the runtime and also supports the main work loop. Its job is to make sure each incoming message becomes the right kind of “turn,” meaning one agent work cycle, and that turns run safely, in order, and only once.

The admission file is the front door. Under a lock, so two requests cannot make conflicting choices, it decides whether to create a new turn, attach the message to one already running, pause because spending limits were reached, or reject it. The ambient reply file adds a courtesy check: if a message in a thread does not clearly ask for the agent, it can avoid starting unnecessary work.

The dispatch file is the turnstile. It starts the next waiting turn for a conversation only when that conversation has no turn already running. The queue runner is the worker. It claims queued turns, prepares the runtime, runs the agent engine, records failures, and wakes whatever should run next.

## Files in this stage

### Queued Turn Execution
Claims queued turns, builds the runtime environment, runs the turn engine, records failures, and triggers follow-up work.

### `core/src/ufo/runtime/queue.py`

`orchestration` · `main loop / turn execution`

A “turn” is the system’s basic work item, like a ticket in a service desk queue. This file makes sure each ticket is picked up once, run with the right agent, tools, model, credentials, sandbox, skills, and billing rules, and then closed in a way that clients and parent tasks can see.

The file sits between the queue system and the actual turn engine. First it installs a process-wide Runtime, which is a bundle of shared services such as the database client, model registry, sandbox provider, tool registry inputs, skill registry, and event hub. When DBOS, the durable workflow system, starts a turn workflow, this file loads the turn from the database, claims it, applies workspace provisioning, chooses the model, freezes billing decisions for crash-safe replay, assembles prompts and tools, opens or authorizes a sandbox, and then hands everything to TurnEngine.

It also protects the rest of the system from awkward edge cases. Scheduled turns are lightly throttled so they do not crowd out member messages. Subagent results are delivered back to their parent conversation. If setup fails before the engine can write a final status, this file writes a failed terminal frame and publishes it so waiting users are not left hanging. In short, it is the traffic controller and safety net for durable agent execution.

#### Function details

##### `_turn_gates`  (lines 139–155)

```
def _turn_gates(parent_turn_id: UUID | None, admission_source: TurnAdmissionSource) -> tuple[asyncio.Semaphore, ...]
```

**Purpose**: Chooses whether a turn must wait for a local fairness slot before running. It mainly limits waves of scheduled, root-level turns so they cannot use up all capacity in one process.

**Data flow**: It receives the turn’s parent id and how the turn was admitted. If the turn is a scheduled root turn, it finds or creates a semaphore, which is a small counter-like lock, for scheduled work on the current event loop. It returns the semaphore to hold, or an empty set of gates when no fairness limit applies.

**Call relations**: _execute_turn asks this before running a claimed turn. The returned gates are held while setup and execution begin, so scheduled background work gives member-facing turns room to run.

*Call graph*: called by 1 (_execute_turn); 2 external calls (Semaphore, get_running_loop).


##### `_without_workspace_skills`  (lines 177–180)

```
async def _without_workspace_skills(name: str) -> None
```

**Purpose**: Acts as a no-op skill loader for cases where workspace skills are disabled. It exists so callers can use the same shape of callback without actually loading any saved workspace skill.

**Data flow**: It receives a skill name and ignores it. Nothing is read, changed, or returned beyond completing successfully with no result.

**Call relations**: This helper is not called by the provided graph, but it represents the disabled path for workspace skill access: a request to load by name goes nowhere.


##### `_member_skill_turn`  (lines 183–195)

```
def _member_skill_turn(turn: Turn) -> bool
```

**Purpose**: Decides whether a turn is the kind of member-facing turn that should be considered for saved workspace skills. It filters out machine-only turns that have no real member prompt to enrich.

**Data flow**: It reads the turn’s admission source, speaker, and parent fields. It returns false for prepared intents and for speakerless internal root turns, and true for ordinary turns where member skill recall could matter.

**Call relations**: _member_skill_block uses this to decide whether to include the saved-skills text in the prompt. _run_turn uses the same decision before launching background evidence gathering for skill selection.

*Call graph*: called by 2 (_member_skill_block, _run_turn).


##### `_member_skill_block`  (lines 198–205)

```
def _member_skill_block(turn: Turn, view: MemberVisibility, enabled: bool) -> str
```

**Purpose**: Returns the saved-skills text block that should be attached to a turn’s starting message. It keeps that block out when the feature is disabled or the turn is not member-facing.

**Data flow**: It receives a turn, a prepared visibility view of skills, and a feature flag. If the flag is on and _member_skill_turn says this turn qualifies, it returns the view’s text block; otherwise it returns an empty string.

**Call relations**: It builds on _member_skill_turn so skill prompting and skill recall follow the same rule. The assembled environment can use this decision when forming the prompt seen by the model.

*Call graph*: calls 1 internal fn (_member_skill_turn).


##### `_prompt_skill_index`  (lines 208–212)

```
def _prompt_skill_index(skills: SkillRegistry, enabled: bool) -> tuple[tuple[str, str], ...]
```

**Purpose**: Chooses what skill index text is shown in the model prompt. It either includes the fold-aware member skill view or only the deploy-level skill list, depending on configuration.

**Data flow**: It receives the skill registry and a flag. If enabled, it asks the skill-selection layer to build a prompt-friendly index; if disabled, it asks the registry for its plain index. It returns that tuple of skill identifiers and descriptions.

**Call relations**: Although not called in the provided graph, this helper is part of the same prompt assembly policy used by the environment layer: it defines what skill catalog the model can see directly.

*Call graph*: calls 1 internal fn (index); 1 external calls (prompt_index).


##### `_fire_shadow_selection`  (lines 218–225)

```
def _fire_shadow_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Starts a background comparison of skill-selection methods without delaying the user’s turn. This is used to collect evidence about which skills would have been chosen by vector search versus text matching.

**Data flow**: It receives the search index, embedding client, turn, and skill cards. It creates an asynchronous task to run _shadow_skill_selection, stores a reference so it is not lost, and arranges to remove that reference when the task finishes.

**Call relations**: _run_turn calls this on qualifying member turns when the skill catalog did not fit fully into the prompt. It hands the real work to _shadow_skill_selection and deliberately does not wait for it.

*Call graph*: calls 1 internal fn (_shadow_skill_selection); called by 1 (_run_turn); 1 external calls (create_task).


##### `_shadow_skill_selection`  (lines 228–254)

```
async def _shadow_skill_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Compares two possible ways of choosing relevant skills for a turn and logs the result. It is best-effort measurement, not part of the turn’s required behavior.

**Data flow**: It takes the turn text and skill cards, trims the query, asks the embedding service to turn text into numbers, searches the vector index, and also runs lexical skill selection. It logs both result lists, or logs a failure class if anything goes wrong or times out.

**Call relations**: _fire_shadow_selection launches this as a detached task. It calls the embedding client, index backend, and skill selector, but never feeds results back into _run_turn, so failures cannot break the user’s turn.

*Call graph*: calls 2 internal fn (embed, vector); called by 1 (_fire_shadow_selection); 3 external calls (timeout, log, select_top_k).


##### `with_implied_grants`  (lines 264–267)

```
def with_implied_grants(names: set[str]) -> set[str]
```

**Purpose**: Expands a set of allowed tool or action names with companion grants that are required for the named capability to work. For example, granting a skill loader also grants the skill search action it depends on.

**Data flow**: It receives a mutable set of names. For each name already present, it looks up any implied companion names and adds them to the same set, then returns the expanded set.

**Call relations**: _agent_actions, _agent_tools, _subagent_actions, and _subagent_tools all call this before filtering registries. That keeps tool and action selection consistent for agents and subagents.

*Call graph*: called by 4 (_agent_actions, _agent_tools, _subagent_actions, _subagent_tools).


##### `_agent_actions`  (lines 270–285)

```
def _agent_actions(actions: Mapping[str, Mapping[str, BoundAction]], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> frozenset[str]
```

**Purpose**: Computes which object actions an ordinary agent turn is allowed to use. These are canonical action ids, meaning stable internal names for actions supplied by extensions.

**Data flow**: It receives the available bound actions, an optional allowlist, the admission source, and optional speaker id. If there is no allowlist, or a speaking prepared intent is being run, it grants all non-profile-only actions. Otherwise it expands the allowlist through with_implied_grants and returns only matching action ids.

**Call relations**: This mirrors the tool-selection rule used by _agent_tools. It is not called in the provided graph, but it defines the action side of the same permission story used when assembling a turn.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `SubagentKeyWithdrawn.__init__`  (lines 297–304)

```
def __init__(self, profile: str, connect_url: str | None=None) -> None
```

**Purpose**: Builds a clear error for a subagent profile that requires the member’s own model account, but that account is no longer connected. The message points the member toward the place where they can reconnect.

**Data flow**: It receives the profile name and an optional base connection URL. It formats a human-readable exception message and stores the profile name on the exception object.

**Call relations**: _subagent_model raises this when model selection discovers that a required member-owned provider key is missing. The error then flows into the normal turn failure backstop.

*Call graph*: called by 1 (_subagent_model).


##### `_member_accounts_connectable`  (lines 307–309)

```
def _member_accounts_connectable(runtime: 'Runtime') -> bool
```

**Purpose**: Checks whether this deployment has any extension capable of storing a member’s own model-provider account. This helps decide whether missing member keys are fixable by asking the user to connect an account.

**Data flow**: It reads the runtime’s extension manifests. If any manifest says it supports member account connections, it returns true; otherwise it returns false.

**Call relations**: _run_turn passes this fact into subagent authorization, and _subagent_model uses it when deciding whether to raise a missing-key error.

*Call graph*: called by 2 (_run_turn, _subagent_model).


##### `_subagent_model`  (lines 312–343)

```
def _subagent_model(profile: SubagentProfile, connected: str | None, agent: Agent, runtime: 'Runtime', pinned: str | None, document: str | None) -> str
```

**Purpose**: Chooses the model that a subagent turn should run on. It carefully separates workspace-paid models from profiles that are meant to run on a member’s own connected account.

**Data flow**: It receives the subagent profile, connected provider name if any, the base agent, runtime, optional pinned model, and optional environment-document model. It resolves the highest-priority valid model: document override, member-owned account model, pinned model, profile model, or agent model. If a required member account is absent, it raises SubagentKeyWithdrawn.

**Call relations**: _run_turn calls this after loading the turn and profile. It uses _member_accounts_connectable and may construct SubagentKeyWithdrawn when the turn cannot legally fall back to the deployment’s own key.

*Call graph*: calls 2 internal fn (__init__, _member_accounts_connectable); called by 1 (_run_turn).


##### `_subagent_actions`  (lines 346–364)

```
def _subagent_actions(actions: Mapping[str, Mapping[str, BoundAction]], profile: SubagentProfile, grants: frozenset[str]) -> frozenset[str]
```

**Purpose**: Computes which object actions a subagent profile is allowed to use. It combines the profile’s own declared actions with any cross-extension grants, unless the profile is isolated.

**Data flow**: It receives all bound actions, a subagent profile, and inherited grants. It builds an allowed-name set, expands implied grants, and returns the canonical ids for actions whose ids are allowed or whose action is marked as a default for non-isolated subagents.

**Call relations**: This is the action counterpart to _subagent_tools. It uses with_implied_grants so subagent actions and tools receive the same companion permissions.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_with_action_verbs`  (lines 367–388)

```
def _with_action_verbs(selected: tuple[ToolDef, ...], all_tools: tuple[ToolDef, ...], granted_actions: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Adds the basic object-discovery tools needed when a turn has been granted object actions. Without these helper verbs, the model might be allowed to perform an action but unable to see what objects or action envelopes exist.

**Data flow**: It receives the currently selected tools, the full tool list, and granted action ids. If no actions are granted, it removes the generic action dispatcher and returns the result. If actions are granted, it adds the dispatcher and read-only action discovery tools when available and not already selected.

**Call relations**: This helper is not called in the provided graph, but it expresses the bridge between action permissions and tool visibility during environment assembly.


##### `_agent_tools`  (lines 391–415)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses which tools an ordinary agent turn may call. It enforces allowlists and keeps profile-only tools away from general agents unless explicitly named.

**Data flow**: It receives all registered tools, an optional allowlist, admission source, and optional speaker id. With no allowlist, or for a speaking prepared intent, it returns all non-profile-only tools. Otherwise it expands implied grants and returns only tools whose names are allowed.

**Call relations**: It uses with_implied_grants just like the action selectors. Although not called in the provided graph, it defines the ordinary-agent tool rule used by the broader runtime assembly flow.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_resolve_profile`  (lines 418–433)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: Looks up the subagent profile named by a turn and logs useful details if the name no longer exists. This protects operators from a vague failure when an extension was removed after a child turn was queued.

**Data flow**: It receives the registry, turn id, and requested profile name. It asks the registry for that profile. On success it returns the profile; on failure it logs the requested name and currently registered names, then re-raises the error.

**Call relations**: _run_turn calls this before building a subagent turn. It delegates the lookup to SubagentRegistry and only adds diagnostic logging around failures.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 436–448)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses which tools a subagent profile may call. It respects the profile’s own tool list, inherited grants, implied companion tools, and whether the profile is isolated from shared defaults.

**Data flow**: It receives all tools, a profile, and inherited grants. It builds the allowed set, expands it with implied grants, then returns tools whose names are allowed or that are default subagent tools for non-isolated profiles.

**Call relations**: It shares the same grant-expansion helper as _agent_tools and _subagent_actions. It is part of the environment assembly policy even though it is not directly called in the provided graph.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_apply_provisions`  (lines 454–463)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: Ensures a workspace has the agents shipped by active extensions. It runs once per workspace per process and is safe to repeat across processes.

**Data flow**: It receives the runtime and workspace id. If this process has already provisioned that workspace, it returns. Otherwise it constructs an AgentProvisioning helper from manifests, applies provisions to the workspace, and remembers that it has done so.

**Call relations**: _execute_turn calls this before running a turn. That means a workspace gets extension-provided agents by the time its first turn in this process starts.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `TurnEnvironment.assemble`  (lines 533–533)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Defines the contract for building everything a turn needs before the engine can run: prompt, tools, hooks, mounted skills, files, and related context. It is a protocol method, so another layer supplies the concrete implementation.

**Data flow**: It accepts an AssembleRequest containing the turn, agent or profile, chosen model, skill registries, grants, and environment document id. The implementation is expected to return an AssembledTurn containing the ready-to-use prompt and runtime attachments.

**Call relations**: _run_turn calls runtime.environment.assemble after it has resolved model and billing choices. The returned bundle is then passed into TurnEngine and sandbox setup.


##### `TurnEnvironment.environment_model`  (lines 535–535)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Defines how the host checks whether a pinned environment document chooses a model. This keeps document interpretation outside the queue runner while still letting billing and validation happen before execution.

**Data flow**: It receives an environment document identifier and an optional subagent profile name. The implementation returns a model name if the document pins one, or none if it does not.

**Call relations**: _run_turn calls this before assembling the environment. If it returns a model, _run_turn validates it, uses it for billing, and passes it into the assembly request.


##### `TurnEnvironment.clis`  (lines 537–537)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Defines how the runtime obtains command-line credential definitions that can be exposed inside a sandbox. These are used for connector tools that need environment variables.

**Data flow**: It takes no arguments beyond the environment object. The implementation returns a mapping from CLI names to credential descriptors.

**Call relations**: _run_turn reads this before creating SandboxAuthorizer and before opening the sandbox. _open_sandbox and SandboxAuthorizer.authorize then use the descriptors to build the correct environment variables.


##### `TurnEnvironment.slots`  (lines 539–539)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Defines how the runtime asks which credential slots are declared by the active environment. A credential slot is a named place where a secret, such as an API token, may be stored.

**Data flow**: It takes no arguments beyond the environment object. The implementation returns the declared credential slots as a tuple.

**Call relations**: _run_turn passes these slots into _open_sandbox. There they help derive provider keys and git credentials only when the sandbox is actually opened.


##### `init_runtime`  (lines 574–585)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the one Runtime object that this process will use to run turns. It also seeds sandbox carriers with bundled system skills so sandboxes can start with shared built-in capabilities.

**Data flow**: It receives a Runtime. If one is already installed, it raises an error. Otherwise it creates a system skill bundle from bundled skills, seeds compatible sandbox carriers with the bundle archive, and stores the runtime in the module global.

**Call relations**: The server startup path is expected to call this before any turn workflow runs. _execute_turn later reads the installed runtime and fails fast if it was never initialized.

*Call graph*: calls 1 internal fn (from_skills).


##### `reset_runtime`  (lines 588–593)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed Runtime so tests can install a fresh one. Production serving initializes once and does not normally use this.

**Data flow**: It takes no inputs. It sets the module-level runtime reference back to none and returns nothing.

**Call relations**: This is a testing seam around init_runtime’s single-install guard. It is not part of ordinary turn execution.


##### `_execute_turn`  (lines 596–678)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the top-level workflow body for one queued turn. It binds workspace context, applies fairness gates, provisions workspace defaults, runs the turn, and performs cleanup handoffs afterward.

**Data flow**: It receives workspace and turn ids as strings from the workflow system. It loads identifying turn facts, decides whether a member-owned account context is needed, waits for any fairness gates, applies provisions, starts tracing context, calls _run_turn, and catches setup failures. It then delivers child results to parents, offers the next conversation turn, and returns a status string.

**Call relations**: turn_workflow calls this directly. It orchestrates _turn_gates, _apply_provisions, _run_turn, _commit_failed_terminal, _deliver_to_parent, and _offer_next_turn so every successful, failed, parked, or superseded turn follows the same outer lifecycle.

*Call graph*: calls 6 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _offer_next_turn, _run_turn, _turn_gates); called by 1 (turn_workflow); 10 external calls (AsyncExitStack, select, monotonic, workspace_tx, emit_histogram, turn_span, agent, speaker, ws, UUID).


##### `_offer_next_turn`  (lines 681–693)

```
async def _offer_next_turn(runtime: Runtime, conversation_id: UUID) -> None
```

**Purpose**: Asks the dispatcher to start the next queued turn for a conversation after the current workflow ends. It treats this as a best-effort wake-up because later sweeps can repair missed offers.

**Data flow**: It receives the runtime and conversation id. It calls the dispatcher with the DBOS client. If that fails, it logs the error class and does not raise.

**Call relations**: _execute_turn calls this after each turn attempt when it knows the conversation id. It hands control to dispatch_next_turn, but keeps failures from changing the just-finished turn’s result.

*Call graph*: called by 1 (_execute_turn); 2 external calls (log, dispatch_next_turn).


##### `_deliver_to_parent`  (lines 696–728)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: If a finished turn is a child subagent turn, this delivers its durable terminal result back to the parent conversation. It avoids using in-memory results so it only sends what was actually committed.

**Data flow**: It receives the runtime and child turn id. It reads the turn row, returns early if there is no parent or no terminal result, then asks SubagentResult to deliver the validated turn. Delivery failures are logged and swallowed, except cancellation.

**Call relations**: _execute_turn calls this after _run_turn or failure handling. It uses runtime.invoker_for and runtime.subagents to post the child result, while relying on later sweeps to retry if delivery is deferred.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_run_turn`  (lines 731–1024)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs the actual agent engine for a single turn. This is the central assembly line that turns a database row into a live model run with tools, sandbox access, skills, billing, and output rules.

**Data flow**: It receives the runtime and turn id. It claims the turn, loads the turn, resolves agent or subagent settings, chooses and freezes model pricing, assembles the prompt and tool environment, decides whether workspace-owned keys are used, prepares sandbox authorization, mounts skills and files, constructs TurnEngine, and runs either an ordinary turn or an intent turn. It returns statuses such as failed, parked, superseded, or the engine’s terminal status.

**Call relations**: _execute_turn calls this inside workspace, agent, speaker, and tracing context. It calls many helpers in this file for loading, lineage, model choice, billing freeze, BYOK freeze, sandbox setup, failure backstop, and skill shadow logging, then hands the fully prepared bundle to TurnEngine.

*Call graph*: calls 11 internal fn (_commit_failed_terminal, _fire_shadow_selection, _frozen_billing_identity, _frozen_byok, _load_turn, _member_accounts_connectable, _member_skill_turn, _previous_turn_ended_at, _resolve_profile, _run_lineage (+1 more)); called by 1 (_execute_turn); 26 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 769–773)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates an authorized subagent launcher for a specific acting member. This lets tools spawn child agents under the right member authority.

**Data flow**: It receives an optional acting member id. It asks the Subagents helper to authorize that member, then returns both the spawn interface and the authorized Subagents object.

**Call relations**: This nested helper is created inside _run_turn and passed into TurnEngine. When the engine needs member-scoped subagent behavior, it calls through this authorization wrapper.


##### `_commit_failed_terminal`  (lines 1027–1088)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Writes and publishes a failed final result when something goes wrong outside the turn engine’s own failure handling. This prevents clients from waiting forever on a turn that failed during setup.

**Data flow**: It receives the event hub, turn id, and error. It builds a terminal failure frame, repeatedly tries to update queued or running database rows to failed, records a metric and stack log only when it really transitions the row, publishes the terminal event, and uses exponential backoff if the write or publish path fails.

**Call relations**: _execute_turn and _run_turn both call this as a safety net. It publishes through Hub so listeners learn the turn ended even if the engine never got far enough to publish its own terminal.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 10 external calls (__init__, __init__, sleep, update, workspace_tx, emit_metric, formatted_stack, log, log_error, turn_profile).


##### `turn_workflow`  (lines 1092–1093)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Exposes turn execution as a DBOS durable workflow. DBOS can replay this workflow after crashes, which is why the heavy engine work is arranged beneath it.

**Data flow**: It receives workspace and turn ids as strings from the queue/workflow system. It simply awaits _execute_turn and returns that status.

**Call relations**: This is the queue-facing entry for turn execution. Its only job is to hand off to _execute_turn inside the named durable workflow.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 1096–1189)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Loads the full turn record, its agent, and the conversation audience from the database. It converts raw database JSON into typed objects used by the rest of the runtime.

**Data flow**: It receives a turn id. It queries the turn, agent, and conversation tables, builds a Turn object, builds an Agent object, parses audience information, and returns all three.

**Call relations**: _run_turn calls this after claiming a turn, and also when a claim shows the turn was already superseded so transcript repair can still work with typed turn data.

*Call graph*: called by 1 (_run_turn); 9 external calls (__init__, __init__, model_validate, model_validate, model_validate, model_validate, select, workspace_tx, parse_audience).


##### `_run_lineage`  (lines 1192–1224)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: Finds where a spawned turn’s live activity should be published. For child turns, it walks up to the root turn so user interfaces can tail one coherent activity stream.

**Data flow**: It receives a Turn. If the turn has no parent, it returns none. Otherwise it follows parent_turn_id links in the database until it finds the root, determines a profile label, and returns a RunLineage object with root, parent, profile, and display name.

**Call relations**: _run_turn calls this before creating TurnEngine. The resulting lineage tells the engine how to label and route activity from subagents or spawned agent children.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 1227–1239)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: Finds when the previous turn in the same conversation ended. This gives the engine timing context for the new turn.

**Data flow**: It receives a Turn. If this is the first turn in the conversation, it returns none. Otherwise it queries the prior sequence number’s updated time and ensures the returned datetime has timezone information.

**Call relations**: _run_turn calls this for non-intent turns. The timestamp is passed into TurnEngine as previous_turn_ended_at.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_frozen_billing_identity`  (lines 1242–1263)

```
async def _frozen_billing_identity(turn_id: UUID, candidate: _BillingIdentity) -> _BillingIdentity
```

**Purpose**: Freezes the model and price information used for one workflow attempt. This keeps crash replay from billing the same attempted work under changed prices or a changed model.

**Data flow**: It receives a turn id and candidate billing identity. It locks and reads the stored billing identity. If the stored identity matches the current attempt, it returns that. Otherwise it writes the candidate into the turn row and returns it.

**Call relations**: _run_turn calls this after resolving the model and current price. The returned identity is then used to construct the Pricing object passed to TurnEngine.

*Call graph*: called by 1 (_run_turn); 4 external calls (model_dump, select, update, workspace_tx).


##### `_frozen_byok`  (lines 1266–1315)

```
async def _frozen_byok(turn_id: UUID, decided: bool, attempt: str) -> bool
```

**Purpose**: Freezes whether this attempt used a workspace-owned model key. BYOK means “bring your own key,” where the workspace supplies its own provider credentials.

**Data flow**: It receives the turn id, the freshly decided true/false value, and the attempt id. It reads the stored BYOK decision. If that decision belongs to this attempt, it returns it. Otherwise it tries to write the new attempt’s decision, rereads the row, and returns the settled value.

**Call relations**: _run_turn calls this after checking whether the workspace has a stored key for the resolved model. The frozen answer is passed to TurnEngine so billing stays consistent across replay.

*Call graph*: called by 1 (_run_turn); 4 external calls (or_, select, update, workspace_tx).


##### `_open_sandbox`  (lines 1318–1381)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens the conversation sandbox for a turn and fills it with the environment variables needed for safe tool execution. A sandbox is the isolated workspace where commands can run without touching the host directly.

**Data flow**: It receives sandbox services, token codec, turn, optional grant store, CLI credentials, credential store, credential slots, and a cache flag. It creates a signed run token, builds environment variables for conversation id, tool bridge URL, git proxy auth, grant-backed CLI credentials, and provider keys, then opens the sandbox session for the conversation.

**Call relations**: _run_turn provides this as the open callback for _LateSandbox. It calls the sandbox provider and several credential-environment helpers only when the sandbox is actually needed.

*Call graph*: calls 2 internal fn (open, encode); 7 external calls (__init__, span, cache_git_config, _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env).


##### `SandboxAuthorizer.authorize`  (lines 1392–1404)

```
async def authorize(self, acting_member_id: UUID | None) -> Sandbox
```

**Purpose**: Re-authorizes an existing sandbox for a particular acting member. This lets later tool calls run with the right member-scoped grants without reopening the whole sandbox.

**Data flow**: It receives an optional acting member id. It creates a signed run token containing workspace, turn, and acting member, builds updated grant CLI environment variables, and asks the sandbox object to authorize with those values while clearing old CLI variables.

**Call relations**: _run_turn creates a SandboxAuthorizer and passes its authorize method to TurnEngine for ordinary turns. When the engine needs sandbox access under a member’s authority, this method supplies the fresh token and environment.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### Admission Decisions
Handles the conversation front door by deciding whether incoming messages should start, join, wait, be ignored, or be refused.

### `core/src/ufo/runtime/surfaces/admission.py`

`domain_logic` · `request handling and background job admission`

A “turn” is one unit of conversation work: a member message, a scheduled event, or an internal callback that the agent should answer. This file makes sure every turn enters the system through the same gate. That matters because the gate checks important rules that must not be bypassed: the conversation must stay bound to its original agent, the speaker must be allowed to act, billing limits must be respected, duplicate deliveries must not create duplicate work, and replies for durable surfaces must be written back later.

The main class, `Admission`, works like a reception desk. It first locks the conversation row in the database, so two arrivals cannot grab the same turn number at once. It then checks whether the same idempotency key has already been seen. An idempotency key is a “same message” label used when a sender retries after a network failure. If the message is new, admission either creates a fresh turn or, if another turn is already alive, stores the message as an arrival for that live turn to read later.

It also decides whether the turn should run now, park for later, or finish immediately as cancelled with a human-readable reason. Finally, if the turn should run, it puts it on the DBOS workflow queue, which is the durable background-work system. Small wrapper classes expose safer, narrower versions of this power to surfaces, internal jobs, and connect callbacks.

#### Function details

##### `_refused`  (lines 142–153)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: Decides what should happen when a turn is not allowed to proceed. If the turn already represents paid-for work that must not be lost, it parks the turn; otherwise it cancels it with a message explaining why.

**Data flow**: It receives a flag saying whether the turn carries already-finished work and a refusal message. If the work must be preserved, it returns a parked status and no final reply. If not, it creates a terminal frame, which is the stored final outcome of a turn, saying the turn was cancelled and why.

**Call relations**: This helper is used by `Admission._create_turn` when checks such as archived app, missing seat, or billing limits refuse a turn. It gives that larger turn-creation step a consistent way to choose between “wait” and “cancel.”

*Call graph*: called by 1 (_create_turn); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 164–208)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Admits a message that came from a real member-facing surface, such as a chat or comment surface. It validates member-specific details and then asks the shared admission path to decide whether the message starts work or joins existing work.

**Data flow**: It receives workspace and conversation IDs, the message body, the speaking member, optional retry key, context, intent, comment, and runtime settings. It checks that prepared intents match the body and that comments are not empty, then passes the message into `_admit`. After admission, it may publish live hub notifications so connected clients can see a queued arrival or a surface comment.

**Call relations**: `Admission.redispatch` calls this when it needs to give an old pending member message another chance. Internally, this method hands the real decision to `Admission._admit`, then uses hub messages such as `ArrivalQueued` and `Reply` to notify live listeners when appropriate.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 4 external calls (__init__, __init__, model_dump_json, span).


##### `Admission.redispatch`  (lines 210–259)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Finds an old pending member message that did not get fully processed and tries to admit it again. This lets a message whose original target died or was cancelled get its own fresh chance to run.

**Data flow**: It opens a workspace database transaction and looks for the oldest unconsumed inbound member message in the conversation. If the row lacks an idempotency key, it stamps one onto it so retries remain safe. It then calls `admit_member` with the stored message and returns the new turn and arrival IDs only if that re-admission opened a run.

**Call relations**: This function sits after normal admission and before engine recovery. It reads and updates the inbound-message table directly, converts stored context back into a `TurnContext`, and then reuses `Admission.admit_member` so recovery follows the same rules as a normal resend.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 261–344)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=Non
```

**Purpose**: Admits an internal turn, meaning work requested by the system, an extension, a scheduled fire, or another agent rather than directly by a member. It can also refuse to run if a member has already replied since the caller started waiting.

**Data flow**: It receives the workspace, conversation, asserted agent, message, retry key, context, authority information, scheduling flags, member-wait watermarks, and runtime settings. It passes these into `_admit`. If `_admit` reports that a member already superseded the wait, this method returns `None`; otherwise it returns the admitted turn ID.

**Call relations**: This is the public internal-entry path into the shared admission machinery. It delegates all real admission decisions to `Admission._admit` and translates the private `_SupersededByMember` signal into the public meaning: no turn was admitted because the member got there first.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 346–547)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, o
```

**Purpose**: This is the central admission algorithm. It decides whether an incoming message is a duplicate, a fold into a live turn, a new queued turn, a parked turn, or a cancelled turn.

**Data flow**: It receives all admission details: who is speaking, which conversation and agent are involved, optional idempotency and context, billing-related flags, wait watermarks, comments, and runtime settings. Inside one locked database transaction, it loads the conversation, confirms the agent binding, checks archived status and speaker validity, deduplicates retries, checks whether a member already spoke, tries to fold into a live turn, or creates a new turn. It records comments, decides whether dispatch should happen now, commits the database work, then calls `_finish_admission` to emit metrics and enqueue work.

**Call relations**: `Admission.admit_member` and `Admission.invoke` both funnel into this function so every kind of caller gets the same safety checks. It coordinates `_validate_member_watermarks`, `_deduplicate`, `_guard_member_watermark`, `_fold_live`, `_create_turn`, `_record_comment`, and `_finish_admission` as the main assembly line for admission.

*Call graph*: calls 7 internal fn (_create_turn, _deduplicate, _finish_admission, _fold_live, _guard_member_watermark, _record_comment, _validate_member_watermarks); called by 2 (admit_member, invoke); 8 external calls (__init__, __init__, __init__, exists, select, update, workspace_tx, uuid4).


##### `Admission._guard_member_watermark`  (lines 549–584)

```
async def _guard_member_watermark(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, deduped: _ExistingTurn | None, turn_watermark: int | None, arrival_watermark: int | None
```

**Purpose**: Checks whether an internal wait should be cancelled because a member has already spoken since the wait began. This prevents a timer or callback from resuming a wait after the human already answered it.

**Data flow**: It receives the database connection, workspace and conversation IDs, any already-deduplicated turn, and two sequence watermarks. If there is no fresh admission to check, or no watermarks, it does nothing. Otherwise it queries for either a newer member-founded turn or a newer member arrival row. If it finds one, it raises `_SupersededByMember`.

**Call relations**: `Admission._admit` calls this after deduplication and before folding or creating a new turn. The raised signal travels up through `Admission.invoke`, which turns it into `None` for callers waiting on a member.

*Call graph*: called by 1 (_admit); 3 external calls (exists, execute, select).


##### `Admission._validate_member_watermarks`  (lines 587–591)

```
def _validate_member_watermarks(turn_watermark: int | None, arrival_watermark: int | None) -> None
```

**Purpose**: Makes sure callers do not ask an incomplete “has the member spoken?” question. A member message can appear either as a new turn or as an arrival folded into a live turn, so both sequence markers are required together.

**Data flow**: It receives the turn watermark and arrival watermark. If exactly one is present, it raises a `ValueError`. If both are present or both are absent, it returns without changing anything.

**Call relations**: `Admission._admit` calls this before doing database work. It protects the later `_guard_member_watermark` check from silently watching only half of the places where a member reply could land.

*Call graph*: called by 1 (_admit).


##### `Admission._finish_admission`  (lines 593–625)

```
async def _finish_admission(self, workspace_id: UUID, conversation_id: UUID, surface: str, turn_id: UUID, status: TurnStatus | None, admitted: Admitted, counted_source: TurnAdmissionSource | None, fol
```

**Purpose**: Performs the after-commit side effects of admission: count the admitted turn and, when needed, place it on the background workflow queue.

**Data flow**: It receives the admitted result, the turn status, surface name, source type, and dispatch flags. If a new turn was counted, it emits a metric. If a parked turn was revived by a folded message, it enqueues that turn with a fresh workflow ID. If the admitted turn is queued and ready to dispatch now, it enqueues it. It returns the same admitted result to the caller.

**Call relations**: `Admission._admit` calls this once the database transaction has committed. This separation keeps the durable database decision first, then hands runnable work to `_enqueue` and reports metrics through the observability system.

*Call graph*: calls 1 internal fn (_enqueue); called by 1 (_admit); 2 external calls (emit_metric, uuid4).


##### `Admission._deduplicate`  (lines 627–732)

```
async def _deduplicate(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, idempotency_key: str | None, runtime_config: TurnRuntimeConfig | None, inbound: _In
```

**Purpose**: Uses an idempotency key to prevent duplicate deliveries from creating duplicate turns or duplicate queued arrivals. This is what makes retries safe.

**Data flow**: It receives a database connection, conversation and agent identity, optional idempotency key, runtime config, inbound message, and optional comment. With no key, it returns “nothing found.” With a key, it first looks for an existing turn. If found, it verifies the conversation, agent, and runtime config match, then returns that existing turn. If no turn exists, it looks for an inbound-message row with the same key. It may return the already-admitted target, recover the stored inbound message, or delete a stale queued row so the message can be re-admitted.

**Call relations**: `Admission._admit` calls this early, before seat checks, folding, or turn creation. When a duplicate already maps to a result, this function may call `_record_comment` and let `_admit` return immediately.

*Call graph*: calls 1 internal fn (_record_comment); called by 1 (_admit); 9 external calls (__init__, __init__, __init__, model_validate, model_validate, replace, delete, execute, select).


##### `Admission._fold_live`  (lines 734–890)

```
async def _fold_live(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, agent_id: UUID, archived: bool, member_admission: bool, holds_wo
```

**Purpose**: Tries to attach a new inbound message to an already-live turn instead of starting a separate turn. This keeps one conversation turn from closing while unread messages are waiting behind it.

**Data flow**: It receives the current admission details and looks for the oldest non-terminal turn in the conversation. If there is no live turn, or the speaker, seats, archived state, runtime config, or spending checks do not allow folding, it returns an empty fold result. If folding is allowed, it creates an inbound-message row with its own sequence number and idempotency key. If the live turn is running, it records any comment and returns an admitted result. If the live turn was parked, it marks that turn queued again and returns the parked turn ID so it can be re-enqueued.

**Call relations**: `Admission._admit` calls this before creating a new turn, except for cases that must stand alone, such as scheduled or intent admissions. This function uses seat checks, spend checks, balance checks, logging, and `_record_comment` to turn a fresh message into a safe arrival for an existing turn.

*Call graph*: calls 1 internal fn (_record_comment); called by 1 (_admit); 13 external calls (__init__, __init__, __init__, __init__, __init__, model_validate, execute, insert, select, update (+3 more)).


##### `Admission._create_turn`  (lines 892–1036)

```
async def _create_turn(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, surface: str, agent_id: UUID, archived: bool, member_admission
```

**Purpose**: Creates a new turn row in the database and decides its initial state: queued, parked, or cancelled. This is where a message becomes durable work.

**Data flow**: It receives the locked database connection plus the workspace, conversation, surface, agent, speaker, authority, billing, runtime, and inbound-message details. It assigns the next conversation sequence number, derives the stable turn ID, preserves subagent identity when needed, checks archived app status, speaker resolution, seat access, spending limits, and balance availability. It inserts the turn row, gives the conversation a title if it does not have one, and creates a writeback row for durable surfaces. It returns the created turn ID, sequence, status, and source.

**Call relations**: `Admission._admit` calls this when the message is not a duplicate and was not folded into a live turn. This function uses `_refused` for refusal behavior and relies on billing, seat, schema, and tracing helpers to make the new turn complete enough for later dispatch.

*Call graph*: calls 1 internal fn (_refused); called by 1 (_admit); 16 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_dump, execute, insert, select (+6 more)).


##### `Admission._record_comment`  (lines 1038–1081)

```
async def _record_comment(self, connection: AsyncConnection, workspace_id: UUID, admitted: Admitted, comment: str | None, message_ref: UUID | None=None) -> Admitted
```

**Purpose**: Stores an optional surface comment as a mid-turn reply linked to the admitted turn or arrival. This lets comments travel through the same durable reply path as other visible turn output.

**Data flow**: It receives a database connection, workspace ID, an `Admitted` result, optional comment text, and an optional message reference. If there is no comment, it returns the admitted result unchanged. If there is a comment, it builds a stable reply ID, inserts a mid-turn reply if it does not already exist, and returns a new admitted result containing the comment ID when the insert succeeded.

**Call relations**: `Admission._admit`, `_deduplicate`, and `_fold_live` call this whenever a member admission or duplicate delivery may carry a surface comment. It uses a conflict-safe insert so repeating the same delivery does not duplicate the comment.

*Call graph*: called by 3 (_admit, _deduplicate, _fold_live); 3 external calls (__init__, execute, mid_turn_reply_id_for).


##### `Admission._enqueue`  (lines 1083–1130)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places a queued turn onto the DBOS background workflow queue so a worker can run it. DBOS is the durable job system used here to run turns reliably.

**Data flow**: It receives workspace, conversation, and turn IDs plus an optional workflow ID. It reads the turn’s parent and admission source to choose the correct queue name, then asks DBOS to enqueue the workflow. If enqueueing is cancelled or fails, it clears the turn’s dispatch marker in the database so another attempt can happen later, and logs non-cancellation failures.

**Call relations**: `Admission._finish_admission` calls this after admission decides a turn should run. This function is the bridge from admission’s database state to the actual workflow runner, using `turn_queue_for` to route different turn kinds correctly.

*Call graph*: called by 1 (_finish_admission); 5 external calls (select, update, workspace_tx, log, turn_queue_for).


##### `AdmissionInvoker.invoke`  (lines 1141–1170)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=None, holds_work_alr
```

**Purpose**: Provides a workspace-bound way for internal jobs and extension workflows to invoke a turn. It deliberately does not allow those callers to pretend a member directly spoke the message.

**Data flow**: It receives a conversation, agent, message, optional retry key, context, authority, scheduling flags, wait watermarks, and runtime settings. It adds the stored workspace ID from the wrapper and forwards everything to the underlying `Admission.invoke`. The result is the new or existing turn ID, or `None` if a member reply superseded the wait.

**Call relations**: This wrapper is handed to trusted internal code instead of the full `Admission` object. Its role is to narrow what the caller can do while still using the same shared internal-admission path.


##### `MemberAdmission.admit`  (lines 1181–1203)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Provides a workspace-bound way for member-facing surfaces to admit member messages. It forces the call to include a speaking member and routes the message through member-specific admission checks.

**Data flow**: It receives a conversation, message, optional retry key, context, required speaker member ID, optional intent, comment, and runtime settings. It adds the stored workspace ID and forwards the request to `Admission.admit_member`. The result tells the surface which turn was admitted, whether a run opened, and whether the message became an arrival.

**Call relations**: Surfaces receive this narrower capability instead of the full `Admission` object. That keeps surface code on the member-admission path, where seat checks, speaker checks, comments, and live hub notifications are applied consistently.


##### `ConnectResume.resume`  (lines 1231–1266)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Admits the result of an external account-connect callback back into the conversation that requested it. It returns whether the resume message was actually admitted.

**Data flow**: It receives the conversation, result message, speaking member, and required idempotency key. It first checks the latest turn lane; if the lane is for prepared intents, it declines because free-text resume messages do not belong there. Otherwise it reads the current workspace, tries to admit the message as that member, and returns `true` on success. If admission fails, it logs the failure and returns `false`.

**Call relations**: This class is used after a member leaves the app flow for a browser consent page and comes back. It relies on `Admission.admit_member` for the real admission rules, while using workspace transaction and logging helpers to safely decline or report failures.

*Call graph*: 4 external calls (select, workspace_tx, log, ws_current).


### `core/src/ufo/runtime/turns/ambient_reply.py`

`domain_logic` · `pre-admission turn gating`

In a busy group chat thread, not every new message is meant for the agent. After the agent has joined a thread once, later replies could be answers to the agent, questions for another person, thanks, corrections, or just people talking among themselves. If every such message automatically became a full agent turn, the system would spend money and attention on replies like “nothing from me” or “standing by.”

This file puts a small gate in front of that. It asks a cheaper model call a very narrow question: should this message become a new agent turn, yes or no? The answer is limited to one of two words: REPLY or NO_REPLY.

To make that decision fair, the classifier sends the model a small, recent slice of the thread. Each message says who wrote it, whether it was written by the agent, and the text. Long history is trimmed, and a new message that is too long is not silently shortened for decision-making; instead, classification fails so the caller can choose the safer path of admitting the turn.

The prompt is careful to treat chat text as untrusted. It wraps the thread as JSON between fence lines, like putting evidence in a sealed envelope, so a user’s message cannot pretend to be an instruction to the classifier. If the model’s answer cannot be read, this file raises an error rather than guessing, because an accidental silence is worse than an extra reply.

#### Function details

##### `MeteredModel.model`  (lines 95–95)

```
def model(self) -> str
```

**Purpose**: This protocol property names the model that will be used for the small reply-or-do-not-reply decision. It lets this file depend on a simple promise instead of importing a heavier model-access object directly.

**Data flow**: A concrete model wrapper provides its configured model name. The classifier reads that name and places it into the model request. Nothing is changed by reading it.

**Call relations**: When AmbientReplyClassifier.decide builds the request for the language model, it relies on this property to know which model to call. The protocol keeps the classifier connected to model access without creating a tight dependency on the larger runtime wiring.


##### `MeteredModel.complete`  (lines 97–97)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This protocol method represents one paid model call that returns plain text. Here, it is used to ask the classifier model for a single decision word.

**Data flow**: It receives a ModelRequest containing the system instructions, the thread payload, token limits, and reasoning settings. The concrete model service sends that request to the model and returns the model’s text answer. The method itself, as a protocol entry, only defines that shape of interaction.

**Call relations**: AmbientReplyClassifier.decide calls this method after preparing the payload. The returned text is then checked for REPLY or NO_REPLY so the caller can decide whether to found a new turn.


##### `_entry`  (lines 100–105)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This helper turns one AmbientMessage into the small dictionary format sent to the classifier model. It keeps only the speaker, whether the agent wrote it, and a bounded amount of text.

**Data flow**: It takes an AmbientMessage as input. It copies the speaker and own flag, and cuts the text down to the configured per-message character limit. It returns a plain dictionary ready to be placed inside the JSON payload.

**Call relations**: AmbientReplyClassifier._payload calls this helper for every recent history message and for the new message. It is the small adapter that turns internal message objects into the safe, compact evidence packet the model will read.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 118–136)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision point: it asks whether one ambient message should create a new agent turn. It uses a cheap, focused model call and returns either REPLY or NO_REPLY.

**Data flow**: It receives the new message and recent thread history. First it rejects a new message that is too long, because deciding from a shortened version could be misleading. Then it builds a payload from the message and history, sends a ModelRequest to the configured model, searches the answer for REPLY or NO_REPLY, and returns the last valid decision word it finds. If the model fails to give a readable decision, it raises an error instead of silently choosing.

**Call relations**: A chat surface or turn-admission layer calls this before starting a new turn for an unmentioned thread reply. The function hands payload construction to AmbientReplyClassifier._payload, wraps that payload in a user Message and ModelRequest, sends it through MeteredModel.complete, and gives the resulting decision back to the caller. If it cannot decide safely, the caller is expected to settle that failure, typically by allowing the turn rather than risking an ignored request.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 138–154)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This method builds the exact text sent to the classifier model: a compact JSON object containing recent thread history and the new message. Its job is to give the model enough context while keeping user-written chat text from being mistaken for instructions.

**Data flow**: It takes the new message and the available history. It keeps only the latest configured number of history messages, converts each message through _entry, serializes everything as JSON, then wraps the JSON between matching fence lines. If the chosen fence text appears inside the payload, it lengthens the fence until it is unique. The result is a single string ready to send as the model’s user message.

**Call relations**: AmbientReplyClassifier.decide calls this method just before making the model request. This method calls _entry to format each message and json.dumps to produce safe JSON text. It supplies the sealed, structured context that lets the classifier apply the reply rules without treating chat content as trusted prompt text.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### Conversation Dispatch Gating
Starts the next eligible waiting turn only when the conversation has no other turn already running.

### `core/src/ufo/runtime/turns/dispatch.py`

`orchestration` · `between turns, when a workflow ends or a dispatcher retry sweep offers queued work`

A “turn” is one unit of work in a conversation, like one customer taking their turn at a service desk. This file makes sure only one turn for the same conversation is sent to the background workflow system at a time. Without it, two replies or actions in the same conversation could run at once and step on each other.

The main function opens a database transaction and locks the conversation row. A database transaction is a safe bundle of database work that either all succeeds together or does not count. The lock is like putting a “do not touch” sign on that conversation while deciding what should happen next. It then checks whether any turn is already marked as running. If so, it stops.

If nothing is running, it looks for the earliest queued turn. If that turn has already been marked as offered to the workflow queue, it also stops, which prevents duplicate dispatch. Otherwise it stamps the turn with a dispatch time, commits that decision, and then asks DBOS, the background workflow runner, to enqueue the turn.

One important detail: if the turn had been claimed before, it gets a fresh workflow id. That avoids DBOS treating it as an already-finished duplicate. If enqueueing fails, the file clears the dispatch stamp so a later sweep or retry can offer the turn again, then logs the delay.

#### Function details

##### `dispatch_next_turn`  (lines 28–98)

```
async def dispatch_next_turn(client: DBOSClient, conversation_id: UUID) -> None
```

**Purpose**: This function finds the next queued turn for a conversation and sends it to the background workflow queue, but only if no turn in that conversation is already running. It is used to preserve the order of a conversation while safely moving work forward.

**Data flow**: It receives a DBOS client, which can enqueue background workflows, and a conversation id. It reads the database under a conversation lock, checks for a running turn, then selects the earliest queued turn. If there is no eligible turn, or it was already stamped as enqueued, nothing changes. If there is an eligible turn, the function marks it with a dispatch time, chooses the right queue and workflow id, and asks DBOS to run it. If that enqueue request fails, it reopens the database, removes the dispatch mark from the still-queued turn, and writes a log entry so the system can try again later.

**Call relations**: This function sits at the handoff point after a turn finishes, parks, fails, is cancelled, or is rediscovered by a dispatcher sweep. It uses `workspace_tx` to make the database decision safely, SQLAlchemy queries and updates to inspect and stamp turns, `turn_queue_for` to choose the correct worker queue, and `DBOSClient.enqueue_async` to actually start the workflow. If enqueueing cannot be completed, it calls the project logger to record that dispatch was deferred.

*Call graph*: 7 external calls (enqueue_async, select, update, workspace_tx, log, turn_queue_for, uuid4).
