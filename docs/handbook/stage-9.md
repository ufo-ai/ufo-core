# Agent turn execution loop  `stage-9`

This stage is the main work loop for one agent turn: a single piece of queued work in a conversation. It starts when queue.py claims a turn from the durable queue, meaning a work list that survives crashes. It builds the needed model, tools, sandbox, billing, and permissions, then hands control to engine.py. The engine is the safety rail. It loads the conversation, runs the turn, saves progress, and records a final result even if something fails.

Before the model answers, the context and compaction parts gather the conversation history and shrink older material into summaries when the model’s reading limit would be exceeded. Then agent.py runs the core loop: send messages to the model, stream the reply, run any requested tools, and decide whether more rounds are needed. The model request stage cleans and records streamed output as it arrives.

hooks.py lets extensions inspect or block tool actions at key moments. Finally, the transcript and publication parts save the timeline, costs, file changes, and final answer, while live updates are sent to viewers.

## Sub-stages

- [Context loading and conversation compaction](stage-9.1.md) `stage-9.1` — 2 files
- [Model request, stream handling, and intent runs](stage-9.2.md) `stage-9.2` — 2 files
- [Transcript, cost, and final-result publication](stage-9.3.md) `stage-9.3` — 5 files

## Files in this stage

### Queued turn runtime
Claims queued work, assembles runtime dependencies, and drives a recoverable agent turn through the engine.

### `core/src/ufo/runtime/queue.py`

`orchestration` · `turn execution`

A “turn” is one piece of work for the agent, like replying to a user message or running a spawned subagent. This file is the traffic controller and launch crew for those turns. It makes sure only the right process runs a turn, limits scheduled background waves so they do not crowd out interactive work, loads the turn and agent from the database, chooses the correct model, freezes billing rules for the attempt, prepares the sandbox, and then hands everything to the TurnEngine that does the actual model/tool loop.

It also protects several important promises. Billing is frozen per attempt so a crash replay or resume does not silently change who pays. Subagents that must use a member’s own connected AI account are stopped if that account disappears or is rate limited, instead of falling back to the platform key. Workspace skills and tools are filtered so the model only sees what this turn is allowed to use. If sandbox setup cannot happen because the provider is temporarily unavailable, the turn can be “parked” for later rather than failed.

The file is also a safety net. If anything breaks before the engine can write a normal terminal result, it retries writing a failed terminal record and publishes it to listeners, so clients waiting for the turn are not left hanging.

#### Function details

##### `_turn_gates`  (lines 157–173)

```
def _turn_gates(parent_turn_id: UUID | None, admission_source: TurnAdmissionSource) -> tuple[asyncio.Semaphore, ...]
```

**Purpose**: Decides whether a turn must wait for a fairness slot before it runs. It mainly slows large batches of scheduled root turns so they cannot fill the whole worker process and block member-facing messages.

**Data flow**: It receives the turn’s parent id and admission source. If the turn is not a root scheduled turn, it returns no locks. If it is a root scheduled turn, it finds or creates an event-loop-local semaphore, which is a small counter that only lets a limited number of tasks pass at once, and returns it.

**Call relations**: _execute_turn asks this before running a claimed turn. The gates it returns are held around provisioning and execution setup so scheduled work shares capacity fairly.

*Call graph*: called by 1 (_execute_turn); 2 external calls (Semaphore, get_running_loop).


##### `_Rates.of`  (lines 201–209)

```
def of(cls, price: ModelPrice) -> '_Rates'
```

**Purpose**: Copies a model price card into the small serializable rate shape used for stored billing identity. This lets billing details be saved on the turn row.

**Data flow**: It receives a ModelPrice with token prices. It copies each price field into a _Rates object. The output is a compact rate record with the same numbers.

**Call relations**: _TurnBilling._identity uses this when freezing the starting model’s rates and any alternate model rates for the turn attempt.


##### `_Rates.price`  (lines 211–219)

```
def price(self) -> ModelPrice
```

**Purpose**: Turns the stored rate record back into a ModelPrice object. This is used when reconstructing the pricing table for the engine.

**Data flow**: It reads the integer rate fields from the _Rates instance. It places them into a new ModelPrice. The result is the pricing object expected by the model and billing code.

**Call relations**: _BillingIdentity.pricing calls this for the main model and alternates so the rest of the runtime can price usage from the frozen billing record.

*Call graph*: 1 external calls (__init__).


##### `_BillingIdentity.pricing`  (lines 235–242)

```
def pricing(self) -> Pricing
```

**Purpose**: Builds the full pricing table that a turn attempt should use. It combines the frozen main model rate and any frozen alternate rates under one digest.

**Data flow**: It reads the billing identity’s model name, rate fields, alternate rates, and price digest. It converts each stored rate back into a ModelPrice. It returns a Pricing object that the engine can use consistently for all token accounting.

**Call relations**: _run_turn passes this pricing into TurnEngine after _TurnBilling.resolve has produced a valid billing identity.

*Call graph*: 1 external calls (__init__).


##### `_plan_pricing`  (lines 245–246)

```
def _plan_pricing(models: tuple[str, ...]) -> Pricing
```

**Purpose**: Creates a zero-dollar pricing table for plan-funded model use. This is for cases where the member or plan already pays outside per-token platform billing.

**Data flow**: It receives model names. It assigns each one the shared zero-rate plan-served price card. It returns a Pricing object with a digest describing that table.

**Call relations**: _TurnBilling._identity uses it when the resolved model is plan-funded, and _TurnBilling._validate uses it to check that a stored billing identity still matches the model’s funding type.

*Call graph*: called by 2 (_identity, _validate); 1 external calls (pricing_from).


##### `_TurnBilling.resolve`  (lines 257–271)

```
async def resolve(self) -> tuple[_BillingIdentity, ResolvedModelClient, bool]
```

**Purpose**: Finds, freezes, and validates the billing setup for one turn attempt. It ensures the model, payer, and bring-your-own-key verdict do not drift during crash recovery or retry.

**Data flow**: It starts with a registry, turn id, attempt id, candidate model, and possible alternates. It reads any stored billing identity for this attempt; if none exists, it resolves the candidate model and writes a new frozen identity. It validates that the resolved client still matches the frozen payer and funding facts, freezes the BYOK decision, and returns the billing identity, model client, and BYOK flag.

**Call relations**: _run_turn calls this after choosing the model name but before assembling and running the engine. It hands off to _stored_billing_identity, _frozen_billing_identity, _identity, _validate, and _frozen_byok to make the decision durable.

*Call graph*: calls 5 internal fn (_identity, _validate, _frozen_billing_identity, _frozen_byok, _stored_billing_identity); 1 external calls (__init__).


##### `_TurnBilling._identity`  (lines 273–289)

```
def _identity(self, model: str, resolved: ResolvedModelClient) -> _BillingIdentity
```

**Purpose**: Builds the candidate billing identity for a model before it is frozen on the turn. It records which model is being used, what its prices are, and who is expected to fund it.

**Data flow**: It receives a model name and resolved model client. It chooses either zero plan pricing or the registry’s normal pricing, copies the main and alternate model rates, and creates a _BillingIdentity. The output is ready to compare with or store on the turn row.

**Call relations**: _TurnBilling.resolve calls this only when no matching stored billing identity already exists for the attempt.

*Call graph*: calls 1 internal fn (_plan_pricing); called by 1 (resolve); 1 external calls (__init__).


##### `_TurnBilling._validate`  (lines 292–299)

```
def _validate(billing: _BillingIdentity, model: ResolvedModelClient) -> None
```

**Purpose**: Checks that a frozen billing identity still agrees with the current model client. If the model’s funding or payer changed mid-attempt, it stops the run instead of billing inconsistently.

**Data flow**: It receives the frozen billing identity and the currently resolved model client. It compares funding, payer, and whether the price digest matches plan-funded expectations. It returns nothing when the identity is safe, or raises ModelFundingChanged when it is not.

**Call relations**: _TurnBilling.resolve calls this after loading or creating the billing identity and before returning the model client to _run_turn.

*Call graph*: calls 1 internal fn (_plan_pricing); called by 1 (resolve); 1 external calls (__init__).


##### `_without_workspace_skills`  (lines 302–305)

```
async def _without_workspace_skills(name: str) -> None
```

**Purpose**: Represents a disabled workspace-skills loader. It deliberately loads nothing when an agent setting says workspace skills should not be used.

**Data flow**: It receives a skill name but ignores it. It returns None and changes nothing. The before-and-after story is intentionally no-op: no saved workspace skill enters the prompt or tools through this route.

**Call relations**: This is a helper for skill-loading paths outside the shown call graph. It exists as a safe replacement function when workspace skills are switched off.


##### `_member_skill_turn`  (lines 308–320)

```
def _member_skill_turn(turn: Turn) -> bool
```

**Purpose**: Decides whether a turn is the kind that should receive the saved-skills member block. It excludes prepared intents and certain internal, speakerless root turns that do not correspond to a user prompt.

**Data flow**: It reads the turn’s admission source, speaker member id, and parent id. It returns false for prepared intents and for a machine-created internal root with no speaker. Otherwise it returns true.

**Call relations**: _member_skill_block uses this to decide whether to render the block, and _run_turn uses it when deciding whether to start shadow skill-selection logging.

*Call graph*: called by 2 (_member_skill_block, _run_turn).


##### `_member_skill_block`  (lines 323–330)

```
def _member_skill_block(turn: Turn, view: MemberVisibility, enabled: bool) -> str
```

**Purpose**: Returns the text block of saved member-visible skills for a turn, or an empty string when that block should not be shown. This supports experiments and settings that can turn the block off.

**Data flow**: It receives a turn, a member skill view, and an enabled flag. If the feature is disabled or the turn is not eligible, it returns an empty string. Otherwise it returns the view’s prepared block text.

**Call relations**: It relies on _member_skill_turn for eligibility. Environment assembly code can use it when composing the prompt seen by the model.

*Call graph*: calls 1 internal fn (_member_skill_turn).


##### `_prompt_skill_index`  (lines 333–337)

```
def _prompt_skill_index(skills: SkillRegistry, enabled: bool) -> tuple[tuple[str, str], ...]
```

**Purpose**: Chooses which skill index should appear in the prompt. When member skills are enabled it uses the richer prompt index; otherwise it exposes only the registry’s normal index.

**Data flow**: It receives the skill registry and an enabled flag. It either asks the selection module to build a prompt-aware index or asks the registry for its basic index. It returns a tuple of skill index entries.

**Call relations**: This is a prompt-composition helper for skill assembly. It bridges SkillRegistry and the skill-selection prompt renderer.

*Call graph*: calls 1 internal fn (index); 1 external calls (prompt_index).


##### `_fire_shadow_selection`  (lines 343–350)

```
def _fire_shadow_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Starts a background comparison of skill-selection methods without making the turn wait. This is used to gather evidence about vector search versus lexical matching.

**Data flow**: It receives the index, embedding client, turn, and skill cards. It creates an asynchronous task for _shadow_skill_selection, keeps a reference so it is not garbage-collected, and arranges to remove the reference when done. It returns immediately.

**Call relations**: _run_turn calls this for eligible main-agent turns when the catalog is too large to fit in the prompt. The actual work is handed to _shadow_skill_selection.

*Call graph*: calls 1 internal fn (_shadow_skill_selection); called by 1 (_run_turn); 1 external calls (create_task).


##### `_shadow_skill_selection`  (lines 353–379)

```
async def _shadow_skill_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Runs the background skill-selection comparison and logs the result. Its failures are intentionally harmless to the user’s turn.

**Data flow**: It embeds the turn’s inbound text, asks the vector index for matching skill owners, also computes lexical top matches, and logs both lists. If anything fails or times out, it logs a failure event instead of raising into the turn.

**Call relations**: _fire_shadow_selection launches this as a fire-and-forget task. It calls the embedding service, vector index, and lexical selector only for observation.

*Call graph*: calls 2 internal fn (embed, vector); called by 1 (_fire_shadow_selection); 3 external calls (timeout, log, select_top_k).


##### `with_implied_grants`  (lines 389–392)

```
def with_implied_grants(names: set[str]) -> set[str]
```

**Purpose**: Adds companion permissions that must travel with a named tool or action. For example, granting a skill loader also grants the search action it needs to find skills.

**Data flow**: It receives a mutable set of names. For each name already present, it looks up implied companion names and adds them to the same set. It returns the widened set.

**Call relations**: _agent_actions, _agent_tools, _subagent_actions, and _subagent_tools call this before filtering available tools or actions, so related capabilities stay usable together.

*Call graph*: called by 4 (_agent_actions, _agent_tools, _subagent_actions, _subagent_tools).


##### `_agent_actions`  (lines 395–410)

```
def _agent_actions(actions: Mapping[str, Mapping[str, BoundAction]], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> frozenset[str]
```

**Purpose**: Selects which object action ids a normal agent turn is allowed to hold. It mirrors the tool-filtering rules but works at the action id level.

**Data flow**: It receives the registered actions, an optional allowlist, admission source, and optional speaker id. With no allowlist, or for a speaking prepared intent, it returns every non-profile-only action id. With an allowlist, it expands implied grants and returns only matching canonical action ids that are actually registered.

**Call relations**: This helper uses with_implied_grants and is meant to stay in step with _agent_tools, so action dispatch and tool exposure agree.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `SubagentKeyWithdrawn.__init__`  (lines 425–440)

```
def __init__(self, profile: str, connect_url: str | None=None, rate_limited: bool=False) -> None
```

**Purpose**: Creates a clear error for a subagent profile that must run on a member’s own provider account but no usable account is available. The message points the member toward the connection screen and says whether rate limiting was the cause.

**Data flow**: It receives the profile name, optional connection URL, and a rate-limited flag. It builds a human-readable message, stores the profile and rate-limit state on the exception, and initializes the exception.

**Call relations**: _subagent_model raises this when it cannot choose a valid own-account model for a subagent.

*Call graph*: called by 1 (_subagent_model).


##### `_member_accounts_connectable`  (lines 443–445)

```
def _member_accounts_connectable(runtime: 'Runtime') -> bool
```

**Purpose**: Checks whether this deployment has any extension capable of storing a member’s own model-provider account. This affects whether own-account subagents can ask the user to connect an account.

**Data flow**: It reads the runtime’s manifests. If any manifest declares that it connects member accounts, it returns true; otherwise false.

**Call relations**: _run_turn passes this fact into the Subagents helper, and _subagent_model uses it to decide whether missing own-account access should become a SubagentKeyWithdrawn error.

*Call graph*: called by 2 (_run_turn, _subagent_model).


##### `_subagent_model`  (lines 448–479)

```
def _subagent_model(profile: SubagentProfile, connected: str | None, agent: Agent, runtime: 'Runtime', pinned: str | None, document: str | None) -> str
```

**Purpose**: Chooses the model name a subagent turn should run on. It carefully separates workspace-funded model choices from profiles that are required to spend a member’s own connected account.

**Data flow**: It receives the subagent profile, connected provider, agent, runtime, optional pinned model, and optional environment-document model. For own-account profiles, it prefers the environment document when present, otherwise maps the connected provider to its allowed model or raises SubagentKeyWithdrawn. For other profiles, it uses the pinned model, profile model, or parent agent model. It returns a resolved registry model name.

**Call relations**: _run_turn calls this after resolving the profile and connected member providers. It may call _member_accounts_connectable and raise SubagentKeyWithdrawn to prevent unsafe fallback spending.

*Call graph*: calls 2 internal fn (__init__, _member_accounts_connectable); called by 1 (_run_turn).


##### `_own_account_alternates`  (lines 482–509)

```
def _own_account_alternates(profile: SubagentProfile, connected: tuple[str, ...], chosen: str, document: str | None, runtime: 'Runtime') -> tuple[str, ...] | None
```

**Purpose**: Finds backup models for an own-account subagent if the member connected multiple provider accounts. These alternates let work move to another account the member already pays for when one account is rate limited.

**Data flow**: It receives the profile, connected providers, chosen model, optional environment-document model, and runtime. If the profile is not own-account or the environment document chose the model, it returns None. Otherwise it maps connected providers to their resolved models, verifies the chosen model is one of them, and returns the other models as alternates.

**Call relations**: _run_turn calls this after _subagent_model. Its output goes into _TurnBilling so alternate rates and account failover can be frozen with the attempt.

*Call graph*: called by 1 (_run_turn).


##### `_subagent_actions`  (lines 512–530)

```
def _subagent_actions(actions: Mapping[str, Mapping[str, BoundAction]], profile: SubagentProfile, grants: frozenset[str]) -> frozenset[str]
```

**Purpose**: Selects which object action ids a subagent profile may use. It respects the profile’s own tool names, cross-extension grants, and whether the profile is isolated.

**Data flow**: It receives registered actions, a subagent profile, and inherited grants. It builds an allowed-name set from the profile, adds grants when not isolated, expands implied grants, and returns canonical action ids that match or are default subagent actions allowed for non-isolated profiles.

**Call relations**: It uses with_implied_grants and mirrors _subagent_tools, keeping action permission decisions aligned with the tools offered to a subagent.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_with_action_verbs`  (lines 533–554)

```
def _with_action_verbs(selected: tuple[ToolDef, ...], all_tools: tuple[ToolDef, ...], granted_actions: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Adds the basic object-action discovery tools when a turn has permission for any canonical object action. Without these companion verbs, the model might be allowed to dispatch an action but unable to discover its shape.

**Data flow**: It receives selected tools, all live tools, and granted action ids. It removes the generic object-action tool if no actions are granted. If actions are granted, it adds object_action and read tools that are present in the full registry but not already selected. It returns the adjusted tool tuple.

**Call relations**: This is a tool-selection helper used by environment/tool assembly paths. It enforces the same grant idea described by _agent_actions and _subagent_actions.


##### `_agent_tools`  (lines 557–581)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses the actual tool definitions offered to a normal agent turn. It prevents general agents from seeing profile-only primitives unless an allowlist explicitly names them.

**Data flow**: It receives all tools, an optional allowlist, admission source, and optional speaker id. With no allowlist, or for a speaking prepared intent, it returns all non-profile-only tools. With an allowlist, it expands implied grants and returns only tools whose names are allowed and currently registered.

**Call relations**: It uses with_implied_grants and mirrors _agent_actions so the model’s callable tools and the action registry agree.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_resolve_profile`  (lines 584–599)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: Looks up the subagent profile named on a turn and logs useful details if the profile no longer exists. This gives operators the missing name and the profiles still registered.

**Data flow**: It receives the subagent registry, turn id, and requested profile name. It returns the matching profile if found. If not found, it logs the requested and registered profiles, then re-raises the unknown-profile error.

**Call relations**: _run_turn calls this for subagent turns before choosing the model and assembling the environment.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 602–614)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses the tool definitions offered to a subagent profile. It respects profile tool names, inherited grants, isolated-tool mode, and default subagent tools.

**Data flow**: It receives all tools, a profile, and grants. It builds and expands the allowed-name set, then filters live tools by allowed names or default-subagent status when not isolated. It returns the selected tools.

**Call relations**: It uses with_implied_grants and is the tool-level counterpart to _subagent_actions.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_apply_provisions`  (lines 620–629)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: Applies extension-shipped agent provisions to a workspace once per process. This makes sure workspaces get agents bundled by active extensions without redoing the setup on every turn.

**Data flow**: It receives the runtime and workspace id. If the workspace was already provisioned in this process, it returns. Otherwise it runs AgentProvisioning with the active manifests, then records the workspace id in an in-memory set.

**Call relations**: _execute_turn calls this before running a turn, after any fairness gates are held and before agent execution begins.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `TurnEnvironment.assemble`  (lines 699–699)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Defines the host-provided operation that builds the complete turn environment. The runtime uses it to get the prompt, tools, hooks, skills, and seeded files without knowing how environment documents are interpreted.

**Data flow**: It accepts an AssembleRequest containing the turn, agent/profile, model, skills, grants, and environment document id. An implementation returns an AssembledTurn containing the prompt, tool registry, hooks, preloaded skills, files, and member skill view.

**Call relations**: _run_turn calls the runtime’s TurnEnvironment implementation during environment assembly. This file declares the contract; another host layer provides the implementation.


##### `TurnEnvironment.environment_model`  (lines 701–701)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Defines how the host reports whether an environment document pins a model. The runtime needs this answer before billing and model validation.

**Data flow**: It accepts an environment document id and optional subagent profile name. An implementation reads whatever that document means and returns a model name or None. No document content is interpreted directly in this file.

**Call relations**: _run_turn calls this before _subagent_model and _TurnBilling.resolve when a turn has a runtime environment document.


##### `TurnEnvironment.clis`  (lines 703–703)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Defines how the host exposes command-line credential definitions that can be made available inside sandboxes. These are used when opening or re-authorizing sandbox access.

**Data flow**: It takes no input besides the environment implementation. It returns a mapping from CLI names to credential descriptions.

**Call relations**: _run_turn reads this and passes the result into _open_sandbox and SandboxAuthorizer so sandbox commands can receive the right credential helpers.


##### `TurnEnvironment.slots`  (lines 705–705)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Defines how the host lists credential slots declared by environment extensions. These slots tell sandbox setup which provider keys may be derived for a turn.

**Data flow**: It takes no input besides the environment implementation. It returns credential slot definitions.

**Call relations**: _run_turn passes these slots to _open_sandbox, which uses them with the credential store when building sandbox environment variables.


##### `init_runtime`  (lines 740–751)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the per-process Runtime object used by queued turn workers. It also seeds system skills into sandbox carriers that support preloading them.

**Data flow**: It receives a Runtime. If a runtime is already installed, it raises an error. Otherwise it packages bundled system skills, seeds them into eligible sandbox carriers and resume carriers, and stores the Runtime in the module global.

**Call relations**: The serving process calls this before any turn workflow runs. _execute_turn later reads the installed runtime; without this, turn execution fails immediately.

*Call graph*: calls 1 internal fn (from_skills).


##### `reset_runtime`  (lines 754–759)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed Runtime so tests can install a fresh one. Production serving is expected to initialize once and not reset.

**Data flow**: It takes no inputs. It sets the module-level runtime reference back to None. It returns nothing.

**Call relations**: This is a test seam around init_runtime. It is not part of the normal turn workflow.


##### `_execute_turn`  (lines 762–844)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the outer workflow body for a turn. It binds workspace context, loads enough database facts to set authority and fairness, runs the turn, and performs after-run delivery and dispatch.

**Data flow**: It receives workspace id and turn id as strings from the durable workflow. It checks that runtime is initialized, loads turn metadata, chooses authority, waits on any fairness gates, applies provisions, enters tracing and model-authority contexts, and calls _run_turn. If setup or runtime orchestration fails, it writes a failed terminal. Afterward it delivers child results and offers the next queued conversation turn. It returns a status string.

**Call relations**: turn_workflow calls this directly. It coordinates _turn_gates, _apply_provisions, _run_turn, _commit_failed_terminal, _deliver_to_parent, and _offer_next_turn.

*Call graph*: calls 6 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _offer_next_turn, _run_turn, _turn_gates); called by 1 (turn_workflow); 11 external calls (AsyncExitStack, select, monotonic, workspace_tx, emit_histogram, turn_span, agent, turn_authority, model_authority, ws (+1 more)).


##### `_offer_next_turn`  (lines 847–859)

```
async def _offer_next_turn(runtime: Runtime, conversation_id: UUID) -> None
```

**Purpose**: Asks the dispatcher to start the next queued turn for a conversation after the current workflow ends. It treats failure as a delay rather than a fatal problem.

**Data flow**: It receives the runtime and conversation id. It calls the dispatcher with the DBOS client. If that fails, it logs the error class and returns without raising.

**Call relations**: _execute_turn calls this on every exit path once it knows the conversation id. A later dispatcher sweep can recover if this immediate offer failed.

*Call graph*: called by 1 (_execute_turn); 2 external calls (log, dispatch_next_turn).


##### `_deliver_to_parent`  (lines 862–894)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: Delivers a finished child or subagent turn’s durable terminal result back to the parent conversation. It reads the database record rather than trusting the current execution’s local state.

**Data flow**: It receives the runtime and child turn id. It loads the turn row, returns if there is no parent or no terminal result, and otherwise asks SubagentResult to deliver the validated Turn. Delivery errors are logged and swallowed so they do not overwrite the child’s real terminal state.

**Call relations**: _execute_turn calls this after _run_turn or failure handling. If delivery is missed, a later sweep can find and retry it.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_SandboxSetup.run`  (lines 904–931)

```
async def run(self, sandbox: Sandbox, preload: tuple[LoadedSkill, ...], files: tuple[EnvironmentFile, ...]) -> None
```

**Purpose**: Prepares the sandbox before the engine starts by mounting preloaded skills and writing files from the environment document. If the sandbox provider is temporarily unavailable, it may park the turn for retry.

**Data flow**: It receives a sandbox, loaded skills, and files. It loads skills into the sandbox, then writes each file. If the sandbox provider is unavailable and the engine says this turn can be parked, it reads the transcript to see which messages were already absorbed, parks the turn, and raises the parking signal.

**Call relations**: _run_turn calls this after creating the sandbox wrapper and before constructing TurnEngine. It calls _SandboxSetup._park when setup should pause instead of fail.

*Call graph*: calls 2 internal fn (write_file, _park); 4 external calls (__init__, span, sandbox_provider_park, load_skills).


##### `_SandboxSetup._park`  (lines 933–972)

```
async def _park(self, parked: TurnParked, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Marks a running turn as parked and makes unabsorbed inbound messages available again. It also notifies listeners that the turn is waiting for an external sandbox provider to recover.

**Data flow**: It receives a TurnParked object and ids of absorbed messages. It updates the turn row from running to parked for the current attempt, clears consumed_turn_id on unabsorbed inbound messages, publishes a Parked event to the hub, emits a metric, and logs the park. If the row no longer belongs to this attempt, it does nothing further.

**Call relations**: _SandboxSetup.run calls this when sandbox setup hits a parkable provider outage.

*Call graph*: called by 1 (run); 6 external calls (__init__, update, workspace_tx, emit_metric, log, turn_profile).


##### `_run_turn`  (lines 975–1257)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs one claimed turn from start to finish. This is the main assembly line that turns database state into a configured TurnEngine run.

**Data flow**: It claims the turn attempt, loads the turn, agent, and audience, resolves subagents and model choices, freezes billing, asks the environment to assemble prompts/tools/skills/files, opens and authorizes sandbox access, mounts setup items, builds TurnEngine with all supporting services, and runs either a normal turn or prepared intent. It returns the terminal status, parked, superseded, or failed.

**Call relations**: _execute_turn calls this inside workspace, agent, tracing, and authority contexts. It delegates to many helpers including _load_turn, _run_lineage, _previous_turn_ended_at, _resolve_profile, _subagent_model, _own_account_alternates, _TurnBilling.resolve, _fire_shadow_selection, _SandboxSetup.run, and _commit_failed_terminal.

*Call graph*: calls 10 internal fn (_commit_failed_terminal, _fire_shadow_selection, _load_turn, _member_accounts_connectable, _member_skill_turn, _own_account_alternates, _previous_turn_ended_at, _resolve_profile, _run_lineage, _subagent_model); called by 1 (_execute_turn); 27 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 1015–1017)

```
def subagents_for(authority: ExecutionAuthority) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates an authority-limited view of subagent spawning for a particular execution authority. This lets tools spawn children only under the authority they are currently allowed to use.

**Data flow**: It receives an ExecutionAuthority. It asks the Subagents object to authorize that authority, then returns the authorized spawn interface together with the authorized Subagents wrapper.

**Call relations**: _run_turn defines this small helper while building TurnEngine. The engine receives it so later tool execution can request subagent access under the right authority.


##### `_commit_failed_terminal`  (lines 1260–1331)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, attempt: str, error: BaseException) -> None
```

**Purpose**: Writes a durable failed terminal result when something outside the engine fails. It keeps retrying database publication so a waiting client can eventually see that the turn ended.

**Data flow**: It receives the hub, turn id, attempt id, and error. It builds a failed TerminalFrame with the error class and truncated message, then repeatedly tries to update the turn row only if it is still queued or running under this attempt. On success it emits metrics, logs the stack, publishes a Terminal event, and returns. On transient failure it logs and sleeps with backoff.

**Call relations**: _execute_turn and _run_turn call this as a backstop. Engine-owned failures usually have already written their terminal, so this update will not match and will quietly return.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 12 external calls (__init__, __init__, sleep, and_, or_, update, workspace_tx, emit_metric, formatted_stack, log (+2 more)).


##### `turn_workflow`  (lines 1335–1336)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the durable DBOS workflow entry for running a turn. DBOS is the workflow system that can replay steps after crashes.

**Data flow**: It receives workspace id and turn id as workflow arguments. It passes them to _execute_turn and returns that status string.

**Call relations**: The queue/workflow system calls this entrypoint. All real work is delegated to _execute_turn.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 1339–1434)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Loads the complete turn record together with its agent and conversation audience. This turns raw database rows into the typed objects the runtime expects.

**Data flow**: It receives a turn UUID. It queries the turn, agent, and conversation tables, validates nested JSON fields such as context, terminal frame, runtime config, and object refs, and returns a Turn, Agent, and Audience.

**Call relations**: _run_turn calls this after claiming a turn, and also when a claim finds the turn already handled and transcript repair may be needed.

*Call graph*: called by 1 (_run_turn); 9 external calls (__init__, __init__, model_validate, model_validate, model_validate, model_validate, select, workspace_tx, parse_audience).


##### `_run_lineage`  (lines 1437–1469)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: Finds where a spawned turn should publish live activity. For child turns, it traces back to the root turn that user interfaces are already watching.

**Data flow**: It receives a Turn. If the turn has no parent, it returns None. Otherwise it follows parent_turn_id links in the database until it reaches the root, determines the profile or agent name for display, and returns a RunLineage object.

**Call relations**: _run_turn calls this before building TurnEngine, and the engine uses the lineage to publish child activity under the correct root stream.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 1472–1484)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: Finds when the previous turn in the same conversation ended. This gives the engine timing context for non-intent turns.

**Data flow**: It receives a Turn. If this is the first sequence number, it returns None. Otherwise it reads the previous turn’s updated_at timestamp and ensures it has a timezone, returning a datetime.

**Call relations**: _run_turn calls this for ordinary turns before building TurnEngine. Prepared intents skip it.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_frozen_billing_identity`  (lines 1487–1508)

```
async def _frozen_billing_identity(turn_id: UUID, candidate: _BillingIdentity) -> _BillingIdentity
```

**Purpose**: Stores the billing identity for an attempt if one is not already stored for that same attempt. This prevents two replaying workers from choosing different billing facts.

**Data flow**: It receives a turn id and candidate billing identity. Under a database row lock, it reads the stored billing identity. If a matching attempt identity exists, it returns that. Otherwise it writes the candidate to the turn row and returns it.

**Call relations**: _TurnBilling.resolve calls this when it needs to freeze a newly computed billing identity.

*Call graph*: called by 1 (resolve); 4 external calls (model_dump, select, update, workspace_tx).


##### `_stored_billing_identity`  (lines 1511–1521)

```
async def _stored_billing_identity(turn_id: UUID, attempt: str) -> _BillingIdentity | None
```

**Purpose**: Reads the frozen billing identity for the current attempt, if one exists. It ignores billing data from older attempts.

**Data flow**: It receives a turn id and attempt id. It reads billing_identity from the turn row, validates it into a _BillingIdentity, and returns it only when its attempt matches. Otherwise it returns None.

**Call relations**: _TurnBilling.resolve calls this first, before computing or freezing a new identity.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `_frozen_byok`  (lines 1524–1573)

```
async def _frozen_byok(turn_id: UUID, decided: bool, attempt: str) -> bool
```

**Purpose**: Freezes whether this attempt is served by a workspace/member-provided key rather than the platform default. The answer affects billing and must stay stable during crash replay.

**Data flow**: It receives a turn id, the newly decided boolean, and attempt id. It reads the stored BYOK value and attempt; if they match, it returns the stored value. Otherwise it writes the new decision for this attempt if possible, reads back the settled value, and returns the settled answer or the original decision.

**Call relations**: _TurnBilling.resolve calls this after resolving the model client. The result is passed into TurnEngine as the byok flag.

*Call graph*: called by 1 (resolve); 4 external calls (or_, select, update, workspace_tx).


##### `_open_sandbox`  (lines 1576–1636)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens the conversation sandbox for a turn with the right signed run token and environment variables. The sandbox is the isolated place where commands, files, and tools run.

**Data flow**: It receives the sandbox provider, token codec, turn, grant store, CLI credentials, credential store, credential slots, and a cache-rewrite flag. It creates a run token naming the workspace and turn, builds environment variables for conversation id, tool bridge URL, git proxy authentication, CLI grants, and provider keys, then opens the sandbox session. It returns the SandboxSession.

**Call relations**: _run_turn passes this as the opener for a _LateSandbox. The opener is only invoked when the sandbox is actually needed, so credentials are not derived unnecessarily.

*Call graph*: calls 2 internal fn (open, encode); 7 external calls (__init__, span, cache_git_config, _git_config_env, _grant_cli_env, _keyed_provider_env, cli_git_config).


##### `SandboxAuthorizer.authorize`  (lines 1647–1664)

```
async def authorize(self, authority: ExecutionAuthority) -> Sandbox
```

**Purpose**: Creates a re-authorized sandbox view for a specific execution authority. This lets the same sandbox be used while changing which member or workspace authority the proxy token represents.

**Data flow**: It receives an ExecutionAuthority. It encodes a new run token for the same workspace and turn but with that authority, prepares updated grant-based CLI environment variables, and asks the sandbox to authorize with those replacements. It returns the authorized sandbox.

**Call relations**: _run_turn gives this method to TurnEngine for normal turns. Intent turns do not receive sandbox_for, because they dispatch under a different prepared-intent path.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### `core/src/ufo/runtime/engine.py`

`orchestration` · `turn execution`

A “turn” is one unit of agent work, like answering a user message or carrying out a prepared action from the interface. This file is the turn’s main engine. It decides who owns the turn, gathers the right context, lets the model think and ask for tools, runs those tools safely, folds in new messages that arrived while the turn was running, and finally writes a durable terminal record saying whether the turn finished, failed, was cancelled, or was parked to resume later.

The most important idea here is replay safety. The code uses DBOS steps, which are durable checkpoints for side-effecting work such as model calls, tool calls, and queue drains. If the process crashes, already-recorded steps are replayed from stored results instead of being done again. That prevents double-charging model tokens, double-running tools, or losing messages.

The engine also watches practical limits: billing caps, seat permissions, model provider rate limits, sandbox availability, oversized tool results, images, and context overflow. It streams live updates to the hub for user interfaces, but treats database records as the source of truth. In everyday terms, this file is both the conductor and the black box recorder for an agent’s turn: it coordinates every moving part, and it preserves exactly what happened.

#### Function details

##### `_claim_turn`  (lines 310–358)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued or parked turn so this workflow attempt becomes its owner. It also allows the same attempt to reclaim a turn during crash recovery.

**Data flow**: It receives a turn id and an attempt id, reads the current turn row and locks its conversation, then updates the turn to running only if it is claimable by this attempt. It returns whether the claim was fresh, adopted from the same attempt, or not won at all.

**Call relations**: TurnEngine._mark_running calls this at the start of normal and intent turns. The result decides whether the engine may proceed or must fall back to the repair path for an already-running or already-finished turn.

*Call graph*: called by 1 (_mark_running); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_activity_goal`  (lines 452–453)

```
def _activity_goal(requesters: Mapping[UUID, ActiveMessage]) -> str
```

**Purpose**: Builds a short plain-text goal from the active user messages. This gives activity summaries enough context to say what a tool call is trying to accomplish.

**Data flow**: It receives active requester messages, extracts member-facing text from each rendered message, and joins them into one string. The output is a goal summary string.

**Call relations**: Runtime tool preparation and intent execution call this before starting activity labels. Those labels are later published while tools are running.

*Call graph*: called by 2 (run_intent, prepare); 1 external calls (member_message_text).


##### `_RoundInput.__repr__`  (lines 468–473)

```
def __repr__(self) -> str
```

**Purpose**: Provides a compact debug representation of a model-round request. It avoids printing the full prompt or message contents.

**Data flow**: It reads the round input fields and turns counts and flags into a short string. Nothing else changes.

**Call relations**: It is used implicitly by logging or debugging when a _RoundInput is displayed.


##### `EffectiveCall.parallel_safe`  (lines 494–498)

```
def parallel_safe(self) -> bool
```

**Purpose**: Says whether the resolved tool call can run beside other tool calls. This is used to preserve correct order when some tools must run alone.

**Data flow**: It reads the resolved tool declaration and returns its parallel-safe flag. It does not modify anything.

**Call relations**: Runtime tool scheduling asks this through _RuntimeTools.parallel_safe before deciding how to group tool calls.


##### `EffectiveCall.semantic_call`  (lines 500–511)

```
def semantic_call(self) -> ToolUseBlock
```

**Purpose**: Returns the tool call under the name that represents what it really does. For object actions, that means replacing the generic transport name with the actual action name.

**Data flow**: It reads the original call and resolved action information. It returns either the original call or a copied call with a semantic name and validated action input.

**Call relations**: Activity summaries and final-act parsing use this so they see the real action, not just the wire-level dispatcher.


##### `EffectiveCall.meter_dimensions`  (lines 513–523)

```
def meter_dimensions(self) -> dict[str, str]
```

**Purpose**: Builds metric labels for a resolved tool call. This keeps dashboards grouped by the real operation that ran.

**Data flow**: It reads whether the tool is a normal tool or a bound object action. It returns a dictionary of labels such as call name, kind, binding, and contributor.

**Call relations**: Dispatch and binding paths pass these labels to _meter_dispatch so tool timing and failures are reported under stable names.


##### `EffectiveCall.__repr__`  (lines 525–526)

```
def __repr__(self) -> str
```

**Purpose**: Provides a short debug label for a resolved tool call. It shows the semantic call name and provider call id without dumping inputs.

**Data flow**: It reads fields from the EffectiveCall and returns a string. No state changes.

**Call relations**: It is used implicitly by logs, debuggers, or error displays.


##### `_BoundToolCall.call`  (lines 536–537)

```
def call(self) -> ToolUseBlock
```

**Purpose**: Gives easy access to the original tool-use block inside a bound call. A bound call is a call that already has its execution context attached.

**Data flow**: It reads the EffectiveCall stored inside the bound call and returns its call object. It does not change anything.

**Call relations**: Dispatch code uses this property so it can treat bound and rejected calls through a common interface.


##### `_BoundToolCall.__repr__`  (lines 539–540)

```
def __repr__(self) -> str
```

**Purpose**: Provides a concise debug label for a bound tool call. It names the tool and call id without exposing arguments.

**Data flow**: It reads the call name and id and returns a string. No state changes.

**Call relations**: It is used implicitly when a bound call is logged or inspected.


##### `_RejectedToolCall.parallel_safe`  (lines 552–555)

```
def parallel_safe(self) -> bool
```

**Purpose**: Marks rejected or malformed calls as not safe to reorder with other calls. Even bad calls must occupy their original place in the model’s sequence.

**Data flow**: It returns false every time. No input is transformed.

**Call relations**: The tool runner checks this through the same scheduling path as valid calls, ensuring invalid calls act as ordering barriers.


##### `_RejectedToolCall.__repr__`  (lines 557–561)

```
def __repr__(self) -> str
```

**Purpose**: Provides a short debug label for a rejected tool call. It includes the tool name, call id, outcome, and error class.

**Data flow**: It reads the rejected call fields and returns a string. It changes nothing.

**Call relations**: It is used implicitly by diagnostics when rejected calls are displayed.


##### `_Burn.segments`  (lines 636–645)

```
def segments(self, serving: str, attempt: str, usage_events: Sequence[Usage]) -> tuple[_Segment, ...]
```

**Purpose**: Splits model usage by the account or model that paid for it. This matters when a turn fails over from one model account to another after rate limiting.

**Data flow**: It receives the current serving model, attempt id, and usage events, then cuts the usage at recorded account-change points. It returns billing segments with summed usage for each model/account series.

**Call relations**: Pricing and billing use these segments through TurnEngine._priced and TurnEngine._record_usage.

*Call graph*: calls 1 internal fn (_total_usage); 1 external calls (__init__).


##### `DispatchResult._errors_say_something`  (lines 692–700)

```
def _errors_say_something(self) -> 'DispatchResult'
```

**Purpose**: Guarantees that every error tool result has text for the model to read. This prevents a failed tool from looking like a silent or empty success.

**Data flow**: After a DispatchResult is built, it checks whether it is marked as an error with blank text. If so, it fills in a generic diagnostic message and returns the same result.

**Call relations**: Every dispatch result passes through this Pydantic validator, so all tool error paths inherit the same safety rule.


##### `ModelStreamError.__init__`  (lines 711–712)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Creates an exception that preserves the model provider’s error class, message, and any partial output. This lets billing and recovery still see what happened before the stream failed.

**Data flow**: It receives the provider error class, message, and optional partial output, then stores all three in the exception arguments. It returns an exception instance.

**Call relations**: TurnEngine._stream_recovering_overflow raises this after a streamed model round reports an error in its recorded result.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 714–716)

```
def __str__(self) -> str
```

**Purpose**: Formats the model stream error for logs and terminal records. It includes the provider’s class name and message but not the partial output.

**Data flow**: It reads the stored error class and message and returns a string. It leaves the exception unchanged.

**Call relations**: Python calls this when the error is converted to text, especially during terminal commit and diagnostics.


##### `ModelStreamError.model_error_class`  (lines 719–721)

```
def model_error_class(self) -> str
```

**Purpose**: Exposes the provider’s original error class. This is used to distinguish truncation, overflow, rate limits, and other provider failures.

**Data flow**: It reads the first stored exception argument and returns it. No state changes.

**Call relations**: Commit and recovery code use this property when deciding how to report or recover from a model stream failure.


##### `ModelStreamError.partial_output`  (lines 724–726)

```
def partial_output(self) -> str
```

**Purpose**: Exposes any text the model streamed before failing. This can be salvaged into a file when the response was cut off.

**Data flow**: It reads the stored partial-output argument and returns it. The exception is unchanged.

**Call relations**: _RuntimeModel.stream uses it when converting a truncated response into recoverable feedback for the model.


##### `ModelStreamError.model_error_message`  (lines 729–731)

```
def model_error_message(self) -> str
```

**Purpose**: Exposes the provider’s original error message. This keeps the terminal record faithful to what the provider reported.

**Data flow**: It reads the stored message argument and returns it. No state changes.

**Call relations**: TurnEngine._commit_once uses it when building the final TerminalFrame for a failed model round.


##### `TurnParked.__init__`  (lines 737–746)

```
def __init__(self, message: str, retry_at: datetime | None=None, external_retry_count: int | None=None) -> None
```

**Purpose**: Creates the special exception used to pause a turn without marking it failed. Parking means the turn can resume later when a gate clears or a retry time arrives.

**Data flow**: It receives a message, optional retry time, and optional retry count, then stores them on the exception. The exception is later caught by the engine’s outer run loops.

**Call relations**: Spend checks, seat checks, provider retries, and sandbox-provider retries raise this; TurnEngine.run and run_intent catch it and persist the parked state.

*Call graph*: called by 5 (_enforce_authority_seat, _enforce_seats, _enforce_spend, _stream_retrying_interruption, sandbox_provider_park).


##### `sandbox_provider_park`  (lines 749–768)

```
def sandbox_provider_park(turn: Turn) -> TurnParked | None
```

**Purpose**: Decides whether a sandbox-provider outage should pause the turn for retry. It avoids parking child turns whose parent is waiting inline, because that would lose the real error path.

**Data flow**: It reads the turn’s spawn and retry information, calculates the next retry time if allowed, and returns a TurnParked exception or None. It does not write to storage.

**Call relations**: TurnEngine._invoke_dispatch calls this when a tool reports sandbox-provider unavailability. If it returns a park, the dispatch raises it to the run loop.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_invoke_dispatch); 2 external calls (now, timedelta).


##### `_intent_admits`  (lines 774–779)

```
def _intent_admits(tool: ToolDef) -> bool
```

**Purpose**: Checks whether a prepared member intent is allowed to reach a tool. It only allows core object mutations or tools/actions that declare they are presentable in the interface.

**Data flow**: It receives a tool definition and returns true if its name or presentation metadata admits it. It does not change the tool.

**Call relations**: TurnEngine.run_intent uses this before dispatching member-submitted panel actions.

*Call graph*: called by 1 (run_intent).


##### `_context_tag`  (lines 788–806)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the metadata block placed before a member message. It records when and where the message came from, using the persisted admission time rather than the current clock.

**Data flow**: It receives a message id, optional context, and admission time. It formats message_ref, time, sender, question, and source into a <context> text block.

**Call relations**: TranscriptRepair.load_messages uses it for the founding inbound message, and TurnEngine._render_arrival uses it for queued arrivals.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 809–810)

```
def _bounded(content: str) -> str
```

**Purpose**: Clips tool output to the engine’s maximum tool-result size. This protects later model rounds from being flooded with huge text.

**Data flow**: It receives text and returns a clipped version. It does not write anywhere.

**Call relations**: TurnEngine._finish_dispatch uses it for oversized or error tool output.

*Call graph*: called by 1 (_finish_dispatch); 1 external calls (clipped).


##### `_raised_text`  (lines 813–822)

```
def _raised_text(tool_name: str, error: Exception) -> str
```

**Purpose**: Turns an exception raised by a tool into text the model can understand. It gives a special message for bare exceptions that have no explanation.

**Data flow**: It receives the tool name and exception, checks whether the exception has a message, and returns a diagnostic string. Nothing else changes.

**Call relations**: TurnEngine._invoke_dispatch uses it when a tool handler raises so the model gets an error result instead of an unexplained failure.

*Call graph*: called by 1 (_invoke_dispatch).


##### `_meter_dispatch`  (lines 825–859)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str, semantic: Mapping[str, str] | None=None) -> None
```

**Purpose**: Records metrics for one tool dispatch: how often it ran, how long it took, and how it ended. These metrics make tool reliability visible.

**Data flow**: It receives the tool registry, call, start time, outcome, error class, profile, and semantic labels. It emits a count and duration histogram and returns nothing.

**Call relations**: TurnEngine._bind_or_error and TurnEngine._dispatch_step call it when binding or dispatch exits.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 862–905)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedRef, ...]]
```

**Purpose**: Finds which skills were successfully loaded in the current conversation window. This prevents the engine from needlessly reloading skills already in front of the model.

**Data flow**: It scans messages for completed load-skill tool calls, checks their results were readable and not truncated, asks the skill registry for each closure, and yields those closures. It ignores stale or malformed entries.

**Call relations**: TurnEngine._reseed_loaded_skills calls this after compaction or message changes to rebuild the loaded-skill tracker.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 908–930)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured final action when a specific tool was the last successful call in a round. This is how the engine recognizes things like a final question to ask the user.

**Data flow**: It receives tool calls, results, a tool name, and a payload model. It parses JSON from the matching result and returns a validated payload or None.

**Call relations**: _round_acts uses it for final-act types that only count when they are the last call in a round.

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_pending_act`  (lines 933–961)

```
def _pending_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts the most recent successful structured action of a given kind anywhere in a round. This is used for requests that remain owed until a member responds.

**Data flow**: It receives tool calls, results, a tool name, and a payload model. It walks backward through calls, parses the matching result JSON, and returns a validated payload or None.

**Call relations**: _round_acts uses it for pending acts such as credential or connection requests.

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_round_acts`  (lines 964–995)

```
def _round_acts(resolved: tuple[_Resolution, ...], results: tuple[ToolResultBlock, ...]) -> dict[str, BaseModel]
```

**Purpose**: Collects the structured acts a tool round left open. These acts become fields on the terminal frame, such as a question or credential request.

**Data flow**: It receives resolved calls and tool results, converts bound actions to their semantic names, applies the correct parsing rule for each act, and returns a dictionary keyed by terminal-frame field name.

**Call relations**: _RuntimeTools.after_round uses it after model tool rounds, and TurnEngine.run_intent uses it after an intent dispatch.

*Call graph*: calls 2 internal fn (_final_act, _pending_act); called by 2 (run_intent, after_round).


##### `_act`  (lines 998–1002)

```
def _act(acts: dict[str, BaseModel], frame_field: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Safely pulls one typed act out of the act dictionary. It prevents the caller from treating the wrong payload type as the requested act.

**Data flow**: It receives the acts dictionary, a field name, and an expected model type. It returns the payload only if it is an instance of that type, otherwise None.

**Call relations**: Runtime tool cleanup and intent execution use it to fill question, credential-request, and connect-request fields.

*Call graph*: called by 2 (run_intent, after_round).


##### `_created_refs`  (lines 1005–1037)

```
def _created_refs(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> tuple[ObjectRef, ...]
```

**Purpose**: Reads which workspace objects were newly created by object-apply tool results. This lets the terminal record name objects that now exist.

**Data flow**: It receives tool calls and results, finds successful object_apply results, parses their JSON, and returns valid ObjectRef values for creations only. Updates and unreadable results are ignored.

**Call relations**: TurnEngine._fold_created accumulates these during a turn, and run_intent includes them in the terminal frame.

*Call graph*: called by 2 (_fold_created, run_intent); 2 external calls (__init__, loads).


##### `_total_usage`  (lines 1040–1048)

```
def _total_usage(usage_events: Sequence[Usage]) -> Usage
```

**Purpose**: Adds many model usage records into one total. Usage means token counts, including input, output, and cache-related tokens.

**Data flow**: It receives a sequence of Usage records and sums each token field. It returns a new Usage record with totals.

**Call relations**: Billing, cost publishing, model metrics, and burn segmentation use this helper whenever they need one combined usage number.

*Call graph*: called by 3 (_publish_cost, _stream_once, segments); 1 external calls (__init__).


##### `_to_harness_reasoning`  (lines 1051–1052)

```
def _to_harness_reasoning(block: ReasoningBlock) -> HarnessReasoning
```

**Purpose**: Converts a runtime reasoning block into the harness format. The harness is the lower-level agent runner used by this engine.

**Data flow**: It receives a reasoning block, stores its kind and serialized payload, and returns a HarnessReasoning object.

**Call relations**: Runtime model responses and message conversion use this when handing reasoning back to the harness.

*Call graph*: called by 2 (stream, _to_harness_message); 2 external calls (__init__, model_dump).


##### `_from_harness_reasoning`  (lines 1055–1064)

```
def _from_harness_reasoning(block: HarnessReasoning) -> ReasoningBlock
```

**Purpose**: Converts a harness reasoning block back into the runtime model format. It preserves the provider-specific reasoning block type.

**Data flow**: It receives a HarnessReasoning object, checks its kind, validates the payload as the matching runtime block, and returns it. Unknown kinds raise an error.

**Call relations**: Message conversion calls this when the harness hands messages back to the runtime engine.

*Call graph*: called by 1 (_from_harness_message); 3 external calls (model_validate, model_validate, model_validate).


##### `_to_harness_call`  (lines 1067–1068)

```
def _to_harness_call(call: ToolUseBlock) -> HarnessToolCall
```

**Purpose**: Converts a runtime tool-use block into the harness tool-call shape. This lets the generic agent runner work with runtime calls.

**Data flow**: It receives a ToolUseBlock and copies its id, name, and input dictionary into a HarnessToolCall. No external state changes.

**Call relations**: Runtime model streaming and message conversion use it before returning calls to the harness.

*Call graph*: called by 2 (stream, _to_harness_message); 1 external calls (__init__).


##### `_from_harness_call`  (lines 1071–1072)

```
def _from_harness_call(call: HarnessToolCall) -> ToolUseBlock
```

**Purpose**: Converts a harness tool call back into the runtime tool-use shape. This lets runtime dispatch code understand calls chosen by the harness.

**Data flow**: It receives a HarnessToolCall and copies its id, name, and input into a ToolUseBlock. It returns the new runtime object.

**Call relations**: Runtime tools use it when resolving, executing, and summarizing harness calls.

*Call graph*: called by 3 (_resolve, after_round, _from_harness_message); 1 external calls (__init__).


##### `_to_harness_result`  (lines 1075–1092)

```
def _to_harness_result(result: ToolResultBlock) -> HarnessToolResult
```

**Purpose**: Converts a runtime tool result into the harness format. It supports plain text as well as mixed text and image content.

**Data flow**: It receives a ToolResultBlock, maps text blocks and image blocks into harness equivalents, and returns a HarnessToolResult with error and activity flags preserved.

**Call relations**: _RuntimeTools.execute and message conversion call it when sending runtime tool results back into the harness loop.

*Call graph*: called by 2 (execute, _to_harness_message); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_result`  (lines 1095–1112)

```
def _from_harness_result(result: HarnessToolResult) -> ToolResultBlock
```

**Purpose**: Converts a harness tool result into the runtime result format. It rebuilds runtime text and image blocks.

**Data flow**: It receives a HarnessToolResult, maps its content into runtime text or image blocks, and returns a ToolResultBlock with flags preserved.

**Call relations**: _RuntimeTools.after_round and message conversion use it when the harness reports completed calls.

*Call graph*: called by 2 (after_round, _from_harness_message); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_message`  (lines 1115–1133)

```
def _to_harness_message(message: Message) -> HarnessMessage
```

**Purpose**: Converts a full runtime conversation message into the harness message format. It handles text, images, tool calls, tool results, and reasoning blocks.

**Data flow**: It receives a Message, converts each content block if needed, and returns a HarnessMessage. Plain string messages are copied directly.

**Call relations**: _to_harness_messages applies this across whole conversations before giving them to the AgentEngine.

*Call graph*: calls 3 internal fn (_to_harness_call, _to_harness_reasoning, _to_harness_result); called by 1 (_to_harness_messages); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_message`  (lines 1136–1152)

```
def _from_harness_message(message: HarnessMessage) -> Message
```

**Purpose**: Converts a harness conversation message back into the runtime format. This restores the engine’s native block types after the harness runs.

**Data flow**: It receives a HarnessMessage, converts each content block if needed, and returns a runtime Message. Plain string content is copied directly.

**Call relations**: _from_harness_messages applies this across whole conversations returned by the AgentEngine.

*Call graph*: calls 3 internal fn (_from_harness_call, _from_harness_reasoning, _from_harness_result); called by 1 (_from_harness_messages); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_messages`  (lines 1155–1156)

```
def _to_harness_messages(messages: tuple[Message, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Converts a tuple of runtime messages into harness messages. It is the batch version of _to_harness_message.

**Data flow**: It receives runtime messages, converts each one, and returns a tuple of harness messages. It does not mutate the originals.

**Call relations**: TurnEngine._model_round and runtime conversation/model adapters use this at the boundary with AgentEngine.

*Call graph*: calls 1 internal fn (_to_harness_message); called by 4 (_model_round, prepare, prepare_exhaust, stream).


##### `_from_harness_messages`  (lines 1159–1160)

```
def _from_harness_messages(messages: tuple[HarnessMessage, ...]) -> tuple[Message, ...]
```

**Purpose**: Converts a tuple of harness messages into runtime messages. It is the batch version of _from_harness_message.

**Data flow**: It receives harness messages, converts each one, and returns a tuple of runtime messages. It does not mutate the originals.

**Call relations**: Runtime conversation/model adapters and TurnEngine._model_round use this when results come back from AgentEngine.

*Call graph*: calls 1 internal fn (_from_harness_message); called by 5 (_model_round, checkpoint, prepare, prepare_exhaust, stream).


##### `_RuntimeConversation.prepare`  (lines 1180–1201)

```
async def prepare(self, messages: tuple[HarnessMessage, ...], round_index: int) -> HarnessPreparedRound
```

**Purpose**: Prepares the conversation before each model round. It absorbs new arrivals, checks spend, refreshes loaded skills, and compacts context if needed.

**Data flow**: It receives harness messages and a round index, converts messages to runtime form, folds in arrivals, updates the engine’s window, checks billing gates, compacts, records compaction usage, and returns prepared harness messages.

**Call relations**: AgentEngine calls this before model rounds. It hands back the safe, current conversation that _RuntimeModel.stream will send to the model.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); 2 external calls (__init__, span).


##### `_RuntimeConversation.checkpoint`  (lines 1203–1205)

```
async def checkpoint(self, messages: tuple[HarnessMessage, ...]) -> None
```

**Purpose**: Marks a model round as safely completed in the engine’s durable-so-far window. This is important because interrupted turns must only persist complete rounds.

**Data flow**: It receives harness messages, converts them to runtime messages, stores them in the engine window, and marks that this turn ran at least one round. It returns nothing.

**Call relations**: AgentEngine calls this after a completed round so later failure or cancellation can save the correct transcript slice.

*Call graph*: calls 1 internal fn (_from_harness_messages).


##### `_RuntimeConversation.prepare_exhaust`  (lines 1207–1216)

```
async def prepare_exhaust(self, messages: tuple[HarnessMessage, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Prepares messages for the forced final answer path when the round budget is exhausted. It still checks spend and compacts context before the final attempt.

**Data flow**: It receives harness messages, checks spend, compacts runtime messages if needed, records compaction usage, and returns harness messages. It does not absorb new arrivals.

**Call relations**: AgentEngine uses this when it has run out of normal tool-use rounds and needs a final answer.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages).


##### `_RuntimeModel.stream`  (lines 1225–1280)

```
async def stream(self, request: HarnessModelRequest, round_index: int) -> HarnessModelRound
```

**Purpose**: Runs one model round through the TurnEngine and returns it in harness form. It also converts certain truncation failures into feedback the model can use to recover.

**Data flow**: It receives a harness model request, converts messages and tool schemas, calls the engine’s stream-with-recovery path, enforces seats, publishes cost, and returns text, calls, and reasoning. On recoverable truncation, it may offload partial output and raise RecoverableModelError.

**Call relations**: AgentEngine calls this whenever it needs the language model. It delegates the durable model step work to TurnEngine._stream_recovering_overflow.

*Call graph*: calls 5 internal fn (__init__, _from_harness_messages, _to_harness_call, _to_harness_messages, _to_harness_reasoning); 4 external calls (__init__, __init__, emit_metric, log).


##### `_RuntimeTools.definitions`  (lines 1293–1303)

```
def definitions(self) -> tuple[HarnessToolDefinition, ...]
```

**Purpose**: Builds the tool definitions offered to the harness. It includes requester fields only when the conversation needs explicit member references.

**Data flow**: It reads the engine’s tool registry and active requesters, turns each runtime schema into a harness tool definition, and returns them. It changes no state.

**Call relations**: AgentEngine asks this to know which tools the model may call.

*Call graph*: 1 external calls (__init__).


##### `_RuntimeTools.parallel_safe`  (lines 1305–1306)

```
def parallel_safe(self, call: HarnessToolCall) -> bool
```

**Purpose**: Tells the harness whether a specific tool call can run in parallel. This supports safe batching of tool calls.

**Data flow**: It receives a harness call, resolves it if needed, and returns the resolved call’s parallel-safety answer.

**Call relations**: AgentEngine calls this while planning dispatch segments, and _RuntimeTools._resolve caches the resolution.

*Call graph*: calls 1 internal fn (_resolve).


##### `_RuntimeTools.prepare`  (lines 1308–1326)

```
async def prepare(self, calls: tuple[HarnessToolCall, ...]) -> None
```

**Purpose**: Resolves and binds all tool calls in a round before execution. It also starts live activity summaries for valid bound calls.

**Data flow**: It receives harness calls, resolves each one, binds requesters and context, stores the binding by call id, and starts activity tasks for bound calls. If the turn is already parked, it does nothing.

**Call relations**: AgentEngine calls this before executing calls. Later _RuntimeTools.execute uses the stored bindings.

*Call graph*: calls 2 internal fn (_resolve, _activity_goal); 1 external calls (gather).


##### `_RuntimeTools.execute`  (lines 1328–1336)

```
async def execute(self, call: HarnessToolCall) -> HarnessToolResult
```

**Purpose**: Executes one prepared tool call and returns its result to the harness. If a tool parks the turn, later calls in the same round receive the park message as an error.

**Data flow**: It receives a harness call, finds its prepared binding, dispatches it through the engine, converts the result to harness form, and returns it. If TurnParked is raised, it stores the parked state and returns an error result.

**Call relations**: AgentEngine calls this for each tool call after prepare. It delegates real dispatch to TurnEngine._dispatch.

*Call graph*: calls 1 internal fn (_to_harness_result); 1 external calls (__init__).


##### `_RuntimeTools.after_round`  (lines 1338–1364)

```
async def after_round(self, calls: tuple[HarnessToolCall, ...], results: tuple[HarnessToolResult, ...]) -> None
```

**Purpose**: Cleans up after a tool round and records what the round changed. It tracks workspace-change targets, created objects, and open final acts.

**Data flow**: It receives harness calls and results, converts them to runtime form, records changed paths, folds created objects into the turn, parses open acts, updates state, and clears temporary resolution and binding caches.

**Call relations**: AgentEngine calls this after tool execution. Its act state feeds the final return from TurnEngine._model_round.

*Call graph*: calls 5 internal fn (_resolve, _act, _from_harness_call, _from_harness_result, _round_acts); 2 external calls (__init__, change_targets).


##### `_RuntimeTools.after_checkpoint`  (lines 1366–1370)

```
async def after_checkpoint(self) -> None
```

**Purpose**: Raises any parked state after the harness checkpoint has been written. This ensures the completed round is saved before the outer engine parks the turn.

**Data flow**: It reads and clears the stored parked exception. If one existed, it raises it; otherwise it returns normally.

**Call relations**: AgentEngine calls this after checkpointing. The raised TurnParked is caught by TurnEngine.run.


##### `_RuntimeTools.interrupted`  (lines 1372–1373)

```
def interrupted(self) -> None
```

**Purpose**: Clears a pending question when the round is interrupted. A question should not survive if the turn did not finish cleanly enough to ask it.

**Data flow**: It replaces the state’s open acts with the question field set to None. Other act fields are preserved.

**Call relations**: AgentEngine uses this interruption hook while unwinding a disrupted round.

*Call graph*: 1 external calls (replace).


##### `_RuntimeTools._resolve`  (lines 1375–1381)

```
def _resolve(self, call: HarnessToolCall) -> _Resolution
```

**Purpose**: Resolves a harness tool call once and caches the answer. This avoids repeating name and object-action lookup during the same round.

**Data flow**: It receives a harness call, checks the cache by call id, converts to runtime call if needed, asks the engine to resolve it, stores the result, and returns it.

**Call relations**: Definitions of safety, preparation, and after-round parsing all call this so they agree on the same resolved identity.

*Call graph*: calls 1 internal fn (_from_harness_call); called by 3 (after_round, parallel_safe, prepare).


##### `_RuntimeEvents.speak`  (lines 1389–1390)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Passes mid-turn marked replies from the harness to the engine’s delivery logic. These are snippets the model marked as direct replies to members.

**Data flow**: It receives marked replies and a round number, then delegates to TurnEngine._speak. It returns nothing.

**Call relations**: AgentEngine calls this when it finds speakable spans during a round.


##### `_RuntimeEvents.closing`  (lines 1392–1393)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Passes final-round marked replies to the engine so they can be streamed as visible text. This prevents the closing answer from disappearing from live output.

**Data flow**: It receives marked replies and delegates to TurnEngine._stream_closing_spans. It returns nothing.

**Call relations**: AgentEngine calls this during closing output handling.


##### `_RuntimeEvents.exhausted`  (lines 1395–1402)

```
def exhausted(self) -> None
```

**Purpose**: Records that the turn ran out of allowed model/tool rounds. It marks the incomplete reason and emits observability events.

**Data flow**: It updates the meter’s incomplete reason, emits a metric, and logs the forced-final condition. It returns nothing.

**Call relations**: AgentEngine calls this when it reaches the configured maximum round count.

*Call graph*: 2 external calls (emit_metric, log).


##### `TranscriptRepair.resolve`  (lines 1417–1441)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal result for a duplicate or redelivered execution that no longer owns the turn. This helps clients stop waiting after a previous run committed but crashed before publishing.

**Data flow**: It reads the turn’s terminal frame from the database. If present, it persists inbound messages if needed, publishes the terminal frame to the hub, and returns it; otherwise it returns None.

**Call relations**: TurnEngine._resolve_unclaimed calls this when _mark_running loses the claim.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 1443–1451)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed conversation transcript including the final assistant answer. This makes the turn available as history for later turns.

**Data flow**: It receives messages, answer, system prompt, and injected context, appends the assistant answer, and writes a Conversation record. It returns nothing.

**Call relations**: TurnEngine._persist_transcript wraps this after labeling tool-result activity.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_interrupted`  (lines 1453–1469)

```
async def persist_interrupted(self, messages: tuple[Message, ...], ran: bool) -> None
```

**Purpose**: Writes the safe transcript for a turn that ended without a normal answer. If the turn did real work, it adds an interruption notice so later turns do not repeat it blindly.

**Data flow**: It receives completed messages and a flag saying whether this turn ran. It optionally appends an interrupted-turn user notice and writes the conversation.

**Call relations**: TurnEngine._persist_interrupted calls this on failure or cancellation when there is a completed message window.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair._persist_parked`  (lines 1471–1493)

```
async def _persist_parked(self, messages: tuple[Message, ...], absorbed: tuple[UUID, ...], requesters: Mapping[UUID, ActiveMessage], system: str, injected: str) -> bool
```

**Purpose**: Writes the resumable transcript state for a parked turn. This includes absorbed message ids and active requesters so the turn can continue later.

**Data flow**: It receives messages, absorbed ids, requesters, system prompt, and injected text, builds a ParkedTurn record, and writes the conversation. It returns whether the write succeeded.

**Call relations**: TurnEngine._persist_parked calls this before committing a parked state.

*Call graph*: calls 1 internal fn (write_conversation); 2 external calls (__init__, __init__).


##### `TranscriptRepair.persist_inbound`  (lines 1495–1514)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves the user inbound messages when a turn ends before producing a full assistant transcript. This prevents the user’s message from disappearing.

**Data flow**: It loads the founding message or builds a safe denial message, appends any absorbed arrivals, and writes the conversation. It returns nothing.

**Call relations**: TranscriptRepair.resolve uses this for terminal repair, and TurnEngine._persist_interrupted uses it when there was no completed round.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 1516–1526)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the conversation window the turn should start from. It uses parked state if available, otherwise prior transcript plus this turn’s inbound message.

**Data flow**: It checks for a parked record, returns its messages if present, or builds the founding user message with context tagging for member turns. Spawned turns keep their inbound bare.

**Call relations**: TurnEngine._load_messages and TranscriptRepair.persist_inbound rely on this to reconstruct the model-visible conversation.

*Call graph*: calls 3 internal fn (_parked_record, _prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._parked_record`  (lines 1528–1537)

```
async def _parked_record(self) -> Conversation | None
```

**Purpose**: Reads a saved parked conversation only if it belongs to this exact turn sequence. This avoids using stale or unrelated transcript data.

**Data flow**: It reads the transcript and checks sequence, from-run flag, and parked data. It returns the stored Conversation or None.

**Call relations**: TranscriptRepair.load_messages and TurnEngine._prepare_run use it to resume parked turns.

*Call graph*: called by 1 (load_messages).


##### `TranscriptRepair._prior_messages`  (lines 1539–1545)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Returns the transcript messages before this turn. It deliberately excludes this turn’s own writes during replay.

**Data flow**: It reads the transcript and returns empty if there is none or if the stored sequence is this turn or newer. Otherwise it returns stored messages.

**Call relations**: TranscriptRepair.load_messages and persist_inbound use it when building the founding window.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 1547–1575)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None, from_run: bool=False, parked: ParkedTurn | None=None) -> bool
```

**Purpose**: Writes a Conversation record with a few retries. This makes transcript writes more tolerant of short database or storage hiccups.

**Data flow**: It builds a Conversation from messages and optional metadata, tries to write it, logs failures, sleeps between attempts, and returns whether it succeeded.

**Call relations**: All TranscriptRepair persistence methods delegate to this one write path.

*Call graph*: called by 4 (_persist_parked, persist_inbound, persist_interrupted, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 1609–1626)

```
def exited(self, status: str) -> None
```

**Purpose**: Records how long this execution ran and how many model rounds it performed. It reports the exit status only once.

**Data flow**: It receives a status, checks whether the meter already ended, calculates elapsed time, emits duration and round metrics, and marks itself ended.

**Call relations**: TurnEngine._commit and the exception paths in run/run_intent call this when an execution reaches a terminal, parked, cancelled, or preempted exit.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 1717–1728)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that the engine was wired with compatible audiences and tool naming. It catches setup mistakes before a turn runs.

**Data flow**: It reads extension contexts, hook audience, and output-model/tool configuration. It raises ValueError for mismatches or a forbidden finish-tool collision.

**Call relations**: Dataclass construction calls this automatically when a TurnEngine is created.


##### `TurnEngine.__repr__`  (lines 1730–1734)

```
def __repr__(self) -> str
```

**Purpose**: Provides a compact debug label for the engine. It names the turn, agent, and profile.

**Data flow**: It reads engine fields and returns a string. It changes nothing.

**Call relations**: Used implicitly by debugging and logging tools.


##### `TurnEngine.profile`  (lines 1737–1740)

```
def profile(self) -> str
```

**Purpose**: Computes the telemetry profile for this turn, such as main, agent child, or subagent profile. Metrics use it to separate different kinds of work.

**Data flow**: It reads the turn’s subagent and spawn fields and returns a profile string. No state changes.

**Call relations**: Most logging and metric calls in the engine use this property.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 1742–1910)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal agent turn from claim to final publish. This is the main workflow body for conversational model-driven work.

**Data flow**: It creates runtime context and usage trackers, claims the turn, prepares messages, loops through model rounds and tool execution, commits the final frame, persists transcript and workspace changes, and handles parking, cancellation, preemption, and failure cleanup.

**Call relations**: This is the central orchestration method. It calls preparation, model-round, commit, park, transcript, billing, arrival-release, sandbox-stop, and publish helpers.

*Call graph*: calls 16 internal fn (_bill_cancelled, _commit, _mark_running, _model_round, _park, _persist_interrupted, _persist_parked, _persist_transcript, _prepare_run, _publish (+6 more)); 9 external calls (__init__, __init__, __init__, __init__, __init__, partial, monotonic, emit_metric, log).


##### `TurnEngine._rank_find`  (lines 1912–1933)

```
async def _rank_find(self, usage_events: list[Usage], system: str, user: str) -> str
```

**Purpose**: Runs a small model completion used by tool-side search/ranking. It records the tokens spent by that helper model call.

**Data flow**: It receives usage storage plus system and user text, builds a model request, streams text deltas into a string, appends Usage events both to the turn and current tool dispatch, and returns the completed text.

**Call relations**: The ToolContext exposes this as find. Tool handlers can call it during dispatch, and _invoke_dispatch makes sure its usage is attributed to that tool.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._prepare_run`  (lines 1935–2014)

```
async def _prepare_run(self, usage_events: list[Usage], meter: _TurnMeter, absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage], pending_guard: bool) -> _PreparedRun
```

**Purpose**: Builds the initial model-ready state for a turn. It resumes parked turns, runs prompt-submit hooks, applies scheduled memory, handles denied founding messages, and injects extra context.

**Data flow**: It receives usage and requester trackers plus a pending-arrival guard, then returns system prompt, messages, injected text, and possibly an already-committed terminal. It may also commit and publish a denial answer.

**Call relations**: TurnEngine.run calls this after claiming the turn and before entering model rounds.

*Call graph*: calls 7 internal fn (_commit, _load_messages, _persist_transcript, _publish_terminal, _record_workspace_changes, _repair, _scheduled_system); called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, escape, span).


##### `TurnEngine.run_intent`  (lines 2016–2155)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a prepared intent turn without asking the model. It dispatches exactly one typed tool call and commits the result.

**Data flow**: It claims the turn, parses the inbound intent or bridge request into a tool call, checks seats and allowed actions, binds context, dispatches the tool, commits success or failure, persists transcript, publishes terminal, and handles parking or cancellation.

**Call relations**: This is the direct-action sibling of TurnEngine.run. It shares the same binding, dispatch, commit, parking, and transcript machinery.

*Call graph*: calls 19 internal fn (_bind_or_error, _commit, _dispatch_step_recovering, _enforce_seats, _load_messages, _mark_running, _park, _persist_transcript, _publish_terminal, _rejected (+9 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, model_validate_json, monotonic, emit_metric (+1 more)).


##### `TurnEngine._scheduled_system`  (lines 2157–2191)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. If memory search fails or finds nothing, the original prompt is kept.

**Data flow**: It receives the base system prompt, searches memory with a short timeout, formats matches into escaped text, and returns the augmented or original prompt.

**Call relations**: TurnEngine._prepare_run calls it only for scheduled-admission turns.

*Call graph*: called by 1 (_prepare_run); 5 external calls (__init__, timeout, escape, log, audience_subjects).


##### `TurnEngine._mark_running`  (lines 2193–2201)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn as owned by the current workflow attempt. This prevents two workers from running the same turn at once.

**Data flow**: It passes the turn id and attempt id to _claim_turn and returns true if any claim branch succeeded. It does not otherwise mutate engine state.

**Call relations**: TurnEngine.run and run_intent call it before doing any side-effecting work.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 2203–2204)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a TranscriptRepair helper for this turn. The helper owns transcript recovery and persistence details.

**Data flow**: It reads the engine’s turn, transcript, and hub fields and returns a new TranscriptRepair. No external state changes.

**Call relations**: Load, persist, parked, and unclaimed-resolution helpers call this whenever they need transcript repair behavior.

*Call graph*: called by 6 (_load_messages, _persist_interrupted, _persist_parked, _persist_transcript, _prepare_run, _resolve_unclaimed); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 2206–2208)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the starting conversation messages under an observability span. This wraps transcript repair with tracing.

**Data flow**: It creates a repair helper, calls its load_messages method, and returns the message tuple. It does not alter messages itself.

**Call relations**: TurnEngine._prepare_run and run_intent call this when they need the current transcript window.

*Call graph*: calls 1 internal fn (_repair); called by 2 (_prepare_run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 2210–2299)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the generic AgentEngine for one complete answer attempt. It supplies runtime adapters for model, tools, conversation, and events.

**Data flow**: It receives context, messages, usage and state trackers, builds structured-output support if this is a subagent contract, runs AgentEngine, then returns final messages, answer text, and any open acts.

**Call relations**: TurnEngine.run calls this inside its main loop. The adapters inside it call back into many TurnEngine helpers.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); called by 1 (run); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__).


##### `TurnEngine._model_round.validate`  (lines 2243–2244)

```
def validate(call: HarnessToolCall) -> str
```

**Purpose**: Validates a finish-tool payload against the subagent output contract. It turns a valid payload into JSON for the harness.

**Data flow**: It receives a harness tool call, validates its input with the output contract, and returns the normalized JSON string. Invalid input raises validation errors.

**Call relations**: The StructuredOutput object inside _model_round uses this when the model calls the finish tool.


##### `TurnEngine._model_round.accept_prose`  (lines 2246–2255)

```
def accept_prose(text: str) -> str | None
```

**Purpose**: Optionally accepts short freeform prose as a valid structured result. This lets simple subagent contracts accept a direct text answer without an explicit finish call.

**Data flow**: It receives answer text, checks whether the contract supports freeform results and the text is short enough, validates it as a result payload, and returns JSON or None.

**Call relations**: The StructuredOutput object inside _model_round uses this during final-answer handling.

*Call graph*: 1 external calls (freeform_result_contract).


##### `TurnEngine._fold_created`  (lines 2301–2329)

```
async def _fold_created(self, created: dict[ObjectRef, None], tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> None
```

**Purpose**: Adds newly created object references to the turn’s durable accumulator. This preserves creations even if the turn later parks, fails, or resumes.

**Data flow**: It receives the created-object map plus calls and results, extracts fresh refs, updates the map, and writes the full set to the turn row if there are new entries.

**Call relations**: _RuntimeTools.after_round calls this after each tool round.

*Call graph*: calls 1 internal fn (_created_refs); 2 external calls (update, workspace_tx).


##### `TurnEngine._absorb_arrivals`  (lines 2331–2391)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Adds queued inbound messages to the current conversation window. It renders each arrival safely and publishes acknowledgment for member messages.

**Data flow**: It receives current messages and tracking lists, claims arrivals, appends denied or rendered messages, updates absorbed ids and active requesters, logs the batch, publishes Absorbed frames for member arrivals, and returns updated messages.

**Call relations**: _RuntimeConversation.prepare calls this before model rounds so the model sees messages that arrived mid-turn.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 2393–2445)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Delivers marked mid-turn replies to members and live streams. These are model-written spans meant as immediate member-facing messages.

**Data flow**: It receives marked replies and a round index, writes idempotent mid-turn delivery rows, logs new rows, and publishes Reply frames. It skips subagent turns.

**Call relations**: _RuntimeEvents.speak delegates here when AgentEngine finds speakable spans.

*Call graph*: calls 1 internal fn (_publish); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 2447–2457)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Streams final marked reply spans as text deltas during closing. This ensures live terminals display the final answer even if the span was redacted earlier.

**Data flow**: It receives marked replies and publishes each reply’s text as a TextDelta. It returns nothing.

**Call relations**: _RuntimeEvents.closing calls this during final output handling.

*Call graph*: calls 1 internal fn (_publish); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 2459–2483)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Renders one queued arrival exactly as the model should see it. It applies prompt-submit hooks and either returns safe denial text or context-tagged content.

**Data flow**: It receives message id, body, context, speaker, and creation time, fires the user_prompt_submit hook, and returns either rendered content plus None or None plus denial text.

**Call relations**: TurnEngine._claim_arrivals calls this inside the durable queue-drain step.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 2486–2555)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably drains pending inbound messages for this turn. Because it is a DBOS step, crash recovery reuses the same claimed batch instead of consuming messages twice.

**Data flow**: It receives already absorbed ids, stamps claimable inbound rows with this turn id, sorts them by sequence, renders each arrival, logs the result, and returns Arrival records. It also closes adoption replay mode when a live drain runs.

**Call relations**: TurnEngine._absorb_arrivals calls this whenever a round is prepared.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 7 external calls (__init__, model_validate, and_, or_, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 2557–2576)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns claimed-but-not-absorbed arrivals to the pending queue after failure or cancellation. This prevents messages from being stranded if a drain did not complete.

**Data flow**: It receives absorbed ids, clears consumed_turn_id for this turn’s other stamped inbound rows, and logs any failure. It is best-effort and returns nothing.

**Call relations**: TurnEngine.run calls it on failed, cancelled, parked-error, or preempted exits.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._stream_recovering_overflow`  (lines 2578–2650)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, tool_schemas: tuple[ToolSchema, ...], tool_choice: str | None, round_index: int, offe
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large. This gives oversized conversations a chance to continue.

**Data flow**: It receives messages, usage, prompts, tool settings, and round flags, runs the stream retry path, converts recorded model errors into ModelStreamError, and on context overflow compacts messages, records compaction usage, and retries.

**Call relations**: _RuntimeModel.stream calls this for every model round.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_retrying_interruption); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._stream_retrying_interruption`  (lines 2652–2691)

```
async def _stream_retrying_interruption(self, round_input: _RoundInput, usage_events: list[Usage]) -> StreamResult
```

**Purpose**: Runs one model round and retries once if the model stream died mid-round from a transient interruption. It still bills any usage from interrupted attempts.

**Data flow**: It receives a RoundInput and usage list, calls _stream_once, appends usage, may move to another account after rate limit, may park for provider retry, and returns the final StreamResult or errored result.

**Call relations**: TurnEngine._stream_recovering_overflow uses this for both normal and post-compaction attempts.

*Call graph*: calls 3 internal fn (_move_account, _stream_once, __init__); called by 1 (_stream_recovering_overflow); 4 external calls (now, timedelta, emit_metric, log).


##### `TurnEngine._move_account`  (lines 2693–2708)

```
async def _move_account(self, usage_events: list[Usage]) -> bool
```

**Purpose**: Moves the turn to the member’s next available model account after a rate limit. It records where the old account’s usage stopped.

**Data flow**: It asks the serving model to move. If successful, it records the previous model and usage index in the burn tracker, logs the move, and returns true; otherwise false.

**Call relations**: _stream_retrying_interruption calls this when a stream result reports account rate limiting.

*Call graph*: called by 1 (_stream_retrying_interruption); 1 external calls (log).


##### `TurnEngine._priced`  (lines 2710–2716)

```
def _priced(self, usage_events: Sequence[Usage]) -> int
```

**Purpose**: Calculates this attempt’s cost in micro-dollars using the correct model prices. It accounts for account failover segments.

**Data flow**: It receives usage events, asks the burn tracker for model/account segments, prices each segment, and returns the total micro-USD amount.

**Call relations**: Spend enforcement and live cost publishing call this before billing is finally written.

*Call graph*: called by 2 (_enforce_spend, _publish_cost).


##### `TurnEngine._enforce_spend`  (lines 2718–2758)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks billing balance, spend caps, and seats before each model round. If the turn has spent too much or lacks access, it parks instead of failing.

**Data flow**: It receives current usage and requesters, enforces seats, prices pending usage, checks balance and spend caps through the database, and raises TurnParked on denial. Otherwise it returns normally.

**Call relations**: _RuntimeConversation.prepare and prepare_exhaust call this before letting another model round run.

*Call graph*: calls 3 internal fn (_enforce_seats, _priced, __init__); 6 external calls (__init__, __init__, workspace_tx, applicable_caps_absent, balance_absent, audience_member).


##### `TurnEngine._enforce_seats`  (lines 2760–2769)

```
async def _enforce_seats(self, requesters: Mapping[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks that all members involved in the turn still have seats in the workspace. A revoked seat parks the turn with a user-facing message.

**Data flow**: It gathers member ids from active requesters and on-behalf-of authority, queries the Seats service, and raises TurnParked if any are not seated.

**Call relations**: Spend enforcement and intent execution call this before spending or dispatching.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_enforce_spend, run_intent); 2 external calls (__init__, workspace_tx).


##### `TurnEngine._enforce_authority_seat`  (lines 2771–2777)

```
async def _enforce_authority_seat(self, authority: ExecutionAuthority) -> None
```

**Purpose**: Checks the seat for the member authority attached to a specific tool call. This protects tool execution under member identity.

**Data flow**: It extracts a member id from execution authority, skips non-member authority, queries Seats, and raises TurnParked if the member is not seated.

**Call relations**: TurnEngine._invoke_dispatch calls it immediately before running a tool handler.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_invoke_dispatch); 3 external calls (__init__, workspace_tx, authority_member_id).


##### `TurnEngine._stream_once`  (lines 2780–2951)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Runs one actual model-provider stream as a durable DBOS step. It publishes live text, records usage and timing, and returns a memoized StreamResult.

**Data flow**: It receives RoundInput, builds the provider ModelRequest with tools, cache settings, and reasoning settings, streams through ModelRoundRunner, emits metrics, and returns text, tool calls, reasoning, usage, and any captured stream error.

**Call relations**: _stream_retrying_interruption calls this. Because it is a DBOS step, recovery replays its recorded output instead of calling the model again.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_stream_retrying_interruption); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, now, emit_histogram, emit_metric, emit_up_down_metric (+1 more)).


##### `TurnEngine._publish_cost`  (lines 2953–2966)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes a live cost update after a model round. This lets user interfaces show a running cost meter.

**Data flow**: It receives usage events, totals tokens, prices the attempt, builds a CostTick frame, and publishes it. Publish failures are swallowed by _publish.

**Call relations**: _RuntimeModel.stream calls this after a successful model stream and seat check.

*Call graph*: calls 3 internal fn (_priced, _publish, _total_usage); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2968–2979)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the engine’s record of which skills are already loaded in the current window. This keeps skill-loading behavior accurate after compaction.

**Data flow**: It receives messages, derives loaded skill closures from them, combines them with preloaded skills, and reseeds the compaction tracker.

**Call relations**: _RuntimeConversation.prepare and _stream_recovering_overflow call this after absorbing or compacting messages.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 1 (_stream_recovering_overflow).


##### `TurnEngine._resolve_call`  (lines 2981–2995)

```
def _resolve_call(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Turns a raw model tool call into either a valid resolved call or a rejected call. It handles ordinary tools and generic object-action dispatch.

**Data flow**: It receives a ToolUseBlock, routes object_action calls to _resolve_action, otherwise looks up the tool registry, and returns EffectiveCall or _RejectedToolCall.

**Call relations**: _RuntimeTools._resolve and run_intent use this before binding and dispatching.

*Call graph*: calls 2 internal fn (_rejected, _resolve_action); called by 1 (run_intent); 1 external calls (__init__).


##### `TurnEngine._resolve_action`  (lines 2997–3071)

```
def _resolve_action(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Resolves a generic object_action call into the exact object action it names. It validates kind, action, binding shape, grants, and action input.

**Data flow**: It parses the call input, looks up the object kind and action binding, checks instance/collection rules and grants, validates the action’s input model, and returns an EffectiveCall or rejected call.

**Call relations**: TurnEngine._resolve_call delegates here for object_action wire calls.

*Call graph*: calls 1 internal fn (_rejected); called by 1 (_resolve_call); 3 external calls (__init__, model_validate, replace).


##### `TurnEngine._rejected`  (lines 3073–3089)

```
def _rejected(self, call: ToolUseBlock, error: Exception, dimensions: Mapping[str, str] | None=None, member_refs: tuple[UUID, ...]=()) -> _RejectedToolCall
```

**Purpose**: Builds a rejected tool-call result from an exception. It chooses an outcome label and may add guidance about valid requester references.

**Data flow**: It receives the call, exception, optional metric dimensions, and member refs, turns the error into text, and returns a _RejectedToolCall. No tool handler is run.

**Call relations**: Resolution and binding paths use this whenever a call cannot safely proceed to hooks or handlers.

*Call graph*: called by 4 (_bind_or_error, _resolve_action, _resolve_call, run_intent); 1 external calls (__init__).


##### `TurnEngine._bind_or_error`  (lines 3091–3135)

```
async def _bind_or_error(self, context: ToolContext, item: _Resolution, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Attaches the right requester authority and execution context to a resolved call. If binding fails, it converts the problem into a rejected call where possible.

**Data flow**: It receives base ToolContext, a resolved or rejected item, and active requesters. Rejected items pass through; valid calls are bound through _bind_requester and wrapped as _BoundToolCall, while binding errors become rejections or raised terminal errors.

**Call relations**: _RuntimeTools.prepare and run_intent call this before dispatch. It also meters binding failures.

*Call graph*: calls 4 internal fn (_bind_requester, _member_refs, _rejected, _meter_dispatch); called by 1 (run_intent); 5 external calls (__init__, __init__, meter_dimensions, replace, monotonic).


##### `TurnEngine._dispatch`  (lines 3137–3143)

```
async def _dispatch(self, bound: _DispatchInput, usage_events: list[Usage] | None=None) -> ToolResultBlock
```

**Purpose**: Runs a prepared dispatch input and converts the stored dispatch result into a model-visible tool result. It is the normal tool-execution entry used by the harness adapter.

**Data flow**: It receives a bound or rejected call plus optional usage list, runs the recovering dispatch step, rehydrates images if needed, and returns a ToolResultBlock.

**Call relations**: _RuntimeTools.execute calls this for model-requested tools.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step_recovering).


##### `TurnEngine._dispatch_result`  (lines 3145–3177)

```
async def _dispatch_result(self, result: DispatchResult) -> ToolResultBlock
```

**Purpose**: Converts a DispatchResult into a ToolResultBlock for the model. It rehydrates image blobs when a tool returned images.

**Data flow**: It receives a DispatchResult, either builds a text-only result or loads each image blob and builds mixed text/image content, records the call id as having a displayed activity result, and returns the block.

**Call relations**: TurnEngine._dispatch calls this after the durable dispatch step completes.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._dispatch_step_recovering`  (lines 3179–3189)

```
async def _dispatch_step_recovering(self, bound: _DispatchInput, usage_events: list[Usage] | None) -> DispatchResult
```

**Purpose**: Runs dispatch steps until an interrupted dispatch has been replayed and completed. This supports recovery from cancellation during a tool call.

**Data flow**: It receives a dispatch input and usage list, repeatedly calls _dispatch_step with any resume target, accepts or rejects the result through _accept_dispatch_result, and returns the first non-interrupted result.

**Call relations**: TurnEngine._dispatch and run_intent use this around the DBOS dispatch step.

*Call graph*: calls 2 internal fn (_accept_dispatch_result, _dispatch_step); called by 2 (_dispatch, run_intent).


##### `TurnEngine._accept_dispatch_result`  (lines 3191–3202)

```
def _accept_dispatch_result(self, result: DispatchResult, usage_events: list[Usage] | None) -> bool
```

**Purpose**: Decides whether a dispatch-step result is complete or represents an interrupted live call. It also transfers recorded tool-side usage to the turn.

**Data flow**: It receives a DispatchResult and optional usage list, checks whether the dispatch was live, appends usage when appropriate, raises CancelledError for live interruption, and returns whether the result should be accepted.

**Call relations**: _dispatch_step_recovering calls this after every dispatch step result.

*Call graph*: called by 1 (_dispatch_step_recovering).


##### `TurnEngine._bind_requester`  (lines 3204–3262)

```
async def _bind_requester(self, context: ToolContext, item: EffectiveCall, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Determines which member, if any, a tool call acts for. It uses requested_by references when needed and chooses member-specific sandbox/subagent context.

**Data flow**: It receives base context, an EffectiveCall, and active requesters, parses and removes requested_by if present, derives execution authority, selects sandbox and subagent controls, and returns updated context plus a call with cleaned input.

**Call relations**: TurnEngine._bind_or_error calls this for every valid tool call before dispatch.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error); 5 external calls (__init__, __init__, replace, authority_from_member_id, UUID).


##### `TurnEngine._own_member`  (lines 3264–3272)

```
def _own_member(self, requesters: Mapping[UUID, ActiveMessage]) -> UUID | None
```

**Purpose**: Finds the single member who owns this conversation when one of their messages is active. This lets tools omit requested_by in simple one-member conversations.

**Data flow**: It reads the audience and active requesters. It returns that member id only if the audience is a member and one of their messages is active.

**Call relations**: _bind_requester and _member_refs use it to decide requester-binding behavior.

*Call graph*: called by 2 (_bind_requester, _member_refs); 1 external calls (audience_member).


##### `TurnEngine._member_refs`  (lines 3274–3280)

```
def _member_refs(self, requesters: Mapping[UUID, ActiveMessage]) -> tuple[UUID, ...]
```

**Purpose**: Returns the active message ids that a tool may name with requested_by. It omits refs when the conversation already has an obvious single member.

**Data flow**: It receives active requesters, checks _own_member, and returns either an empty tuple or the ids of active member-authored messages.

**Call relations**: _bind_or_error uses this for binding guidance, and _RuntimeModel.stream uses it to decide whether tool schemas should include requested_by.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error).


##### `TurnEngine._offload`  (lines 3282–3307)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text content into the sandbox runtime output directory and returns a display path. This keeps huge tool results out of the model context.

**Data flow**: It receives a file name and content, ensures the output directory exists, writes the file, logs and returns None on failure, or returns the display path on success.

**Call relations**: _finish_dispatch uses this for oversized tool results, and truncation recovery uses it for salvaged partial model output.

*Call graph*: called by 1 (_finish_dispatch); 2 external calls (emit_metric, log).


##### `TurnEngine._start_activity`  (lines 3309–3314)

```
def _start_activity(self, call: ToolUseBlock, goal: str) -> None
```

**Purpose**: Starts an asynchronous task to summarize what a tool call is doing. This creates live activity updates without blocking dispatch.

**Data flow**: It increments an activity sequence number, creates a task for _generate_activity, stores the task, and arranges cleanup when it finishes.

**Call relations**: _RuntimeTools.prepare and run_intent call this for bound tool calls.

*Call graph*: calls 1 internal fn (_generate_activity); called by 1 (run_intent); 1 external calls (create_task).


##### `TurnEngine._generate_activity`  (lines 3316–3328)

```
async def _generate_activity(self, call: ToolUseBlock, goal: str, sequence: int) -> None
```

**Purpose**: Generates and publishes a short activity label for a tool call. Labels are published in call order even though summaries run concurrently.

**Data flow**: It receives the call, goal, and sequence, asks the activity summarizer for text, stores the label, then under a lock publishes ready labels in sequence order. It also mirrors subagent activity when needed.

**Call relations**: _start_activity launches this as a background task.

*Call graph*: calls 2 internal fn (_publish, _publish_run); called by 1 (_start_activity); 1 external calls (__init__).


##### `TurnEngine._stop_activity`  (lines 3330–3332)

```
def _stop_activity(self) -> None
```

**Purpose**: Cancels any still-running activity-summary tasks. This prevents background work from continuing after the turn is terminal.

**Data flow**: It iterates over stored activity tasks and cancels each one. It returns nothing.

**Call relations**: _publish_terminal calls this after publishing the terminal frame.

*Call graph*: called by 1 (_publish_terminal).


##### `TurnEngine._dispatch_step`  (lines 3335–3440)

```
async def _dispatch_step(self, bound: _DispatchInput, resume_target: ObjectActionTarget | None=None) -> DispatchResult
```

**Purpose**: Runs one tool call as a durable DBOS step. It covers rejected calls, hooks, handler execution, output shaping, interruptions, and dispatch metrics.

**Data flow**: It receives a bound or rejected call plus optional resume target, records the call as live, prepares dispatch, invokes the handler when allowed, finishes output processing, returns DispatchResult, or records an interrupted result on cancellation.

**Call relations**: _dispatch_step_recovering calls this. Because it is a DBOS step, successful tool results replay without running the handler again.

*Call graph*: calls 4 internal fn (_finish_dispatch, _invoke_dispatch, _prepare_dispatch, _meter_dispatch); called by 1 (_dispatch_step_recovering); 3 external calls (__init__, monotonic, span).


##### `TurnEngine._prepare_dispatch`  (lines 3442–3538)

```
async def _prepare_dispatch(self, bound: _BoundToolCall, target: ObjectActionTarget | None) -> _DispatchGate
```

**Purpose**: Validates and gates a bound call before the handler runs. It checks replay preemption, input validation, object targets, and pre-tool hooks.

**Data flow**: It receives a bound call and optional target, may return an immediate DispatchResult for invalid or denied calls, or returns a _DispatchReady with validated arguments and target. It also reports guidance-preempted replay cases.

**Call relations**: _dispatch_step calls this before _invoke_dispatch.

*Call graph*: calls 2 internal fn (_pending_member_guidance, _redoes_on_replay); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, log).


##### `TurnEngine._invoke_dispatch`  (lines 3540–3602)

```
async def _invoke_dispatch(self, bound: _BoundToolCall, ready: _DispatchReady, find_usages: list[Usage]) -> _HandlerOutput
```

**Purpose**: Actually calls the tool handler with the right context. It enforces member seat authority, attaches idempotency keys for side effects, captures find usage, and converts handler output or raised errors.

**Data flow**: It receives a bound call, ready dispatch data, and a usage list. It runs the handler, separates text and images, and returns _HandlerOutput; certain infrastructure errors raise or park the turn.

**Call relations**: _dispatch_step calls this after preparation succeeds, then passes its output to _finish_dispatch.

*Call graph*: calls 3 internal fn (_enforce_authority_seat, _raised_text, sandbox_provider_park); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, replace).


##### `TurnEngine._finish_dispatch`  (lines 3604–3670)

```
async def _finish_dispatch(self, ready: _DispatchReady, handled: _HandlerOutput, find_usages: list[Usage]) -> DispatchResult
```

**Purpose**: Turns raw handler output into a safe durable DispatchResult. It clips or offloads large text, walls untrusted content, fires post hooks, bounds and offloads images, and includes tool-side usage.

**Data flow**: It receives ready call data, handler output, and find usage. It transforms content, writes large text/images when needed, applies hooks, and returns DispatchResult with text, error flag, image references, and usage.

**Call relations**: _dispatch_step calls this after a handler runs successfully or returns an error.

*Call graph*: calls 3 internal fn (_bounded_image, _offload, _bounded); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, wall).


##### `TurnEngine._redoes_on_replay`  (lines 3672–3679)

```
def _redoes_on_replay(self, tool: ToolDef) -> bool
```

**Purpose**: Says whether re-running a tool call would redo work during crash recovery. Non-side-effecting calls can be preempted for new member guidance; side-effecting calls should continue through idempotency.

**Data flow**: It receives a tool definition and returns true when the tool is not side-effecting. No state changes.

**Call relations**: _prepare_dispatch uses this while an adopted replay is open.

*Call graph*: called by 1 (_prepare_dispatch).


##### `TurnEngine._pending_member_guidance`  (lines 3681–3697)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether a new member message is waiting in the conversation. This lets replayed non-side-effecting work yield to newer guidance.

**Data flow**: It queries inbound_message for one unconsumed member-admitted row in this conversation and returns true if found. It does not claim the row.

**Call relations**: _prepare_dispatch calls this only inside a live dispatch body during adoption replay.

*Call graph*: called by 1 (_prepare_dispatch); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 3699–3727)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Downsizes large tool-result images before sending them back to the model. This keeps images within provider limits and avoids paying for useless extra pixels.

**Data flow**: It receives an ImageBlock, decodes it, checks dimensions, thumbnails oversized images, re-encodes them, and returns a new ImageBlock. If decoding or resizing fails, it logs and returns the original image.

**Call relations**: _finish_dispatch calls this before storing image bytes in the blob store.

*Call graph*: called by 1 (_finish_dispatch); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 3729–3801)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Retries until a terminal state is durably written or a guarded commit yields to unseen arrivals. This is the reliable finalization wrapper.

**Data flow**: It receives desired status, usage, meter, answer/error/acts/created data, and arrival guard options. It repeatedly calls _commit_once with backoff, emits terminal metrics and logs on success, marks the meter exited, and returns a TerminalFrame or None.

**Call relations**: run, run_intent, and _prepare_run use this whenever a turn reaches done or failed.

*Call graph*: calls 2 internal fn (_commit_once, exited); called by 3 (_prepare_run, run, run_intent); 4 external calls (sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._publish_terminal`  (lines 3803–3806)

```
async def _publish_terminal(self, frame: TerminalFrame) -> None
```

**Purpose**: Publishes the terminal frame to live listeners and mirrors subagent status. It also stops activity-summary tasks.

**Data flow**: It receives a TerminalFrame, publishes it, publishes any lineage run update, cancels activity tasks, and returns nothing.

**Call relations**: run, run_intent, and _prepare_run call this after a terminal commit is durable.

*Call graph*: calls 3 internal fn (_publish, _publish_run, _stop_activity); called by 3 (_prepare_run, run, run_intent); 1 external calls (__init__).


##### `TurnEngine._record_workspace_changes`  (lines 3808–3821)

```
async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None
```

**Purpose**: Refreshes recorded workspace changes after the turn is done. This lets the portal show files or artifacts affected by the turn.

**Data flow**: It receives target paths, builds a WorkspaceChangeRecorder for the sandbox conversation, and runs its record operation. It returns nothing.

**Call relations**: TurnEngine.run calls it after terminal publish; denial handling in _prepare_run calls it with no targets.

*Call graph*: called by 2 (_prepare_run, run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 3823–3932)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to bill usage and write the terminal frame. It can refuse the commit if new arrivals are still unabsorbed.

**Data flow**: It receives terminal details, optionally locks the conversation and checks pending arrivals, records usage, reads billed cost, builds TerminalFrame, updates the turn row if still non-terminal, and returns the frame plus whether this call committed it.

**Call relations**: _commit calls this inside retry logic.

*Call graph*: calls 1 internal fn (_record_usage); called by 1 (_commit); 10 external calls (__init__, model_validate, and_, or_, select, update, workspace_tx, log, log_error, read_turn_cost).


##### `TurnEngine._park`  (lines 3934–3977)

```
async def _park(self, message: str, usage_events: list[Usage], retry_at: datetime | None=None, absorbed: tuple[UUID, ...]=(), external_retry_count: int | None=None) -> None
```

**Purpose**: Puts a turn into a durable parked state so it can resume later. It bills current usage and releases unabsorbed arrivals in the same transaction.

**Data flow**: It receives a message, usage, optional retry time, absorbed ids, and retry count, updates the turn to parked, records usage, releases unabsorbed arrivals, then publishes a Parked frame and metrics if the update won.

**Call relations**: run and run_intent call this when they catch TurnParked.

*Call graph*: calls 2 internal fn (_publish, _record_usage); called by 2 (run, run_intent); 5 external calls (__init__, update, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 3979–3988)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes a live frame without letting publish failures fail the turn. Durable database state remains the source of truth.

**Data flow**: It receives a live frame, tries to publish it through the hub, and logs any exception. It returns nothing.

**Call relations**: Many helpers use this for text deltas, replies, absorbed notices, cost ticks, activity, terminal, and parked frames.

*Call graph*: called by 8 (_absorb_arrivals, _generate_activity, _park, _publish_cost, _publish_terminal, _speak, _stream_closing_spans, run); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 3990–4012)

```
async def _publish_run(self, activity: str='', status: str='') -> None
```

**Purpose**: Mirrors subagent activity or status onto the root turn’s live stream. Main turns do nothing here.

**Data flow**: It receives optional activity text and status, checks lineage, builds a SubagentActivity frame, publishes it to the root turn, and logs failures. It returns nothing.

**Call relations**: run, _generate_activity, and _publish_terminal call this to keep parent-facing live views updated.

*Call graph*: called by 3 (_generate_activity, _publish_terminal, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 4014–4030)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops sandbox commands after a deliberate workflow cancellation. It avoids doing this for executor preemption, where replay may still need the command’s work.

**Data flow**: It asks the sandbox to stop commands and logs errors without changing the already-durable cancellation outcome. It returns nothing.

**Call relations**: run and run_intent call this in the DBOS cancellation path.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._record_usage`  (lines 4032–4047)

```
async def _record_usage(self, connection: AsyncConnection, usage_events: Sequence[Usage]) -> None
```

**Purpose**: Writes billing ledger usage for this attempt. It creates one cumulative row per model/account segment that served the turn.

**Data flow**: It receives a database connection and usage events, splits them through the burn tracker, and calls record_turn_usage for each segment with pricing and BYOK settings. It returns nothing.

**Call relations**: _commit_once, _park, and _bill_cancelled use this to make consumed tokens billable.

*Call graph*: called by 3 (_bill_cancelled, _commit_once, _park); 1 external calls (record_turn_usage).


##### `TurnEngine._bill_cancelled`  (lines 4049–4059)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for a cancelled or preempted normal turn. Cancellation should not wait forever on billing, but spent tokens should still count.

**Data flow**: It receives usage events, opens a workspace transaction, records usage, and logs any failure. It returns nothing.

**Call relations**: TurnEngine.run calls this in DBOS-cancelled and asyncio-preempted paths.

*Call graph*: calls 1 internal fn (_record_usage); called by 1 (run); 2 external calls (workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 4061–4066)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles an execution that failed to claim the turn. It repairs client visibility if the turn already finished, or no-ops if another live execution owns it.

**Data flow**: It creates a TranscriptRepair helper and calls resolve, returning a terminal frame or None. It does not overwrite turn state.

**Call relations**: run and run_intent call this when _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 4068–4071)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Persists a completed transcript after adding activity labels to tool results. This is the engine-level wrapper around TranscriptRepair.

**Data flow**: It receives messages, answer, system prompt, and injected text, labels the messages, creates a repair helper, and delegates persistence. It returns nothing.

**Call relations**: run, run_intent, and denial handling in _prepare_run call this after a durable terminal commit.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 3 (_prepare_run, run, run_intent).


##### `TurnEngine._persist_interrupted`  (lines 4073–4077)

```
async def _persist_interrupted(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Persists the safe transcript for a failed, cancelled, or interrupted turn. If there are no completed messages, it preserves only inbound or denial text.

**Data flow**: It receives messages, labels them if present, then delegates either to persist_interrupted or persist_inbound on TranscriptRepair. It returns nothing.

**Call relations**: TurnEngine.run calls this in failure and cancellation paths.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 1 (run).


##### `TurnEngine._persist_parked`  (lines 4079–4089)

```
async def _persist_parked(self, messages: tuple[Message, ...], absorbed: tuple[UUID, ...], requesters: Mapping[UUID, ActiveMessage], system: str, injected: str) -> bool
```

**Purpose**: Persists the resumable message window for a parked turn after adding activity labels. This must succeed before the turn is parked.

**Data flow**: It receives messages, absorbed ids, requesters, system prompt, and injected text, labels the messages, and delegates to TranscriptRepair._persist_parked. It returns whether persistence succeeded.

**Call relations**: TurnEngine.run calls this before committing a parked state.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 1 (run).


##### `TurnEngine._labeled`  (lines 4091–4116)

```
def _labeled(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Adds the live activity text shown for tool results into the stored transcript. This makes the saved record match what users saw.

**Data flow**: It receives messages, walks their content blocks, and for completed activity tool results copies in the matching activity label. It returns a new tuple of messages.

**Call relations**: The engine’s transcript persistence wrappers call this before handing messages to TranscriptRepair.

*Call graph*: called by 3 (_persist_interrupted, _persist_parked, _persist_transcript).


### Agent loop and hooks
Runs the model-tool interaction loop and applies extension hooks around important turn events.

### `core/src/ufo/harness/agent.py`

`orchestration` · `agent run loop`

This file is the agent harness: the part that turns a language model into a usable worker. A model by itself can only produce text or requests to call tools. This engine keeps the conversation moving, like a coordinator in a workshop: it asks the model what to do next, sends tool requests to the right tool runner, feeds results back to the model, and stops when there is a final answer.

The file first defines simple message pieces such as text, images, tool calls, tool results, and reasoning notes. It also defines configuration such as how many rounds are allowed and how many tool calls may run at once. Several protocol classes describe the outside parts the engine depends on: the model, the tools, the conversation store, and event reporting. A protocol is a promise about which methods an object must provide.

AgentEngine is the main piece. It runs repeated rounds until the model answers, calls tools, returns an empty response twice, or uses up its round limit. It can also support “structured output”, where the answer must come through a special finish tool and be validated before it is accepted. Without this file, the project would have separate parts for models and tools, but no reliable traffic controller to make them work together safely.

#### Function details

##### `ToolDefinition.__post_init__`  (lines 61–63)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that every declared tool has a real, non-empty name. A tool without a name could not be called reliably by the model.

**Data flow**: It reads the tool name stored on the new ToolDefinition. If the name is blank or only spaces, it raises an error; otherwise the tool definition is left unchanged.

**Call relations**: This runs automatically when a ToolDefinition is created. Later, AgentEngine._stream relies on these tool names when it offers tools to the model and checks for duplicate names.


##### `AgentDefinition.__post_init__`  (lines 74–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that the agent’s safety limits make sense. The engine needs at least one round and at least one allowed parallel tool call, or it cannot run correctly.

**Data flow**: It reads max_rounds and max_parallel_calls from the new AgentDefinition. If either value is less than one, it raises an error; otherwise the definition is kept as-is.

**Call relations**: This runs automatically when an AgentDefinition is created. AgentEngine.run later uses max_rounds to limit the conversation, and AgentEngine._tool_exchange uses max_parallel_calls to limit tool work.


##### `RecoverableModelError.__init__`  (lines 126–128)

```
def __init__(self, feedback: str) -> None
```

**Purpose**: This creates an error that means a model round failed in a way the agent can recover from. It carries feedback that should be sent back into the conversation so the model can try again.

**Data flow**: It receives a feedback string, stores it on the error object, and also passes it to the normal Exception machinery so it has a readable error message.

**Call relations**: The runtime model layer can raise this when a model response is unusable but not fatal. AgentEngine.run catches it, appends the feedback as a user message, and starts the next round instead of ending the whole run.

*Call graph*: called by 1 (stream).


##### `AgentModel.stream`  (lines 134–134)

```
async def stream(self, request: ModelRequest, round_index: int) -> ModelRound
```

**Purpose**: This is the required model interface: given a complete request, produce one model round. Different model providers can implement it while the engine stays the same.

**Data flow**: It takes a ModelRequest containing the system prompt, messages, tools, tool choice, and mode, plus the round number. It returns a ModelRound with the model’s new messages, text, tool calls, and any reasoning information.

**Call relations**: AgentEngine._stream calls this whenever it needs the model to act. The actual implementation lives outside this file, so this protocol is the handshake between the engine and model-specific code.


##### `AgentTools.definitions`  (lines 142–142)

```
def definitions(self) -> tuple[ToolDefinition, ...]
```

**Purpose**: This tells the engine which tools are available for the current agent run. The model sees these definitions and may choose to call them.

**Data flow**: It takes no direct input besides the tool provider’s own state. It returns a tuple of ToolDefinition objects, each describing a callable tool and its expected input.

**Call relations**: AgentEngine._stream calls this in normal mode before asking the model to respond. The returned tools are checked for duplicate names and then included in the ModelRequest.


##### `AgentTools.parallel_safe`  (lines 144–144)

```
def parallel_safe(self, call: ToolCall) -> bool
```

**Purpose**: This tells the engine whether a particular tool call is safe to run at the same time as other calls. It protects tools that must run alone, such as ones that change shared state.

**Data flow**: It receives a ToolCall and returns true or false. True means the call may be grouped with other parallel-safe calls; false means it should be scheduled more carefully.

**Call relations**: AgentEngine._tool_exchange passes this decision function to dispatch_segments, which splits tool calls into safe execution batches before the engine runs them.


##### `AgentTools.prepare`  (lines 146–146)

```
async def prepare(self, calls: tuple[ToolCall, ...]) -> None
```

**Purpose**: This gives the tool layer a chance to get ready before a batch of tool calls runs. For example, it might reserve resources or validate that the calls can be attempted.

**Data flow**: It receives the next tuple of ToolCall objects about to run. It performs any needed side effects and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this just before executing each scheduled batch of tool calls. After preparation, the engine starts the actual tool execution.


##### `AgentTools.execute`  (lines 148–148)

```
async def execute(self, call: ToolCall) -> ToolResult
```

**Purpose**: This runs one tool call and returns the result that should be shown back to the model. It is where a model’s requested action becomes a real effect.

**Data flow**: It receives one ToolCall, uses its name and input to perform the requested work, and returns a ToolResult containing content, an error flag if needed, and optional activity information.

**Call relations**: AgentEngine._tool_exchange calls this for every tool call in a batch, using asyncio.gather so safe calls can run concurrently. The results are then added to the conversation as a user message for the next model round.


##### `AgentTools.after_round`  (lines 150–152)

```
async def after_round(self, calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]) -> None
```

**Purpose**: This notifies the tool layer that a full tool-exchange round has ended. It runs even if a tool fails, so cleanup or bookkeeping can still happen.

**Data flow**: It receives all tool calls the model requested in that round and the ToolResult objects successfully collected so far. It performs any needed follow-up work and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this in a finally block, meaning it is invoked after tool execution whether the round succeeds or raises an error.


##### `AgentTools.after_checkpoint`  (lines 154–154)

```
async def after_checkpoint(self) -> None
```

**Purpose**: This lets the tool layer react after the conversation has been safely checkpointed. A checkpoint is a saved transcript state that future work can rely on.

**Data flow**: It reads any state inside the tool provider and returns nothing. Its purpose is side effects after the transcript has been stored.

**Call relations**: AgentEngine.run calls this after a tool exchange has produced new messages and conversation.checkpoint has completed. This ordering lets tools know the transcript now includes their results.


##### `AgentTools.interrupted`  (lines 156–156)

```
def interrupted(self) -> None
```

**Purpose**: This tells the tool layer that a prepared conversation detected an interrupted final action. It gives tools a chance to mark or clean up interrupted work.

**Data flow**: It takes no explicit input. It updates or signals tool-side state and returns nothing.

**Call relations**: AgentEngine.run calls this when AgentConversation.prepare returns PreparedRound with interrupted_final_act set. That makes the tool boundary aware of an interruption before the next model round starts.


##### `AgentConversation.prepare`  (lines 162–162)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: This prepares the transcript before a model round starts. A conversation store can use it to reload, trim, repair, or annotate messages before the model sees them.

**Data flow**: It receives the current messages and the round number. It returns a PreparedRound containing the messages to use next and a flag saying whether an interrupted final action was found.

**Call relations**: AgentEngine.run calls this at the start of every round. The prepared messages become the input to AgentEngine._stream, and the interruption flag may trigger AgentTools.interrupted.


##### `AgentConversation.checkpoint`  (lines 164–164)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: This saves the transcript after a successful tool exchange. It gives the system a durable point to return to if later work is interrupted.

**Data flow**: It receives the full updated message tuple. It stores or records those messages as needed and returns nothing.

**Call relations**: AgentEngine.run calls this after AgentEngine._tool_exchange returns more messages. Once it completes, the engine calls AgentTools.after_checkpoint.


##### `AgentConversation.prepare_exhaust`  (lines 166–166)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares the transcript when the agent has used all its normal rounds. It lets the conversation boundary clean up or adjust messages before the engine forces a final answer.

**Data flow**: It receives the current messages and returns the messages that should be used for the final exhausted step.

**Call relations**: AgentEngine._exhaust calls this before adding the final forced-answer prompt or structured-output prompt.


##### `AgentEvents.speak`  (lines 172–172)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: This reports model text that is meant to be visible during an ongoing round. It lets user interfaces or logs show progress without changing the agent’s decisions.

**Data flow**: It receives marked replies extracted from the model text and the human-friendly round number. It emits or records them and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this before running tools, because the model may have said something visible before asking for tool calls.


##### `AgentEvents.closing`  (lines 174–174)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: This reports the final visible replies when the agent is closing with an answer. It is an observation hook, not a decision point.

**Data flow**: It receives the marked replies from the final model text. It emits or records them and returns nothing.

**Call relations**: AgentEngine._close calls this for normal final answers, and AgentEngine._exhaust calls it after a forced no-tools final response.


##### `AgentEvents.exhausted`  (lines 176–176)

```
def exhausted(self) -> None
```

**Purpose**: This reports that the agent ran out of its allowed normal rounds. It lets observers note that the final answer is being forced by a limit.

**Data flow**: It takes no explicit input. It emits or records an exhaustion event and returns nothing.

**Call relations**: AgentEngine._exhaust calls this at the start of the exhaustion path, before preparing the transcript and asking for a final answer.


##### `MemoryConversation.prepare`  (lines 195–196)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: This is the simplest conversation preparation: it just passes the messages through unchanged. It is useful for local runs and tests that do not need persistent transcript storage.

**Data flow**: It receives messages and a round number, ignores the round number, wraps the same messages in a PreparedRound, and returns it.

**Call relations**: AgentEngine.run uses this by default through the AgentConversation interface. It creates a PreparedRound so the engine can follow the same path it would use with a more advanced conversation store.

*Call graph*: 1 external calls (__init__).


##### `MemoryConversation.checkpoint`  (lines 198–199)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: This is a no-op checkpoint for in-memory runs. It pretends that saving the transcript succeeded without writing anything anywhere.

**Data flow**: It receives the updated messages, does not modify or store them, and returns nothing.

**Call relations**: AgentEngine.run calls it after tool exchanges when MemoryConversation is the active conversation boundary. Because it does nothing, local and test runs avoid external storage.


##### `MemoryConversation.prepare_exhaust`  (lines 201–202)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This leaves the transcript unchanged when the agent is about to force a final answer after exhausting its rounds. It is the in-memory version of exhaustion preparation.

**Data flow**: It receives the current messages and returns the exact same messages.

**Call relations**: AgentEngine._exhaust calls it through the AgentConversation interface. With MemoryConversation, the engine simply proceeds to add the final prompt.


##### `NullEvents.speak`  (lines 209–210)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: This is a silent event observer for ongoing replies. It is useful when a run should not stream or log visible progress.

**Data flow**: It receives marked replies and a round number, ignores them, and returns nothing.

**Call relations**: AgentEngine._tool_exchange may call it by default. Because it does nothing, the agent can run without an event system attached.


##### `NullEvents.closing`  (lines 212–213)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: This is a silent event observer for final replies. It lets the engine use the same event calls even when nobody is listening.

**Data flow**: It receives the final marked replies, ignores them, and returns nothing.

**Call relations**: AgentEngine._close and AgentEngine._exhaust may call it by default. It keeps the event boundary optional.


##### `NullEvents.exhausted`  (lines 215–216)

```
def exhausted(self) -> None
```

**Purpose**: This is a silent notification for round exhaustion. It exists so the engine can call an exhaustion hook without checking whether an observer is present.

**Data flow**: It takes no input, does nothing, and returns nothing.

**Call relations**: AgentEngine._exhaust calls it by default when no real event observer is configured.


##### `AgentEngine.run`  (lines 230–261)

```
async def run(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: This is the main agent loop. It keeps asking the model for the next step, runs tools when requested, and returns a final Finished result when the agent has answered or must stop.

**Data flow**: It starts with the incoming conversation messages. Each round it lets the conversation boundary prepare them, streams one model response, separates visible marked replies from plain text, runs tool exchanges if the model requested tools, closes if the model gave final text, nudges once if the response is empty, or exhausts the run if the round limit is reached. It returns a Finished object containing the final messages, answer, and flags such as exhausted or structured.

**Call relations**: This is the public entry into AgentEngine. It calls _stream for model rounds, _tool_exchange when tools are requested, _close when text looks final, and _exhaust when the round budget is used up. It also catches RecoverableModelError so the runtime model can ask for a retry with feedback.

*Call graph*: calls 4 internal fn (_close, _exhaust, _stream, _tool_exchange); 3 external calls (__init__, __init__, marked_replies).


##### `AgentEngine._stream`  (lines 263–299)

```
async def _stream(self, messages: tuple[Message, ...], round_index: int, mode: RoundMode) -> ModelRound
```

**Purpose**: This builds the exact request sent to the model for one round. It decides which tools the model may see and whether the model is being forced to call a finish tool.

**Data flow**: It receives the current messages, round number, and round mode. In normal mode it gathers tool definitions, checks for duplicate names, and optionally adds the structured finish tool. In no-tools mode it sends no tools. In force-finish mode it sends only the finish tool and requires that choice. It then calls the model and returns the ModelRound it produced.

**Call relations**: AgentEngine.run uses this for normal rounds, _force_finish uses it when structured output must be forced, and _exhaust uses it for final no-tools or forced-finish attempts. It is the single gate through which model calls leave the engine.

*Call graph*: called by 3 (_exhaust, _force_finish, run); 1 external calls (__init__).


##### `AgentEngine._tool_exchange`  (lines 301–367)

```
async def _tool_exchange(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished | tuple[Message, ...]
```

**Purpose**: This turns model-requested tool calls into tool results and appends both sides of that exchange to the transcript. It also detects structured-output finish calls and may end the run immediately.

**Data flow**: It receives the model round, the visible replies extracted from its text, and the round number. It first reports the visible speech, then checks whether any call is the special structured finish tool. A valid lone finish call becomes a Finished result. Otherwise, it schedules normal tool calls in safe batches, prepares each batch, executes calls, collects ToolResult objects, and always notifies the tool layer after the round. It returns either Finished or the expanded message tuple containing the assistant’s tool calls and the user’s tool results.

**Call relations**: AgentEngine.run calls this whenever a streamed model round contains tool calls. It uses dispatch_segments to choose safe batches, asyncio.gather to run parallel calls, and AgentTools methods to prepare, execute, and finalize tool work.

*Call graph*: called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, gather, dispatch_segments).


##### `AgentEngine._close`  (lines 369–386)

```
async def _close(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished
```

**Purpose**: This finishes a run when the model has produced text instead of tool calls. If structured output is required, it may validate the prose or force the model to use the finish tool.

**Data flow**: It receives the model round, marked visible replies, and the round number. Without structured output, it reports closing replies and returns the model text as the answer. With structured output, it first asks whether prose can be accepted; if not, it appends a prompt telling the model to finish properly and calls _force_finish.

**Call relations**: AgentEngine.run calls this when a model round has non-empty text and no tool calls. It may hand off to _force_finish if the run must produce a schema-checked answer rather than ordinary prose.

*Call graph*: calls 1 internal fn (_force_finish); called by 1 (run); 2 external calls (__init__, __init__).


##### `AgentEngine._force_finish`  (lines 388–397)

```
async def _force_finish(self, messages: tuple[Message, ...], round_index: int) -> Finished
```

**Purpose**: This performs a special structured-output round where the model must call exactly one finish tool. It is used when ordinary text is not enough and the system needs a validated final answer.

**Data flow**: It receives messages and a round number. It streams a force-finish model request, checks that the response contains exactly one call to the configured finish tool, validates that call, and returns a structured Finished result. If the model does not obey or validation fails, it raises a runtime error.

**Call relations**: AgentEngine._close calls this when structured output is required after prose, and AgentEngine._exhaust calls it when the agent is out of rounds but still needs a structured answer. It calls _stream in FORCE_FINISH mode.

*Call graph*: calls 1 internal fn (_stream); called by 2 (_close, _exhaust); 1 external calls (__init__).


##### `AgentEngine._exhaust`  (lines 399–416)

```
async def _exhaust(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: This handles the case where the agent used all allowed normal rounds without finishing. It forces one last answer path and marks the result as exhausted.

**Data flow**: It first notifies events that exhaustion happened, then lets the conversation boundary prepare the messages. If structured output is configured, it adds the structured force-finish prompt and calls _force_finish, returning its answer with exhausted set. Otherwise, it adds the normal final-answer prompt, streams one no-tools model round, extracts marked replies, reports closing events, and returns the final text with exhausted set.

**Call relations**: AgentEngine.run calls this after the max_rounds loop ends. It uses _force_finish for structured answers and _stream in NO_TOOLS mode for plain final answers.

*Call graph*: calls 2 internal fn (_force_finish, _stream); called by 1 (run); 3 external calls (__init__, __init__, marked_replies).


### `core/src/ufo/runtime/ext/hooks.py`

`domain_logic` · `turn lifecycle events`

This file is the project’s “hook chain” for a turn. A hook is extension code that reacts to an event, like a security guard checking a tool call before it happens, or a helper adding extra context after something occurs. Without this file, extensions could not reliably inspect, block, change, or add information to the turn flow.

Hooks are grouped by event and run in a fixed order. Each hook receives a HookContext, which is a package containing the extension, the event payload, the current turn and agent, the audience, and the current speaker. The hook may return nothing, deny the action, modify a tool input, modify a tool output, or inject extra text.

The important rule is that results are folded in order. If one hook changes tool input, the next hook sees that changed input. If several hooks inject text, their text is joined together in order. If any hook returns Deny, the chain stops immediately.

The file is also careful about failure. Hooks that guard important decisions, such as pre_tool_use and user_prompt_submit, can “fail closed”: if they crash or take too long, the action is denied instead of allowed by accident. Less critical hooks are best-effort: failures are logged and the turn continues.

#### Function details

##### `HookChain.__post_init__`  (lines 90–93)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that all hooks in the chain belong to the same audience as the chain itself. It prevents mixing hooks meant for different viewers or participants, which could leak or apply information in the wrong place.

**Data flow**: After a HookChain is created, it looks through every BoundHook stored under every event. It compares each hook extension’s audience with the chain’s audience. If all match, nothing changes; if any differ, it raises an error so the bad chain cannot be used.

**Call relations**: This runs automatically when a HookChain object is built. It acts like a safety inspection before HookChain.fire is ever used, making sure later hook execution happens within one consistent audience.


##### `HookChain.fire`  (lines 95–173)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: This runs all hooks registered for one event and combines their answers into one HookResolution. It is used when the turn engine reaches a hook point and needs to know whether to continue, deny, rewrite input or output, or add injected context.

**Data flow**: It receives the event name, the event payload, optional turn and agent records, and the current speaker id. It finds the hooks for that event, skips tool-specific hooks when they do not match the current tool, builds a HookContext for each remaining hook, and runs the hook with a timeout. As hooks return results, it updates the current tool input, output, or injected text. If a hook denies the action, or if an important gating hook fails, it returns a HookResolution saying the action is denied. Otherwise it returns a HookResolution containing the final changed input or output and any injected text.

**Call relations**: The turn flow calls this whenever a hookable event occurs. Inside, it creates a HookContext to give each extension the information it needs, uses dataclasses.replace to pass along updated payloads after earlier hooks have changed them, and uses asyncio.timeout so a slow hook cannot stall the turn forever. If a hook returns a result type that is not allowed for that event, HookOutcomeNotAllowed is raised and then handled according to the event’s failure policy.

*Call graph*: 5 external calls (__init__, __init__, __init__, timeout, replace).

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-tool-catalog` — The shared menu of tools the agent may call, including built-in tools, extension tools, and guarded bridge tools.
- `reg-model-provider-catalog` — The shared list of available AI models and providers, including limits, prices, credentials, and adapter rules.
- `reg-prompt-skill-environment` — The saved instructions, skills, environment documents, and fingerprints that shape what an agent sees for a turn.
- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-turn-queue-state` — The durable queue of conversation turns, including admission source, run claim, parked state, resume state, and final status.
- `reg-transcript-history` — The saved conversation timeline, including messages, compacted summaries, final answers, costs, and readable history.
- `reg-audience-visibility-state` — The shared privacy labels that decide who may read or join conversation content and workspace objects.
- `reg-live-updates-delivery` — The live reply and notification delivery state used to stream running turns and safely deliver mid-turn or delayed messages once.
- `reg-runtime-fleet-claims` — The attendance and claim sheet for running service processes, including heartbeats, work ownership, and surface listener claims.
- `reg-cancellation-cleanup-state` — The shared stop-and-cleanup state that records when active turns, workflows, child work, sandboxes, and streams are being wound down.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-execution-environment-policy` — The shared rules for where commands and tools may run, such as local execution, Docker, cloud sandboxes, terminals, and browser sessions.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-memory-index-profiles` — The searchable memory layer made from synced pages, chunks, embeddings, summaries, facts, and member or workspace profiles.
- `reg-object-artifact-site-store` — The shared store of workspace objects, files, artifacts, previews, reports, websites, todos, and objective records.
- `reg-object-change-journal` — The durable history of object changes, recording who changed what and what the object looked like before and after.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-delegation-state` — The parent-child work state that tracks subagent turns, their contracts, trace links, pending results, and delivery back to the parent.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-observability-trace` — The tracing, health, logging, and traceparent state used to connect work across turns, subagents, workers, and cleanup.
- `reg-portal-slots-ui-state` — The structured conversation portal display state that extensions can fill with artifacts, sources, tasks, sites, and automations.
- `reg-database-connection-pools` — Process-global database engines, sessions, transaction handles, and connection pools shared by serving, workers, migrations, and cleanup code.
- `reg-durable-workflow-checkpoints` — Saved workflow execution/checkpoint state used to resume, repair, cancel, or finalize long-running workflows after pauses, crashes, or worker handoff.
- `reg-external-client-connection-pools` — Process-global HTTP/gRPC client sessions, proxy clients, DNS/TLS state, and connection pools used for model providers, connectors, cloud storage, and sandbox services.
- `reg-provider-rate-limit-backoff` — Shared throttling, retry-after, backoff, and concurrency state for AI providers and external connector APIs, separate from billing spend caps.
- `reg-browser-automation-sessions` — Live browser automation contexts, pages, cookies, downloads, screenshots, and backend session handles used by tool execution and released during teardown.
- `reg-running-command-process-state` — Active shell commands, subprocesses, terminal tasks, process identifiers, execution logs, and interrupt state tied to conversations or sandboxes.
- `reg-tool-bridge-invocation-state` — Durable request/result state for sandbox-to-host tool bridge calls, including pending bridge invocations, approvals, idempotency, and returned outputs.
- `reg-user-feedback-buffer` — Collected user/operator feedback events, ratings, comments, and review signals used by telemetry, diagnostics, and offline improvement loops.
- `reg-turn-assembly-snapshot` — The resolved per-turn host package handed into execution, including selected agent/model, effective prompts, allowed tools/spawn menu, seeded file digests, skills, and routing choices.
- `reg-turn-token-budget-state` — Per-turn context and token budget state used to trim history, set completion limits, manage prompt-cache assumptions, and reconcile model usage with billing.
