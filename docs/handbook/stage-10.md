# Turn engine execution and model interaction loop  `stage-10`

This stage is the main work loop for an agent turn. A “turn” is one unit of work, such as answering a user message or running a subagent task. runtime/queue.py takes a waiting turn from the durable queue, builds the needed model, tools, permissions, sandbox, and billing checks, then hands it to runtime/engine.py. The engine is the careful supervisor: it rebuilds the conversation, calls the agent runner, saves progress, records costs, and publishes the final or paused result without repeating work after a crash.

harness/agent.py runs the back-and-forth between the AI model and tools. harness/rounds.py manages one live model response, streaming text, collecting tool requests, timing, reasoning notes, and usage data. harness/replies.py removes hidden routing tags from model text while preserving what they mean. runtime/ext/hooks.py lets extensions inspect, enrich, or block actions at key points.

The conversation and compaction parts keep history safe and short enough for the model. The provider adapters make different AI services look the same. The __init__.py files simply make these folders importable.

## Sub-stages

- [Conversation state, contracts, and compaction](stage-10.1.md) `stage-10.1` — 9 files
- [Model request streaming and provider normalization](stage-10.2.md) `stage-10.2` — 5 files

## Files in this stage

### Runtime ingress
Package markers establish import boundaries, and the queue runner claims durable turn work and prepares it for execution.

### `core/src/ufo/runtime/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language, tools, and readers that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may hold useful tools, but the label itself does not do the work. Here, the drawer is `ufo.runtime`, which likely contains code involved in running or coordinating the system at runtime. Because this file is empty, it does not set up state, import helper modules, define shortcuts, or run startup logic. Its value is structural: without it, some Python environments or packaging tools might not recognize the folder as a normal package, and imports that expect `ufo.runtime` to exist could fail or behave differently.


### `core/src/ufo/runtime/turns/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes `ufo.runtime.turns` a named area of the codebase, likely meant to contain code related to “turns” in the runtime. Because the file is empty, it does not create objects, run setup steps, or change behavior when imported. Its value is structural: without it, some tools or Python import modes might not recognize this folder as a normal package, which could make imports less predictable. Think of it like a label on a drawer: it does not hold the tools itself, but it lets the rest of the workshop refer to that drawer by name.


### `core/src/ufo/runtime/queue.py`

`orchestration` · `main loop / turn execution`

A “turn” is one unit of agent work, such as responding to a user message or running a spawned subagent task. This file is the factory floor for those turns. It first installs a process-wide Runtime, which holds shared services such as the database queue client, model registry, sandbox provider, skill registry, credentials, and event hub. When DBOS, the durable workflow system, starts a turn workflow, this file claims the turn so only one worker owns it, applies any extension-provided workspace setup, and loads the turn, agent, and audience from the database.

From there it makes several important decisions. It chooses the model, freezes billing information so retries cannot re-price the same attempt differently, decides which skills and tools are visible, checks subagent profile rules, and opens or reuses the sandbox where commands run. Think of it like preparing a workshop before inviting the agent in: the right tools are laid out, the safety rules are posted, the budget is pinned down, and the workbench is connected to the correct project.

It then builds a TurnEngine, which performs the actual model/tool loop. Around that engine, this file provides durable failure handling: if setup fails, it writes a terminal failure frame; if sandbox capacity is unavailable, it can park the turn for later; if a child subagent finishes, it delivers the result back to its parent conversation; and when one turn ends, it offers the next queued turn for the same conversation.

#### Function details

##### `_turn_gates`  (lines 157–173)

```
def _turn_gates(parent_turn_id: UUID | None, admission_source: TurnAdmissionSource) -> tuple[asyncio.Semaphore, ...]
```

**Purpose**: Decides whether a turn must wait for a local fairness slot before running. It especially limits waves of scheduled, root-level turns so they cannot crowd out member-triggered work in the same worker process.

**Data flow**: It receives the turn’s parent id and admission source. If the turn is not a root scheduled turn, it returns no gates. If it is a root scheduled turn, it finds or creates an asyncio semaphore, which is a limited-capacity lock, and returns it so the caller can wait for a slot.

**Call relations**: _execute_turn calls this before running the turn. The returned gates are entered while setup and execution begin, which gives scheduled work a fair share without changing the durable queue’s overall capacity.

*Call graph*: called by 1 (_execute_turn); 2 external calls (Semaphore, get_running_loop).


##### `_Rates.of`  (lines 201–209)

```
def of(cls, price: ModelPrice) -> '_Rates'
```

**Purpose**: Copies token price fields from a model price card into the small serializable _Rates shape. This is used when freezing billing data onto a turn row.

**Data flow**: It receives a ModelPrice containing costs for input tokens, output tokens, and cache operations. It copies those numeric fields into a new _Rates object. The output is a compact rate record that can later be stored or embedded in billing identity data.

**Call relations**: _TurnBilling._identity uses this while constructing the billing identity for the chosen model and any alternate models.


##### `_Rates.price`  (lines 211–219)

```
def price(self) -> ModelPrice
```

**Purpose**: Turns stored _Rates data back into a ModelPrice object. This lets frozen billing data be used by pricing code that expects the normal price-card type.

**Data flow**: It reads the rate fields on the _Rates instance. It places those values into a new ModelPrice. The result is a model price object with the same numbers.

**Call relations**: _BillingIdentity.pricing uses this to rebuild a full Pricing object from the frozen identity stored for a turn attempt.

*Call graph*: 1 external calls (__init__).


##### `_BillingIdentity.pricing`  (lines 235–242)

```
def pricing(self) -> Pricing
```

**Purpose**: Builds the pricing table that the whole turn attempt should bill against. It includes the chosen model and any allowed alternate models, all using the frozen rates for this attempt.

**Data flow**: It reads the billing identity’s main model, digest, and alternate rate records. It converts each stored rate record back into a ModelPrice and packages them into a Pricing object. The output is the stable pricing card passed into the turn engine.

**Call relations**: _run_turn passes this pricing result into TurnEngine so live usage, terminal cost records, and recovery runs all agree on the same rates.

*Call graph*: 1 external calls (__init__).


##### `_plan_pricing`  (lines 245–246)

```
def _plan_pricing(models: tuple[str, ...]) -> Pricing
```

**Purpose**: Creates a zero-dollar pricing card for models served under a member’s already-paid plan. This records token usage without charging per token.

**Data flow**: It receives model names. It pairs each model with the special zero-price PLAN_SERVED_PRICE and asks the pricing helper to build a Pricing object. The result is a digest-backed price card for plan-funded serving.

**Call relations**: _TurnBilling._identity uses this when the resolved model is plan-funded. _TurnBilling._validate uses it later to confirm that frozen billing still matches the model’s current funding mode.

*Call graph*: called by 2 (_identity, _validate); 1 external calls (pricing_from).


##### `_TurnBilling.resolve`  (lines 257–271)

```
async def resolve(self) -> tuple[_BillingIdentity, ResolvedModelClient, bool]
```

**Purpose**: Finds or creates the frozen billing identity for one turn attempt, then verifies that the model’s payer and funding did not change underneath the run. It prevents retries and recoveries from billing the same attempt in inconsistent ways.

**Data flow**: It starts with the registry, turn id, attempt id, candidate model, and alternates. It reads any stored billing identity for this attempt; if none exists, it resolves the candidate model and writes a frozen identity. It validates the identity against the resolved model, freezes whether a bring-your-own-key style payer is involved, and returns the billing identity, the model client, and the payer verdict.

**Call relations**: _run_turn calls this after choosing the model. It delegates to _stored_billing_identity, _identity, _frozen_billing_identity, _validate, and _frozen_byok before handing the stable billing result to TurnEngine.

*Call graph*: calls 5 internal fn (_identity, _validate, _frozen_billing_identity, _frozen_byok, _stored_billing_identity); 1 external calls (__init__).


##### `_TurnBilling._identity`  (lines 273–289)

```
def _identity(self, model: str, resolved: ResolvedModelClient) -> _BillingIdentity
```

**Purpose**: Builds the proposed billing identity for a newly started attempt. It captures the selected model, its rates, payer information, and alternate model rates in one record.

**Data flow**: It receives a model name and a resolved model client. If the model is plan-funded, it builds a zero-dollar plan pricing card; otherwise it uses the registry’s normal pricing. It copies the chosen model’s rates plus alternate rates into a _BillingIdentity object.

**Call relations**: _TurnBilling.resolve calls this when no billing identity has already been stored for the attempt. The candidate identity is then frozen through _frozen_billing_identity.

*Call graph*: calls 1 internal fn (_plan_pricing); called by 1 (resolve); 1 external calls (__init__).


##### `_TurnBilling._validate`  (lines 292–299)

```
def _validate(billing: _BillingIdentity, model: ResolvedModelClient) -> None
```

**Purpose**: Checks that the stored billing identity still matches the model client that will serve the turn. It catches cases where a model changed payer or funding mode during an attempt.

**Data flow**: It receives the frozen billing identity and the currently resolved model client. It compares funding, payer, and whether the stored price digest looks like plan-funded pricing. It returns nothing on success, or raises ModelFundingChanged if the run would no longer bill consistently.

**Call relations**: _TurnBilling.resolve calls this before returning billing information to _run_turn. If validation fails, the turn setup fails rather than silently charging the wrong party.

*Call graph*: calls 1 internal fn (_plan_pricing); called by 1 (resolve); 1 external calls (__init__).


##### `_without_workspace_skills`  (lines 302–305)

```
async def _without_workspace_skills(name: str) -> None
```

**Purpose**: Represents a no-op loader for the member tier of workspace skills when that feature is switched off. It deliberately makes workspace-saved skills unavailable through this route.

**Data flow**: It receives a skill name but does not load or return anything. The before state and after state are intentionally the same: no workspace skill is made visible.

**Call relations**: This is a small policy helper for skill-loading paths. It exists so callers can use a loader shape even when workspace skills are disabled.


##### `_member_skill_turn`  (lines 308–320)

```
def _member_skill_turn(turn: Turn) -> bool
```

**Purpose**: Decides whether a turn is the kind of turn that should see member-saved skills in its prompt. It excludes prepared intents and internal root machine turns that have no real user-facing topic.

**Data flow**: It reads the turn’s admission source, speaker member id, and parent turn id. It returns false for prepared intents and speakerless internal root turns, and true for normal member-relevant turns. The output is a simple yes-or-no gate.

**Call relations**: _member_skill_block uses it before adding the saved-skills block. _run_turn also uses it before launching shadow skill-selection measurement.

*Call graph*: called by 2 (_member_skill_block, _run_turn).


##### `_member_skill_block`  (lines 323–330)

```
def _member_skill_block(turn: Turn, view: MemberVisibility, enabled: bool) -> str
```

**Purpose**: Chooses the text block of saved member skills to include in a turn’s founding message. It only includes that text when the feature is enabled and the turn is eligible.

**Data flow**: It receives a turn, a member skill visibility view, and an enabled flag. It asks _member_skill_turn whether the turn qualifies. If not enabled or not qualified, it returns an empty string; otherwise it returns the view’s skill block.

**Call relations**: Environment assembly can use this policy to decide what the model sees. It relies on _member_skill_turn so prompt injection and skill recall stay aligned.

*Call graph*: calls 1 internal fn (_member_skill_turn).


##### `_prompt_skill_index`  (lines 333–337)

```
def _prompt_skill_index(skills: SkillRegistry, enabled: bool) -> tuple[tuple[str, str], ...]
```

**Purpose**: Chooses which skill index should be rendered into the prompt. When member skills are enabled, it uses the fold-aware prompt index; otherwise it shows only the base deployed skill index.

**Data flow**: It receives the skill registry and a feature flag. If enabled, it asks the selection module for a prompt-ready index. If disabled, it asks the registry for its regular index. The result is a tuple of skill-name/index text pairs.

**Call relations**: This supports prompt construction in the environment layer. It calls either prompt_index or SkillRegistry.index depending on the workspace skill policy.

*Call graph*: calls 1 internal fn (index); 1 external calls (prompt_index).


##### `_fire_shadow_selection`  (lines 343–350)

```
def _fire_shadow_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Starts a background experiment that compares lexical skill selection with vector-search skill selection. It does not slow down the user’s turn.

**Data flow**: It receives the index service, embedding service, turn, and skill cards. It creates an asynchronous task to run _shadow_skill_selection and stores the task in a set so it is not garbage-collected. When the task finishes, it removes itself from that set.

**Call relations**: _run_turn calls this only for eligible main-agent turns where the full skill catalog does not fit in the prompt. The real turn continues immediately while _shadow_skill_selection logs evidence in the background.

*Call graph*: calls 1 internal fn (_shadow_skill_selection); called by 1 (_run_turn); 1 external calls (create_task).


##### `_shadow_skill_selection`  (lines 353–379)

```
async def _shadow_skill_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Compares two possible ways of choosing skills for a turn and logs the difference. It is measurement-only, so failures never affect the actual turn.

**Data flow**: It reads the turn’s inbound text and trims it to the maximum query size. It embeds that text, searches the vector index, also computes lexical top-k skill matches, and logs both result lists. If anything fails or times out, it logs a failure class instead of raising to the turn.

**Call relations**: _fire_shadow_selection launches this as a fire-and-forget task. It calls the embedder, vector index, and lexical selector to collect data for future skill-selection decisions.

*Call graph*: calls 2 internal fn (embed, vector); called by 1 (_fire_shadow_selection); 3 external calls (timeout, log, select_top_k).


##### `with_implied_grants`  (lines 389–392)

```
def with_implied_grants(names: set[str]) -> set[str]
```

**Purpose**: Adds companion permissions that must travel with a named tool or action. For example, granting a skill loader also grants the skill search action it needs to be useful.

**Data flow**: It receives a mutable set of grant names. For each name already present, it looks up any implied companions and adds them to the same set. It returns the widened set.

**Call relations**: _agent_actions, _agent_tools, _subagent_actions, and _subagent_tools all call this before selecting permissions. This keeps grant behavior consistent across main agents and subagents.

*Call graph*: called by 4 (_agent_actions, _agent_tools, _subagent_actions, _subagent_tools).


##### `_agent_actions`  (lines 395–410)

```
def _agent_actions(actions: Mapping[str, Mapping[str, BoundAction]], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> frozenset[str]
```

**Purpose**: Computes which canonical object actions a main agent is allowed to use on this turn. A canonical action id is the stable internal name for an action, even if different extensions expose tools around it.

**Data flow**: It receives all registered actions, an optional allowlist, admission source, and optional speaker id. If there is no allowlist, or if this is a speaking prepared intent, it grants every non-profile-only action. Otherwise it expands implied grants and returns only action ids named by the allowlist and actually present in the registry.

**Call relations**: This mirrors the tool-selection rules used by _agent_tools. It is part of the environment’s permission calculation for object actions.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `SubagentKeyWithdrawn.__init__`  (lines 425–440)

```
def __init__(self, profile: str, connect_url: str | None=None, rate_limited: bool=False) -> None
```

**Purpose**: Builds a clear error for a subagent profile that requires the member’s own provider account but no usable account is available. The message points the member toward reconnecting or replacing that account.

**Data flow**: It receives the profile name, optional connect URL, and whether the problem is rate limiting. It formats a human-readable cause and stores the profile and rate-limit flag on the exception. The output is an exception ready to be raised.

**Call relations**: _subagent_model raises this when a profile cannot legally fall back to the deployment’s model key. The error then travels through turn failure handling like other setup errors.

*Call graph*: called by 1 (_subagent_model).


##### `_member_accounts_connectable`  (lines 443–445)

```
def _member_accounts_connectable(runtime: 'Runtime') -> bool
```

**Purpose**: Checks whether this deployment has any extension capable of storing a member’s own model-provider account. This changes how missing account keys should be explained.

**Data flow**: It reads the runtime’s extension manifests. If any manifest declares that it connects member accounts, it returns true; otherwise it returns false.

**Call relations**: _run_turn passes this information into the Subagents helper. _subagent_model uses it to decide whether a missing own-account model should become a SubagentKeyWithdrawn error.

*Call graph*: called by 2 (_run_turn, _subagent_model).


##### `_subagent_model`  (lines 448–479)

```
def _subagent_model(profile: SubagentProfile, connected: str | None, agent: Agent, runtime: 'Runtime', pinned: str | None, document: str | None) -> str
```

**Purpose**: Chooses the model that a subagent turn should run on, while respecting profile rules about member-owned accounts. It prevents a profile designed to spend the member’s own account from silently falling back to the deployment’s key.

**Data flow**: It receives the subagent profile, the connected provider chosen for the member if any, the parent agent, runtime, optional pinned model, and optional environment-document model. It resolves the document model if present, otherwise uses the member-account binding for own-key profiles, otherwise raises if that account is missing. For profiles that do not need an own account, it uses the pinned model, profile model, or agent model in that order.

**Call relations**: _run_turn calls this after loading a subagent profile and member connected providers. It may raise SubagentKeyWithdrawn, and it uses _member_accounts_connectable to choose the correct missing-key behavior.

*Call graph*: calls 2 internal fn (__init__, _member_accounts_connectable); called by 1 (_run_turn).


##### `_own_account_alternates`  (lines 482–509)

```
def _own_account_alternates(profile: SubagentProfile, connected: tuple[str, ...], chosen: str, document: str | None, runtime: 'Runtime') -> tuple[str, ...] | None
```

**Purpose**: Finds other member-connected models a subagent may move to if the first own-account provider is rate-limited. This lets work continue on another account the member already connected.

**Data flow**: It receives the profile, all connected providers, the chosen model, an optional environment-document model, and runtime. If the profile is not an own-account profile or the environment document chose the model, it returns none. Otherwise it resolves all own-key models tied to connected providers and returns every one except the chosen model, but only if the chosen model was among them.

**Call relations**: _run_turn calls this after _subagent_model. Its result becomes alternate billing/model-account information for _TurnBilling and MemberAccounts.

*Call graph*: called by 1 (_run_turn).


##### `_subagent_actions`  (lines 512–530)

```
def _subagent_actions(actions: Mapping[str, Mapping[str, BoundAction]], profile: SubagentProfile, grants: frozenset[str]) -> frozenset[str]
```

**Purpose**: Computes which canonical object actions a subagent profile may use. It combines the profile’s own tool names with inherited grants unless the profile is isolated.

**Data flow**: It receives all registered actions, the profile, and cross-extension grants. It builds the allowed name set, expands implied grants, and returns canonical action ids that match those names. If the profile is not isolated, default subagent actions can also ride along.

**Call relations**: This mirrors _subagent_tools for object-action permissions. It calls with_implied_grants so subagent action grants include required companions.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_with_action_verbs`  (lines 533–554)

```
def _with_action_verbs(selected: tuple[ToolDef, ...], all_tools: tuple[ToolDef, ...], granted_actions: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Adds the basic object-action dispatcher and read tools when a turn has any object actions. This ensures the model can discover and call actions it was granted.

**Data flow**: It receives the currently selected tools, all available tools, and granted action ids. If no actions are granted, it removes the generic object-action dispatcher and returns the rest. If actions are granted, it appends the dispatcher and read tools when they exist and are not already selected.

**Call relations**: This is a permission-polishing helper for tool assembly. It makes action grants usable without requiring allowlists to name every low-level support tool explicitly.


##### `_agent_tools`  (lines 557–581)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses which tool definitions a main agent may call during this turn. It enforces allowlists and keeps profile-only tools away from ordinary agents unless explicitly granted.

**Data flow**: It receives all tools, an optional allowlist, admission source, and optional speaker id. With no allowlist, or for a speaking prepared intent, it returns all non-profile-only tools. With an allowlist, it expands implied grants and returns only matching tool names from the live registry.

**Call relations**: This mirrors _agent_actions for tools. It calls with_implied_grants so named tools bring along required companions such as search.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_resolve_profile`  (lines 584–599)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: Looks up the subagent profile named on a queued turn, and logs helpful detail if it no longer exists. This protects against extensions being removed after a child turn was already admitted.

**Data flow**: It receives the subagent registry, turn id, and requested profile name. It asks the registry for that profile. If the registry raises an unknown-profile error, it logs the requested and registered profile names, then raises the same error onward.

**Call relations**: _run_turn calls this before preparing a subagent turn. It hands the resolved profile into model, tool, and prompt decisions.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 602–614)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses the actual tools a subagent profile may call. It follows the profile’s tool list, inherited grants, isolation setting, and default subagent tools.

**Data flow**: It receives all tools, the profile, and granted names. It builds the allowed set, adds cross-extension grants when the profile is not isolated, expands implied grants, and filters the live tool registry. It returns the selected ToolDef objects.

**Call relations**: This is the tool counterpart to _subagent_actions. It calls with_implied_grants so profile tool selection stays consistent with action selection.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_apply_provisions`  (lines 620–629)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: Applies extension-shipped agent templates to a workspace once per process. It makes sure workspaces have the agents that installed extensions provide.

**Data flow**: It receives the runtime and workspace id. If this process has already provisioned that workspace, it returns. Otherwise it constructs an AgentProvisioning helper from active manifests, applies it to the workspace, and records that this workspace has been handled.

**Call relations**: _execute_turn calls this before running a turn. Doing it at first turn time makes provisioning idempotent and avoids blocking ordinary execution with repeated setup.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `TurnEnvironment.assemble`  (lines 699–699)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Defines the host-layer method that composes everything the runtime needs to run a turn: prompt, tools, hooks, skills, and seeded files. It is a protocol method, meaning this file describes the contract but another object implements it.

**Data flow**: It receives an AssembleRequest containing the turn, agent or profile, model, skills, grants, and environment document id. The implementation is expected to return an AssembledTurn bundle. This file then consumes that bundle to build the TurnEngine.

**Call relations**: _run_turn calls runtime.environment.assemble during setup. The implementation sits outside this file, while this protocol keeps the boundary clear.


##### `TurnEnvironment.environment_model`  (lines 701–701)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Defines how the host layer can report whether an environment document pins a specific model. The runtime asks this before billing so model choice and cost are decided early.

**Data flow**: It receives an environment document identifier and an optional subagent profile name. The implementation returns a model name if the document chooses one, or none if it does not.

**Call relations**: _run_turn calls this before resolving the model. Its result can override the pinned model and is then validated through the model registry and billing flow.


##### `TurnEnvironment.clis`  (lines 703–703)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Defines how the host layer exposes configured command-line credentials. These are later passed into the sandbox so tools like git can authenticate safely.

**Data flow**: It takes no extra input beyond the environment implementation. It returns a dictionary of CLI credential definitions keyed by name. The runtime uses those definitions when opening or authorizing sandbox sessions.

**Call relations**: _run_turn reads this before constructing the sandbox opener and SandboxAuthorizer. _open_sandbox and SandboxAuthorizer.authorize then use the returned credentials to build environment variables.


##### `TurnEnvironment.slots`  (lines 705–705)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Defines which credential slots the environment can request for sandbox execution. A credential slot is a named place where a secret or provider key may be supplied.

**Data flow**: It takes no extra input beyond the environment implementation. It returns a tuple of credential-slot declarations. Those declarations are later used to derive sandbox environment variables only when the sandbox is actually opened.

**Call relations**: _run_turn passes these slots into _open_sandbox. This keeps credential reading lazy, so a turn that never opens the sandbox does not read those secrets.


##### `init_runtime`  (lines 740–751)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the single Runtime object for this process. It also seeds sandbox carriers with bundled system skills so sandboxes can mount built-in skill archives.

**Data flow**: It receives a fully built Runtime. If a runtime is already installed, it raises an error. Otherwise it builds a SystemSkillBundle from bundled skills, seeds any compatible sandbox carriers, and stores the runtime in the module global.

**Call relations**: The server startup path is expected to call this once before DBOS workflows run. _execute_turn later reads the stored runtime for every turn.

*Call graph*: calls 1 internal fn (from_skills).


##### `reset_runtime`  (lines 754–759)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed Runtime so tests can install a fresh one. Production serving normally initializes once and does not reset.

**Data flow**: It takes no input. It sets the module-level runtime variable back to none. The result is that a later init_runtime call can succeed.

**Call relations**: This is a testing seam around the global runtime used by _execute_turn. It avoids tests having to mutate the global directly.


##### `_execute_turn`  (lines 762–844)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the top-level durable workflow body for one turn. It binds the workspace, claims fairness slots, applies setup, runs the turn, and performs parent/next-turn handoffs.

**Data flow**: It receives workspace id and turn id as strings from the workflow system. It loads identifying turn fields from the database, determines authority and fairness gates, applies provisions, enters tracing and workspace/model contexts, then calls _run_turn. If setup fails, it writes a failed terminal; afterward it delivers child results and offers the next turn. It returns a status string such as failed, parked, or superseded.

**Call relations**: turn_workflow calls this directly. It coordinates _turn_gates, _apply_provisions, _run_turn, _commit_failed_terminal, _deliver_to_parent, and _offer_next_turn around the durable DBOS workflow.

*Call graph*: calls 6 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _offer_next_turn, _run_turn, _turn_gates); called by 1 (turn_workflow); 11 external calls (AsyncExitStack, select, monotonic, workspace_tx, emit_histogram, turn_span, agent, turn_authority, model_authority, ws (+1 more)).


##### `_offer_next_turn`  (lines 847–865)

```
async def _offer_next_turn(runtime: Runtime, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> None
```

**Purpose**: After a turn ends, asks the conversation dispatcher to start the next queued turn and re-admit any arrivals the ended turn left behind. This keeps a conversation moving even after failures.

**Data flow**: It receives the runtime, workspace id, conversation id, and just-ended turn id. It calls the dispatcher, then asks the workspace invoker to redispatch pending arrivals. If this handoff itself fails, it logs the delay and does not change the ended turn’s result.

**Call relations**: _execute_turn calls this after every turn attempt when it knows the conversation id. It hands work to dispatch_next_turn and the runtime’s invoker.

*Call graph*: called by 1 (_execute_turn); 3 external calls (invoker_for, log, dispatch_next_turn).


##### `_deliver_to_parent`  (lines 868–900)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: If this turn was a child subagent and has a terminal result, delivers that result back to the parent conversation. It reads the durable database state rather than trusting local memory.

**Data flow**: It receives the runtime and child turn id. It loads the turn row, returns early if there is no parent or no terminal result, and otherwise validates the row as a Turn and asks SubagentResult to deliver it. Delivery failures are logged but not raised, because the child’s terminal result is already committed.

**Call relations**: _execute_turn calls this after _run_turn or failure handling. It uses runtime.invoker_for and runtime.subagents to send the result back to the spawning conversation.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_SandboxSetup.run`  (lines 910–937)

```
async def run(self, sandbox: Sandbox, preload: tuple[LoadedSkill, ...], files: tuple[EnvironmentFile, ...]) -> None
```

**Purpose**: Prepares the sandbox before the model starts by mounting preloaded skills and writing environment-seeded files. If the sandbox provider is temporarily unavailable, it may park the turn for retry.

**Data flow**: It receives a sandbox, loaded skills, and environment files. It loads skills into the sandbox and writes each file. If sandbox setup fails due to provider unavailability and the engine can represent that as a parked turn, it reads transcript state, calls _park, and raises the parking signal.

**Call relations**: _run_turn calls this before creating and running TurnEngine. It delegates unavailable-provider handling to sandbox_provider_park and _SandboxSetup._park.

*Call graph*: calls 2 internal fn (write_file, _park); 4 external calls (__init__, span, sandbox_provider_park, load_skills).


##### `_SandboxSetup._park`  (lines 939–978)

```
async def _park(self, parked: TurnParked, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Marks a running turn as parked because the sandbox provider cannot serve it right now. Parking means the turn should resume later instead of failing permanently.

**Data flow**: It receives a TurnParked description and the inbound messages already absorbed by the transcript. It updates the turn row to parked only if this attempt still owns it, releases unabsorbed inbound messages, publishes a Parked event to the hub when possible, and records metrics and logs.

**Call relations**: _SandboxSetup.run calls this after detecting a parkable sandbox-provider failure. The hub publish tells listeners the turn is paused, while database updates make retry durable.

*Call graph*: called by 1 (run); 6 external calls (__init__, update, workspace_tx, emit_metric, log, turn_profile).


##### `_run_turn`  (lines 981–1264)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs the actual TurnEngine for one turn. This is the central assembly line that turns database records and runtime services into a live model/tool execution.

**Data flow**: It starts with a turn id string and the current DBOS attempt id. It claims the turn, loads the turn, agent, and audience, resolves subagent profile and model choices, freezes billing, asks the environment to assemble prompt/tools/hooks/skills/files, opens sandbox access lazily, prepares sandbox contents, builds TurnEngine with all services and permissions, then runs either the normal loop or intent path. It returns the resulting status, or failed/parked/superseded when appropriate.

**Call relations**: _execute_turn calls this inside workspace and tracing contexts. It calls many helpers in this file, including _load_turn, _run_lineage, _resolve_profile, _subagent_model, _own_account_alternates, _TurnBilling.resolve, _open_sandbox, _SandboxSetup.run, _fire_shadow_selection, and _commit_failed_terminal.

*Call graph*: calls 10 internal fn (_commit_failed_terminal, _fire_shadow_selection, _load_turn, _member_accounts_connectable, _member_skill_turn, _own_account_alternates, _previous_turn_ended_at, _resolve_profile, _run_lineage, _subagent_model); called by 1 (_execute_turn); 28 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 1022–1024)

```
def subagents_for(authority: ExecutionAuthority) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates an authority-scoped view of subagent spawning for a particular execution authority. It lets the engine ask for subagent abilities under the correct acting identity.

**Data flow**: It receives an ExecutionAuthority. It asks the Subagents helper to authorize that authority, then returns both the spawn interface and the authorized helper. The output narrows what subagent work can be requested.

**Call relations**: _run_turn defines this nested helper while building TurnEngine. The engine receives it so different tool calls can spawn subagents using the right authority.


##### `_commit_failed_terminal`  (lines 1267–1338)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, attempt: str, error: BaseException) -> None
```

**Purpose**: Writes a durable failed terminal frame when something goes wrong outside the engine’s own failure path. This ensures clients waiting for the turn get a final answer instead of hanging.

**Data flow**: It receives the hub, turn id, attempt id, and exception. It builds a TerminalFrame from the error class and message, then repeatedly tries to update the turn row if it is still queued or still running under this attempt. On success it emits metrics, logs the stack, publishes the terminal event, and returns. If database or publish-adjacent work fails, it waits with backoff and retries.

**Call relations**: _execute_turn and _run_turn call this when setup or orchestration fails. If the engine already wrote a terminal, this update matches no row and exits without double-counting.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 12 external calls (__init__, __init__, sleep, and_, or_, update, workspace_tx, emit_metric, formatted_stack, log (+2 more)).


##### `turn_workflow`  (lines 1342–1343)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Exposes turn execution as the named DBOS workflow. DBOS uses this durable entry point to run or recover turns.

**Data flow**: It receives workspace id and turn id strings from DBOS. It immediately passes them to _execute_turn and returns that status string. It does not add its own logic.

**Call relations**: This is the workflow wrapper around _execute_turn. The DBOS decorator registers it under the turn workflow name so queue workers can invoke it durably.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 1346–1441)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Loads the complete turn record plus its agent and conversation audience from the database. It converts raw database JSON into typed objects used by the runtime.

**Data flow**: It receives a turn UUID. It queries the turn, agent, and conversation tables, then builds a Turn object, an Agent object, and an Audience object. It also validates nested fields such as context, terminal frames, runtime config, and created object references.

**Call relations**: _run_turn calls this after claiming a turn, and also when a claim was already superseded so transcript repair can work from durable state.

*Call graph*: called by 1 (_run_turn); 9 external calls (__init__, __init__, model_validate, model_validate, model_validate, model_validate, select, workspace_tx, parse_audience).


##### `_run_lineage`  (lines 1444–1476)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: Finds where live activity from a spawned turn should be published. For child turns, it traces up to the root turn so user interfaces can tail one coherent activity stream.

**Data flow**: It receives a Turn. If the turn has no parent, it returns none. Otherwise it follows parent_turn_id links in the database until it reaches the root, determines the profile name or agent name for display, and returns a RunLineage record.

**Call relations**: _run_turn calls this before constructing TurnEngine. The resulting lineage tells the engine and hub how to label and route child activity.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 1479–1491)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: Finds when the previous turn in the same conversation ended. This gives the engine timing context for the current non-intent turn.

**Data flow**: It receives a Turn. If this is the first turn in the conversation, it returns none. Otherwise it queries the previous sequence number’s updated_at timestamp and ensures the returned datetime has a timezone.

**Call relations**: _run_turn calls this for normal turns but skips it for prepared intents. The timestamp is passed into TurnEngine.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_frozen_billing_identity`  (lines 1494–1515)

```
async def _frozen_billing_identity(turn_id: UUID, candidate: _BillingIdentity) -> _BillingIdentity
```

**Purpose**: Stores the billing identity for a turn attempt if one is not already frozen for that same attempt. It protects against two recoveries choosing different price cards.

**Data flow**: It receives a turn id and candidate billing identity. Inside a database transaction, it locks the turn row, checks any stored billing identity, and returns the stored one if it belongs to the same attempt. Otherwise it writes the candidate identity to the row and returns it.

**Call relations**: _TurnBilling.resolve calls this when creating billing data for an attempt. It works with _stored_billing_identity and _frozen_byok to make billing replay-safe.

*Call graph*: called by 1 (resolve); 4 external calls (model_dump, select, update, workspace_tx).


##### `_stored_billing_identity`  (lines 1518–1528)

```
async def _stored_billing_identity(turn_id: UUID, attempt: str) -> _BillingIdentity | None
```

**Purpose**: Reads the billing identity already stored for a specific turn attempt. It ignores identities from older or different attempts.

**Data flow**: It receives a turn id and attempt id. It reads the billing_identity field from the database, validates it as a _BillingIdentity, and returns it only if its attempt matches. If there is no stored identity or it belongs to another attempt, it returns none.

**Call relations**: _TurnBilling.resolve calls this first. If it returns a usable identity, the resolver avoids recomputing or rewriting the billing card.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `_frozen_byok`  (lines 1531–1580)

```
async def _frozen_byok(turn_id: UUID, decided: bool, attempt: str) -> bool
```

**Purpose**: Freezes whether this attempt is served by a workspace/member-owned key rather than the platform default. This keeps token billing consistent during crash recovery while allowing a later resumed attempt to decide fresh.

**Data flow**: It receives the turn id, the newly decided boolean, and attempt id. It reads any stored byok verdict for that attempt; if present, it returns it. Otherwise it writes the decision for this attempt when possible, reads back the settled value, and returns the settled value or the original decision.

**Call relations**: _TurnBilling.resolve calls this after model resolution. The returned boolean is passed into TurnEngine so usage accounting knows whether the run used a non-platform payer.

*Call graph*: called by 1 (resolve); 4 external calls (or_, select, update, workspace_tx).


##### `_open_sandbox`  (lines 1583–1643)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens or resumes the sandbox container for a turn and supplies the environment variables needed for proxy authorization, git, tool bridge access, grants, and credentials. The sandbox is where shell commands and file work happen.

**Data flow**: It receives the sandbox provider, token codec, turn, optional grant store, CLI credentials, optional credential store, credential slots, and a cache-rewrite flag. It creates a signed RunToken, builds environment variables for conversation id, tool bridge, git proxy auth, CLI grants, and provider keys, then asks ConversationSandbox to open the conversation’s sandbox for this turn. The output is a SandboxSession.

**Call relations**: _run_turn gives this function to _LateSandbox as the lazy opener. It calls token encoding and several exec-environment helpers only when the sandbox is actually needed.

*Call graph*: calls 2 internal fn (open, encode); 7 external calls (__init__, span, cache_git_config, _git_config_env, _grant_cli_env, _keyed_provider_env, cli_git_config).


##### `SandboxAuthorizer.authorize`  (lines 1654–1671)

```
async def authorize(self, authority: ExecutionAuthority) -> Sandbox
```

**Purpose**: Re-authorizes an existing sandbox for a narrower or different execution authority. This lets tool calls run under the right actor without reopening the whole sandbox.

**Data flow**: It receives an ExecutionAuthority. It encodes a new RunToken for the same workspace and turn but with that authority, builds matching CLI grant environment variables, and calls sandbox.authorize while preserving or replacing the relevant proxy and CLI environment names. It returns an authorized Sandbox view.

**Call relations**: _run_turn creates a SandboxAuthorizer and passes its authorize method into TurnEngine for normal turns. The engine can then request sandbox access for specific authorities during tool execution.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### Turn execution core
The engine runs a restart-safe turn lifecycle and applies extension hooks around important runtime decisions.

### `core/src/ufo/runtime/engine.py`

`orchestration` · `turn execution`

Think of this file as the conductor for one full exchange with an agent. A turn may be a normal user message, a resumed parked task, a subagent task, or a direct intent from a UI panel. The engine first claims the turn in the database so only one worker owns it. It then loads prior conversation text, applies prompt hooks, absorbs any new messages that arrived while the model was working, and repeatedly runs model rounds. In each round, the model may write text, request tools, or finish with a final answer. Tool calls are resolved, checked for authority, run with retry-safe identities, and turned back into tool results the model can read. Large tool outputs are saved to files instead of being stuffed back into the prompt, and images are stored as blobs so recovery logs stay small. The file also enforces spending limits, seats, billing, and provider retry behavior. Its most important design idea is durability: model rounds, tool dispatches, arrival drains, and compaction are DBOS steps, meaning a crash replay reuses already-recorded outputs instead of doing the work again. Without this file, a restarted worker could double-send messages, re-run side effects, lose user arrivals, misbill tokens, or close a turn without seeing all messages.

#### Function details

##### `_claim_turn`  (lines 338–386)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued, parked, or same-attempt running turn for this worker attempt. This prevents two workers from running the same turn at the same time.

**Data flow**: It receives a turn id and attempt id, reads the turn and conversation rows, locks the conversation, and updates the turn to running if the current state allows it. It returns whether this was a fresh claim, an adopted same-attempt claim, or no claim at all.

**Call relations**: TurnEngine._mark_running calls this at the start of normal and intent turns. Its result decides whether the engine proceeds or falls into transcript repair for an already-finished or still-owned turn.

*Call graph*: called by 1 (_mark_running); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_activity_goal`  (lines 480–481)

```
def _activity_goal(requesters: Mapping[UUID, ActiveMessage]) -> str
```

**Purpose**: Builds a short summary of what active members asked for, so activity messages can describe tool work in context.

**Data flow**: It takes active requester messages, extracts member-facing text from each, joins them with newlines, and returns that combined goal string.

**Call relations**: Runtime tool preparation and direct intent execution use this before starting background activity summaries for tool calls.

*Call graph*: called by 2 (run_intent, prepare); 1 external calls (member_message_text).


##### `_RoundInput.__repr__`  (lines 496–501)

```
def __repr__(self) -> str
```

**Purpose**: Gives log output a compact description of a model round input without dumping the full prompt.

**Data flow**: It reads the round input fields, counts messages and system characters, and returns a short printable string.

**Call relations**: It is used implicitly when this data object is logged or displayed during model-round work.


##### `EffectiveCall.parallel_safe`  (lines 522–526)

```
def parallel_safe(self) -> bool
```

**Purpose**: Says whether a resolved tool call may run beside other tool calls. This matters because some tools are safe to parallelize and others must stay in order.

**Data flow**: It reads the resolved tool definition and returns that tool's parallel-safety flag.

**Call relations**: Runtime tool scheduling asks this through _RuntimeTools.parallel_safe after resolving a model's tool call.


##### `EffectiveCall.semantic_call`  (lines 528–539)

```
def semantic_call(self) -> ToolUseBlock
```

**Purpose**: Returns the tool call under the real action identity the system should reason about. For bound object actions, this replaces the generic wire name with the specific action name.

**Data flow**: It reads the original model tool call and, when needed, rewrites its name and input to the resolved action form. It returns a ToolUseBlock.

**Call relations**: Activity summaries and final-act parsing use this so they see the meaningful action, not just the transport wrapper.


##### `EffectiveCall.meter_dimensions`  (lines 541–551)

```
def meter_dimensions(self) -> dict[str, str]
```

**Purpose**: Builds stable metric labels for a resolved call. This keeps dashboards grouped by real tool or action instead of arbitrary model-written names.

**Data flow**: It reads the tool definition and optional bound-action metadata, then returns a small dictionary of telemetry dimensions.

**Call relations**: Binding and dispatch paths pass these dimensions to _meter_dispatch and rejection handling.


##### `EffectiveCall.__repr__`  (lines 553–554)

```
def __repr__(self) -> str
```

**Purpose**: Provides a short debug label for a resolved call.

**Data flow**: It reads the semantic call id and model call id and returns a compact string.

**Call relations**: It is used implicitly in logs or debugging views that print EffectiveCall objects.


##### `_BoundToolCall.call`  (lines 565–566)

```
def call(self) -> ToolUseBlock
```

**Purpose**: Provides convenient access to the original model tool call inside a bound dispatch object.

**Data flow**: It reads the wrapped EffectiveCall and returns its ToolUseBlock.

**Call relations**: Dispatch preparation and execution use this property while working with already-bound calls.


##### `_BoundToolCall.__repr__`  (lines 568–569)

```
def __repr__(self) -> str
```

**Purpose**: Gives bound tool calls a compact debug representation.

**Data flow**: It reads the tool name and call id and returns a short printable string.

**Call relations**: It is used implicitly when bound calls appear in logs or exceptions.


##### `_RejectedToolCall.parallel_safe`  (lines 581–584)

```
def parallel_safe(self) -> bool
```

**Purpose**: Marks rejected or malformed calls as not parallel-safe. A bad call stays as an in-order barrier so the model receives feedback in the same order it acted.

**Data flow**: It ignores input fields and always returns false.

**Call relations**: The runtime tool scheduler asks this the same way it asks real resolved calls.


##### `_RejectedToolCall.__repr__`  (lines 586–590)

```
def __repr__(self) -> str
```

**Purpose**: Gives rejected tool calls a compact debug representation including why they failed.

**Data flow**: It reads the call name, call id, outcome, and error class, then returns a printable string.

**Call relations**: It is used implicitly in logging and debugging around tool resolution and dispatch.


##### `_Burn.segments`  (lines 665–674)

```
def segments(self, serving: str, attempt: str, usage_events: Sequence[Usage]) -> tuple[_Segment, ...]
```

**Purpose**: Splits one turn attempt's model usage across the accounts or models that served it. This is needed when a turn fails over from one account to another after rate limiting.

**Data flow**: It receives the current serving model, attempt id, and usage events; cuts the usage at recorded model-change points; totals each slice; and returns billing segments.

**Call relations**: TurnEngine._priced and _record_usage rely on this to calculate and write the right cost for each serving model.

*Call graph*: calls 1 internal fn (_total_usage); 1 external calls (__init__).


##### `DispatchResult._errors_say_something`  (lines 721–729)

```
def _errors_say_something(self) -> 'DispatchResult'
```

**Purpose**: Guarantees every failed tool result has text for the model to read. An empty failure is confusing, so this inserts a generic diagnostic notice.

**Data flow**: After a DispatchResult is built, it checks whether it is an error with blank text. If so, it fills in a standard message and returns the result object.

**Call relations**: Every dispatch result passes through this validator, whether it came from a rejected call, hook denial, raised handler, or empty error result.


##### `ModelStreamError.__init__`  (lines 740–741)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Stores a model stream failure with its original provider error class, message, and any partial output. This lets the engine bill partial usage while still reporting the real model error.

**Data flow**: It receives the provider error class, message, and optional partial text, and stores them in the exception arguments.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after a recorded stream result says the model failed mid-round.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 743–745)

```
def __str__(self) -> str
```

**Purpose**: Formats the model stream error as provider-class plus message. This keeps error detection and terminal messages readable.

**Data flow**: It reads the stored class and message from the exception arguments and returns a string.

**Call relations**: Commit and logging paths use normal exception string conversion when reporting failed turns.


##### `ModelStreamError.model_error_class`  (lines 748–750)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original provider error class for a model stream failure.

**Data flow**: It reads the first stored exception argument and returns it.

**Call relations**: Commit logic and truncation recovery inspect this to distinguish truncation from other model failures.


##### `ModelStreamError.partial_output`  (lines 753–755)

```
def partial_output(self) -> str
```

**Purpose**: Returns text that the model produced before a stream failure. This can be saved so the model can salvage work after truncation.

**Data flow**: It reads the stored partial-output argument and returns it.

**Call relations**: _RuntimeModel.stream uses it when converting a truncation failure into recoverable feedback.


##### `ModelStreamError.model_error_message`  (lines 758–760)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original provider error message for terminal reporting.

**Data flow**: It reads the stored message argument and returns it.

**Call relations**: TurnEngine._commit_once uses it when building a failed TerminalFrame.


##### `TurnParked.__init__`  (lines 766–775)

```
def __init__(self, message: str, retry_at: datetime | None=None, external_retry_count: int | None=None) -> None
```

**Purpose**: Creates a non-final stop signal for a turn that must wait, such as for spending limits, seat status, sandbox availability, or provider retry time.

**Data flow**: It receives a message, optional retry time, and optional external retry count, stores them on the exception, and makes the message available as exception text.

**Call relations**: Spend, seat, model retry, and sandbox retry paths raise this; TurnEngine.run and run_intent catch it and save the turn as parked.

*Call graph*: called by 5 (_enforce_authority_seat, _enforce_seats, _enforce_spend, _stream_retrying_interruption, sandbox_provider_park).


##### `sandbox_provider_park`  (lines 778–797)

```
def sandbox_provider_park(turn: Turn) -> TurnParked | None
```

**Purpose**: Decides whether a sandbox-provider outage should park the turn for retry or fail immediately. It protects inline child turns from being parked in a way their parent cannot use.

**Data flow**: It reads the turn's spawn and retry state, checks retry limits, and either returns a TurnParked with a future retry time or returns null.

**Call relations**: TurnEngine._invoke_dispatch calls this when a tool handler reports sandbox provider unavailability.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_invoke_dispatch); 2 external calls (now, timedelta).


##### `_intent_admits`  (lines 803–808)

```
def _intent_admits(tool: ToolDef) -> bool
```

**Purpose**: Checks whether a prepared member intent is allowed to call a tool. This keeps UI panel actions limited to object mutations or tools that explicitly declare a presentation.

**Data flow**: It reads the tool name and presentation metadata and returns true only for admitted intent lanes.

**Call relations**: TurnEngine.run_intent uses it after resolving an intent call and before binding or dispatching it.

*Call graph*: called by 1 (run_intent).


##### `_context_tag`  (lines 817–837)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Creates the metadata block placed before a member message, including message reference, time, sender, source, and related question. This gives the model useful context without changing the member's words.

**Data flow**: It receives message id, optional turn context, and admission time; formats the time in the sender's timezone when available; and returns a <context> text block.

**Call relations**: TranscriptRepair.load_messages uses it for the founding user message, and TurnEngine._render_arrival uses it for later arrivals.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 840–841)

```
def _bounded(content: str) -> str
```

**Purpose**: Clips tool text to the maximum size the engine will feed back into the model.

**Data flow**: It receives a string, passes it through the shared clipping helper with the tool-result limit, and returns the clipped string.

**Call relations**: TurnEngine._finish_dispatch uses it for error results and fallback cases where large output cannot be offloaded.

*Call graph*: called by 1 (_finish_dispatch); 1 external calls (clipped).


##### `_speaker_hint`  (lines 844–857)

```
def _speaker_hint(error: Exception, member_refs: Sequence[UUID], member_bound: bool=False) -> str
```

**Purpose**: Adds helpful instructions when a tool failed because it needed member authority. It tells the model which active message reference it can name, or why none is available.

**Data flow**: It receives an exception, active member refs, and whether the turn is already bound to the member; if the error is SpeakerRequired, it returns a retry hint, otherwise an empty string.

**Call relations**: _error_text appends this hint whenever it turns an exception into model-readable tool output.

*Call graph*: called by 1 (_error_text).


##### `_schema_hint`  (lines 860–874)

```
def _schema_hint(error: Exception, input_model: type[BaseModel] | None) -> str
```

**Purpose**: Adds a field-list hint when a tool input failed because required fields were missing or extra fields were supplied. This helps the model fix nesting or wrong field names.

**Data flow**: It receives an exception and optional Pydantic input model, inspects validation errors, and returns a plain hint listing accepted top-level fields when useful.

**Call relations**: _error_text appends this to validation failures from tool binding and dispatch preparation.

*Call graph*: called by 1 (_error_text); 2 external calls (errors, is_required).


##### `_error_text`  (lines 877–899)

```
def _error_text(tool_name: str, error: Exception, member_refs: Sequence[UUID]=(), input_model: type[BaseModel] | None=None, member_bound: bool=False) -> str
```

**Purpose**: Turns an exception into clear tool-result text for the model. It preserves the exception class and adds speaker or schema guidance when that would help the model retry correctly.

**Data flow**: It receives the tool name, exception, optional active member refs, optional input model, and member-binding flag; formats the base error; appends hints; and returns text.

**Call relations**: Rejection, dispatch preparation, and handler invocation paths call this whenever an exception becomes a tool error result.

*Call graph*: calls 2 internal fn (_schema_hint, _speaker_hint); called by 3 (_invoke_dispatch, _prepare_dispatch, _rejected).


##### `_meter_dispatch`  (lines 902–936)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str, semantic: Mapping[str, str] | None=None) -> None
```

**Purpose**: Records metrics for one tool dispatch: how often it ran, how long it took, and how it ended. This makes tool health visible in dashboards.

**Data flow**: It receives the tool registry, call, start time, outcome, error class, profile, and semantic labels; builds bounded metric dimensions; emits count and timing metrics.

**Call relations**: TurnEngine._bind_or_error and _dispatch_step call it around binding and dispatch outcomes.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 939–982)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedRef, ...]]
```

**Purpose**: Finds which skills were successfully loaded in the current conversation window. This prevents the engine from reloading skill context that the model already has.

**Data flow**: It scans messages for completed load-skill calls with readable full results, asks the skill registry for each closure, and yields the resolved loaded references.

**Call relations**: TurnEngine._reseed_loaded_skills uses it after compaction or message changes to refresh the loaded-skill tracker.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 985–1007)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured final act payload when the last successful tool call in a round was a specific final-act tool. It is used for acts that only count if they end the round.

**Data flow**: It reads the final tool call and matching result, parses the first JSON payload line from the result text, validates it with the requested model, and returns the payload or null.

**Call relations**: _round_acts uses this for last-call final acts such as questions that should not remain open if the model continued working.

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_pending_act`  (lines 1010–1038)

```
def _pending_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts the most recent successful structured act payload from anywhere in a round. This is used for requests that remain owed until a member or external system answers them.

**Data flow**: It matches tool calls to results, scans backward for the named tool, parses and validates the result payload, and returns it or null.

**Call relations**: _round_acts uses this for pending acts such as credential or connection requests.

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_round_acts`  (lines 1041–1072)

```
def _round_acts(resolved: tuple[_Resolution, ...], results: tuple[ToolResultBlock, ...]) -> dict[str, BaseModel]
```

**Purpose**: Collects all open structured acts left by a tool round, such as asking a user, requesting credentials, or requesting a connection.

**Data flow**: It receives resolved calls and tool results, converts bound actions to semantic calls, applies each tool's final-act rule, and returns a dictionary keyed by terminal-frame field.

**Call relations**: Runtime tool after-round processing and TurnEngine.run_intent use it to decide what should appear in the terminal frame.

*Call graph*: calls 2 internal fn (_final_act, _pending_act); called by 2 (run_intent, after_round).


##### `_act`  (lines 1075–1079)

```
def _act(acts: dict[str, BaseModel], frame_field: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Safely pulls one typed act out of the round-act dictionary.

**Data flow**: It receives the act dictionary, field name, and expected model class; returns the payload only if it has the expected type.

**Call relations**: Runtime tool after-round processing and intent execution use it after _round_acts.

*Call graph*: called by 2 (run_intent, after_round).


##### `_created_refs`  (lines 1082–1114)

```
def _created_refs(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> tuple[ObjectRef, ...]
```

**Purpose**: Finds object references created by successful object-apply calls. This lets the turn terminal name objects that now exist.

**Data flow**: It pairs tool calls with results, reads successful object_apply result JSON, keeps only created outcomes, validates them as ObjectRef values, and returns the refs.

**Call relations**: TurnEngine._fold_created uses it during normal rounds, and run_intent uses it for direct intent results.

*Call graph*: called by 2 (_fold_created, run_intent); 2 external calls (__init__, loads).


##### `_total_usage`  (lines 1117–1125)

```
def _total_usage(usage_events: Sequence[Usage]) -> Usage
```

**Purpose**: Adds many usage records into one usage total. It is the basic token-summing helper for billing and live cost display.

**Data flow**: It receives usage events, sums each token category, and returns a new Usage object containing the totals.

**Call relations**: _Burn.segments, TurnEngine._stream_once, and TurnEngine._publish_cost call it whenever usage needs to be aggregated.

*Call graph*: called by 3 (_publish_cost, _stream_once, segments); 1 external calls (__init__).


##### `_to_harness_reasoning`  (lines 1128–1129)

```
def _to_harness_reasoning(block: ReasoningBlock) -> HarnessReasoning
```

**Purpose**: Converts a runtime reasoning block into the harness format used by the generic agent loop.

**Data flow**: It receives a reasoning block, stores its type and serialized payload, and returns a HarnessReasoning object.

**Call relations**: Model streaming and message conversion use it when handing runtime data to the harness.

*Call graph*: called by 2 (stream, _to_harness_message); 2 external calls (__init__, model_dump).


##### `_from_harness_reasoning`  (lines 1132–1141)

```
def _from_harness_reasoning(block: HarnessReasoning) -> ReasoningBlock
```

**Purpose**: Converts a harness reasoning block back into the runtime model-provider block type.

**Data flow**: It reads the harness block kind, validates the stored payload against the matching runtime class, and returns that runtime block.

**Call relations**: Message conversion from the harness calls it when checkpointing or continuing a turn.

*Call graph*: called by 1 (_from_harness_message); 3 external calls (model_validate, model_validate, model_validate).


##### `_to_harness_call`  (lines 1144–1145)

```
def _to_harness_call(call: ToolUseBlock) -> HarnessToolCall
```

**Purpose**: Converts a runtime tool-use block into the harness tool-call shape.

**Data flow**: It copies the call id, tool name, and input dictionary into a HarnessToolCall.

**Call relations**: Runtime model output and message conversion call it before returning data to AgentEngine.

*Call graph*: called by 2 (stream, _to_harness_message); 1 external calls (__init__).


##### `_from_harness_call`  (lines 1148–1149)

```
def _from_harness_call(call: HarnessToolCall) -> ToolUseBlock
```

**Purpose**: Converts a harness tool call back into the runtime tool-use shape.

**Data flow**: It copies the call id, name, and input into a ToolUseBlock.

**Call relations**: Runtime tools use it while resolving calls and after-round processing; message conversion also uses it.

*Call graph*: called by 3 (_resolve, after_round, _from_harness_message); 1 external calls (__init__).


##### `_to_harness_result`  (lines 1152–1169)

```
def _to_harness_result(result: ToolResultBlock) -> HarnessToolResult
```

**Purpose**: Converts a runtime tool result into the harness result shape, including text and images.

**Data flow**: It reads result content, converts text blocks and image blocks into harness parts when needed, and returns a HarnessToolResult.

**Call relations**: Runtime tool execution and outgoing message conversion use it when interacting with AgentEngine.

*Call graph*: called by 2 (execute, _to_harness_message); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_result`  (lines 1172–1189)

```
def _from_harness_result(result: HarnessToolResult) -> ToolResultBlock
```

**Purpose**: Converts a harness tool result back into runtime result blocks.

**Data flow**: It reads string or multipart harness content, rebuilds runtime text and image blocks, and returns a ToolResultBlock.

**Call relations**: Runtime tools use it after a round to inspect results, and message conversion uses it for checkpointed harness messages.

*Call graph*: called by 2 (after_round, _from_harness_message); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_message`  (lines 1192–1210)

```
def _to_harness_message(message: Message) -> HarnessMessage
```

**Purpose**: Converts one runtime message into the harness message format.

**Data flow**: It copies plain string messages directly; for block messages, it converts text, images, tool calls, tool results, and reasoning blocks one by one.

**Call relations**: _to_harness_messages applies it to full conversations before passing them to AgentEngine.

*Call graph*: calls 3 internal fn (_to_harness_call, _to_harness_reasoning, _to_harness_result); called by 1 (_to_harness_messages); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_message`  (lines 1213–1229)

```
def _from_harness_message(message: HarnessMessage) -> Message
```

**Purpose**: Converts one harness message back into the runtime message format.

**Data flow**: It copies plain strings directly; for block messages, it rebuilds runtime text, image, tool call, tool result, and reasoning blocks.

**Call relations**: _from_harness_messages applies it to full conversations coming back from AgentEngine.

*Call graph*: calls 3 internal fn (_from_harness_call, _from_harness_reasoning, _from_harness_result); called by 1 (_from_harness_messages); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_messages`  (lines 1232–1233)

```
def _to_harness_messages(messages: tuple[Message, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Converts a whole tuple of runtime messages into harness messages.

**Data flow**: It receives runtime messages, maps each through _to_harness_message, and returns a tuple.

**Call relations**: TurnEngine._model_round and runtime conversation/model adapters use it at every boundary with AgentEngine.

*Call graph*: calls 1 internal fn (_to_harness_message); called by 4 (_model_round, prepare, prepare_exhaust, stream).


##### `_from_harness_messages`  (lines 1236–1237)

```
def _from_harness_messages(messages: tuple[HarnessMessage, ...]) -> tuple[Message, ...]
```

**Purpose**: Converts a whole tuple of harness messages into runtime messages.

**Data flow**: It receives harness messages, maps each through _from_harness_message, and returns a tuple.

**Call relations**: TurnEngine._model_round and runtime conversation/model adapters use it when the harness gives messages back.

*Call graph*: calls 1 internal fn (_from_harness_message); called by 5 (_model_round, checkpoint, prepare, prepare_exhaust, stream).


##### `_RuntimeConversation.prepare`  (lines 1257–1278)

```
async def prepare(self, messages: tuple[HarnessMessage, ...], round_index: int) -> HarnessPreparedRound
```

**Purpose**: Prepares the conversation just before a model round. It absorbs new arrivals, enforces spending, refreshes loaded skills, and compacts context if needed.

**Data flow**: It receives harness messages and round index, converts messages to runtime form, drains arrivals into them, checks spend, maybe compacts, records usage, increments the round meter, and returns prepared harness messages plus an interruption flag.

**Call relations**: AgentEngine calls this before model rounds through the conversation adapter.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); 2 external calls (__init__, span).


##### `_RuntimeConversation.checkpoint`  (lines 1280–1282)

```
async def checkpoint(self, messages: tuple[HarnessMessage, ...]) -> None
```

**Purpose**: Saves a consistent completed-round message window in memory. This is the state used if the turn is interrupted or parked.

**Data flow**: It receives harness messages, converts them to runtime messages, stores them on the engine window, and marks that this turn has completed a round.

**Call relations**: AgentEngine calls this after a round reaches a safe checkpoint.

*Call graph*: calls 1 internal fn (_from_harness_messages).


##### `_RuntimeConversation.prepare_exhaust`  (lines 1284–1293)

```
async def prepare_exhaust(self, messages: tuple[HarnessMessage, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Prepares the conversation for a forced final answer after the round budget is exhausted.

**Data flow**: It receives harness messages, enforces spending, compacts if needed, records compaction usage, and returns compacted harness messages.

**Call relations**: AgentEngine calls it when it must stop offering normal tool rounds and push the model toward finishing.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages).


##### `_RuntimeModel.stream`  (lines 1302–1357)

```
async def stream(self, request: HarnessModelRequest, round_index: int) -> HarnessModelRound
```

**Purpose**: Runs one model round through the engine's durable streaming path and returns it in harness form. It also converts truncation into recoverable feedback when possible.

**Data flow**: It receives a harness model request, converts messages and tool schemas, calls the engine's stream-and-recovery logic, publishes seat and cost checks, and returns text, calls, reasoning, and messages as a HarnessModelRound.

**Call relations**: AgentEngine calls this whenever it needs the model. It hands off to TurnEngine._stream_recovering_overflow and converts results back for the harness.

*Call graph*: calls 5 internal fn (__init__, _from_harness_messages, _to_harness_call, _to_harness_messages, _to_harness_reasoning); 4 external calls (__init__, __init__, emit_metric, log).


##### `_RuntimeTools.definitions`  (lines 1370–1380)

```
def definitions(self) -> tuple[HarnessToolDefinition, ...]
```

**Purpose**: Lists the tools available to the harness for this round. It includes or omits the requested_by field depending on whether member refs are meaningful.

**Data flow**: It reads the engine tool registry and active requesters, builds harness tool definitions from runtime schemas, and returns them.

**Call relations**: AgentEngine asks this adapter what tools the model can call.

*Call graph*: 1 external calls (__init__).


##### `_RuntimeTools.parallel_safe`  (lines 1382–1383)

```
def parallel_safe(self, call: HarnessToolCall) -> bool
```

**Purpose**: Tells the harness whether a particular tool call can run in parallel.

**Data flow**: It resolves the harness call to a runtime call and returns that resolution's parallel-safety flag.

**Call relations**: AgentEngine uses this before grouping tool calls for dispatch.

*Call graph*: calls 1 internal fn (_resolve).


##### `_RuntimeTools.prepare`  (lines 1385–1403)

```
async def prepare(self, calls: tuple[HarnessToolCall, ...]) -> None
```

**Purpose**: Resolves and binds all tool calls in a round before execution. It also starts live activity summaries for real bound calls.

**Data flow**: It receives harness calls, resolves each, binds requester authority and context, stores bindings by call id, and launches activity labeling for executable calls.

**Call relations**: AgentEngine calls this before executing tool calls; later execute reads the stored binding.

*Call graph*: calls 2 internal fn (_resolve, _activity_goal); 1 external calls (gather).


##### `_RuntimeTools.execute`  (lines 1405–1413)

```
async def execute(self, call: HarnessToolCall) -> HarnessToolResult
```

**Purpose**: Runs one prepared tool call and returns a harness tool result. If the turn parks during a tool, it records that park and returns the park reason as an error result for the current call.

**Data flow**: It receives a harness call, looks up the bound dispatch input, calls the engine dispatcher, converts the result to harness form, or captures TurnParked state.

**Call relations**: AgentEngine calls this for each tool call after prepare.

*Call graph*: calls 1 internal fn (_to_harness_result); 1 external calls (__init__).


##### `_RuntimeTools.after_round`  (lines 1415–1441)

```
async def after_round(self, calls: tuple[HarnessToolCall, ...], results: tuple[HarnessToolResult, ...]) -> None
```

**Purpose**: Updates turn-level state after a tool round finishes. It records changed workspace targets, newly created objects, and open final acts.

**Data flow**: It receives harness calls and results, converts them to runtime form, folds changed paths and created refs, parses round acts if every call has a result, and clears per-round caches.

**Call relations**: AgentEngine calls this after tool execution so the engine can carry effects into commit and future rounds.

*Call graph*: calls 5 internal fn (_resolve, _act, _from_harness_call, _from_harness_result, _round_acts); 2 external calls (__init__, change_targets).


##### `_RuntimeTools.after_checkpoint`  (lines 1443–1447)

```
async def after_checkpoint(self) -> None
```

**Purpose**: Raises a stored park signal only after the harness checkpoint is safe.

**Data flow**: It reads any parked state, clears it, and raises the TurnParked if present.

**Call relations**: AgentEngine calls it after checkpointing so a parked turn keeps a resumable transcript window.


##### `_RuntimeTools.interrupted`  (lines 1449–1450)

```
def interrupted(self) -> None
```

**Purpose**: Clears a pending user question when the round was interrupted. A partial or interrupted round should not leave a fresh question owed.

**Data flow**: It replaces the tool state's open acts with the question field set to null.

**Call relations**: AgentEngine calls this during interruption handling.

*Call graph*: 1 external calls (replace).


##### `_RuntimeTools._resolve`  (lines 1452–1458)

```
def _resolve(self, call: HarnessToolCall) -> _Resolution
```

**Purpose**: Resolves a harness tool call once and caches the result for this round.

**Data flow**: It checks the resolution cache by call id, converts the harness call if needed, asks the engine to resolve it, stores the result, and returns it.

**Call relations**: definitions of parallel safety, prepare, and after_round all rely on this shared resolution.

*Call graph*: calls 1 internal fn (_from_harness_call); called by 3 (after_round, parallel_safe, prepare).


##### `_RuntimeEvents.speak`  (lines 1466–1467)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Forwards mid-turn marked replies from the harness to the engine's delivery path.

**Data flow**: It receives marked replies and a round number and passes them to TurnEngine._speak.

**Call relations**: AgentEngine uses this event adapter when the model emits member-addressed reply spans during a tool-using round.


##### `_RuntimeEvents.closing`  (lines 1469–1470)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Forwards closing marked replies so they appear on the live text stream.

**Data flow**: It receives marked replies and passes them to TurnEngine._stream_closing_spans.

**Call relations**: AgentEngine uses this at the end of a turn when the final answer includes marked member spans.


##### `_RuntimeEvents.exhausted`  (lines 1472–1479)

```
def exhausted(self) -> None
```

**Purpose**: Records that the model used up its allowed tool-round budget.

**Data flow**: It sets the meter's incomplete reason, emits a metric, and logs the forced-final event.

**Call relations**: AgentEngine calls it when it reaches max rounds and must force a final answer.

*Call graph*: 2 external calls (emit_metric, log).


##### `TranscriptRepair.resolve`  (lines 1494–1518)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes an already-committed terminal frame when a duplicate worker cannot claim the turn. This helps a waiting client finish even if the original worker crashed after committing.

**Data flow**: It reads the terminal from the turn row; if present, validates it, preserves inbound transcript data, publishes the terminal to the hub, and returns the frame. If no terminal exists, it returns null.

**Call relations**: TurnEngine._resolve_unclaimed calls this when _mark_running loses the claim.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 1520–1528)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the final conversation transcript for a completed answered turn.

**Data flow**: It appends the assistant answer to the message list and writes a Conversation record with system and injected context.

**Call relations**: TurnEngine._persist_transcript delegates to this after a done terminal is committed.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_interrupted`  (lines 1530–1546)

```
async def persist_interrupted(self, messages: tuple[Message, ...], ran: bool) -> None
```

**Purpose**: Writes the safe transcript for a turn that ended without a final answer. If the turn completed work, it adds an interruption notice so the next turn does not repeat it blindly.

**Data flow**: It receives messages and whether this turn ran a round, optionally appends the interruption notice, and writes the conversation as from this run.

**Call relations**: TurnEngine._persist_interrupted calls it on failures, cancellations, and non-done terminals.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair._persist_parked`  (lines 1548–1570)

```
async def _persist_parked(self, messages: tuple[Message, ...], absorbed: tuple[UUID, ...], requesters: Mapping[UUID, ActiveMessage], system: str, injected: str) -> bool
```

**Purpose**: Saves a parked turn's resumable conversation window and requester state.

**Data flow**: It receives messages, absorbed arrival ids, requesters, system prompt, and injected context; wraps requester data in a parked record; and writes the conversation.

**Call relations**: TurnEngine._persist_parked calls it before the turn is marked parked.

*Call graph*: calls 1 internal fn (write_conversation); 2 external calls (__init__, __init__).


##### `TranscriptRepair.persist_inbound`  (lines 1572–1591)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves the founding inbound message, and optionally absorbed arrivals, when a turn ends without a full transcript.

**Data flow**: It either loads the normal founding messages or builds a safe denied-founding message, appends arrivals, and writes the conversation.

**Call relations**: TranscriptRepair.resolve uses this before republishing an old terminal; TurnEngine interruption logic uses it through its repair wrapper.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 1593–1603)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the conversation window for a turn. It resumes parked state when present, otherwise combines prior transcript messages with this turn's inbound message.

**Data flow**: It checks for a parked record, returns its saved messages if found, or builds a user message from the turn inbound with a context tag for member turns.

**Call relations**: TurnEngine._load_messages and persist_inbound rely on this to reconstruct model context.

*Call graph*: calls 3 internal fn (_parked_record, _prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._parked_record`  (lines 1605–1614)

```
async def _parked_record(self) -> Conversation | None
```

**Purpose**: Reads the saved parked conversation only if it belongs to this exact turn sequence and was written by a run.

**Data flow**: It reads the transcript, checks sequence, from-run flag, and parked data, then returns the stored Conversation or null.

**Call relations**: TranscriptRepair.load_messages and TurnEngine._prepare_run use it to resume parked turns.

*Call graph*: called by 1 (load_messages).


##### `TranscriptRepair._prior_messages`  (lines 1616–1622)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Returns conversation messages before this turn, never this turn's own previous write. This avoids a replay reading its own transcript as prior context.

**Data flow**: It reads the transcript and returns messages only if the stored sequence is lower than the current turn's sequence.

**Call relations**: load_messages and persist_inbound use it when building a fresh turn window.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 1624–1652)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None, from_run: bool=False, parked: ParkedTurn | None=None) -> bool
```

**Purpose**: Writes a Conversation record with a few retries. Transcript writes are important, but a transient write failure should not immediately erase the turn's history.

**Data flow**: It builds a Conversation object from messages and metadata, tries to write it, sleeps and retries on failure, logs failures, and returns whether the write succeeded.

**Call relations**: All TranscriptRepair persistence methods call this final writer.

*Call graph*: called by 4 (_persist_parked, persist_inbound, persist_interrupted, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 1686–1703)

```
def exited(self, status: str) -> None
```

**Purpose**: Records wall-clock and round-count metrics when a turn execution reaches its first exit.

**Data flow**: It receives an exit status, checks whether it already recorded an exit, calculates elapsed time, emits timing and round metrics, and marks itself ended.

**Call relations**: TurnEngine._commit and park/cancel/preempt paths call it so each execution is counted once.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 1805–1816)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that the engine was wired with consistent audience and tool settings. This catches unsafe setup errors before a turn runs.

**Data flow**: It checks tool extension audiences, hook audience, and whether a subagent output contract conflicts with a real finish tool; it raises on invalid setup.

**Call relations**: Dataclass initialization calls it automatically when a TurnEngine is constructed.


##### `TurnEngine.__repr__`  (lines 1818–1822)

```
def __repr__(self) -> str
```

**Purpose**: Gives the engine a compact debug label containing turn, agent, and profile information.

**Data flow**: It reads ids and profile and returns a printable string.

**Call relations**: It is used implicitly in logs or debugging output that prints the engine.


##### `TurnEngine.profile`  (lines 1825–1828)

```
def profile(self) -> str
```

**Purpose**: Returns the telemetry profile for this turn, such as main, agent, or subagent profile. Metrics use this to separate different kinds of work.

**Data flow**: It reads the turn's subagent and spawned fields and asks the shared profile helper for the label.

**Call relations**: Almost every metric and log path in the engine uses this property.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 1830–1998)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal conversational turn from claim to terminal result. This is the main body that loads context, loops through model/tool rounds, commits the answer, and handles parking, cancellation, and failure.

**Data flow**: It starts metrics, builds ToolContext, claims the turn, prepares messages, loops through model rounds until an answer commits without unseen arrivals, stages carried artifacts, writes transcripts and workspace changes, and publishes terminal or parked frames. On errors it bills, releases unabsorbed arrivals, persists safe transcript state, and re-raises when appropriate.

**Call relations**: The DBOS workflow for a turn calls this. It orchestrates most helper methods in this file, including preparation, model rounds, dispatch, commit, parking, billing, publishing, and cleanup.

*Call graph*: calls 18 internal fn (_bill_cancelled, _carried_artifacts, _commit, _mark_running, _model_round, _park, _persist_interrupted, _persist_parked, _persist_transcript, _prepare_run (+8 more)); 9 external calls (__init__, __init__, __init__, __init__, __init__, partial, monotonic, emit_metric, log).


##### `TurnEngine._rank_find`  (lines 2000–2021)

```
async def _rank_find(self, usage_events: list[Usage], system: str, user: str) -> str
```

**Purpose**: Runs a small model completion used by tool-side search or ranking. Its usage is charged back to the tool dispatch that requested it.

**Data flow**: It receives system and user text, builds a model request, streams text deltas into a string, records usage both globally and in the current tool's find-usage bucket, and returns the text.

**Call relations**: ToolContext exposes this as find; tool handlers can call it during _invoke_dispatch.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._prepare_run`  (lines 2023–2101)

```
async def _prepare_run(self, usage_events: list[Usage], meter: _TurnMeter, absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage]) -> _PreparedRun
```

**Purpose**: Builds the starting state for a normal turn. It resumes parked turns, applies scheduled-memory context, fires prompt hooks, handles denied founding messages, and prepares the first user message.

**Data flow**: It receives usage, meter, absorbed id, and requester accumulators; loads parked state or transcript messages; applies hooks and injected context; may commit a denied answer; and returns system prompt, messages, injected text, or an early terminal.

**Call relations**: TurnEngine.run calls this after claiming and publishing the run start.

*Call graph*: calls 7 internal fn (_commit, _load_messages, _persist_transcript, _publish_terminal, _record_workspace_changes, _repair, _scheduled_system); called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, escape, span).


##### `TurnEngine.run_intent`  (lines 2103–2242)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a direct tool intent without asking the model. This supports prepared UI actions and sandbox bridge calls using the same guarded, durable dispatch path as model tools.

**Data flow**: It claims the turn, parses the inbound intent into one tool call, enforces seats, resolves and authorizes the call, binds context, dispatches the tool step, commits done or failed terminal state, writes transcript, publishes terminal, and handles park/cancel/failure cases.

**Call relations**: Intent workflows call this instead of run when the inbound turn is already a typed tool action.

*Call graph*: calls 19 internal fn (_bind_or_error, _commit, _dispatch_step_recovering, _enforce_seats, _load_messages, _mark_running, _park, _persist_transcript, _publish_terminal, _rejected (+9 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, model_validate_json, monotonic, emit_metric (+1 more)).


##### `TurnEngine._scheduled_system`  (lines 2244–2278)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. If memory search is unavailable or slow, it leaves the prompt unchanged rather than failing the turn.

**Data flow**: It receives the base system prompt, searches memory with the turn inbound as query under a timeout, formats matches as escaped text, and returns the augmented or original prompt.

**Call relations**: TurnEngine._prepare_run calls it for turns admitted from the scheduler.

*Call graph*: called by 1 (_prepare_run); 5 external calls (__init__, timeout, escape, log, audience_subjects).


##### `TurnEngine._mark_running`  (lines 2280–2288)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn as running for the current workflow attempt.

**Data flow**: It passes the turn id and attempt id to _claim_turn and returns whether the claim succeeded.

**Call relations**: TurnEngine.run and run_intent call it before doing any turn work; failure routes to _resolve_unclaimed.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 2290–2291)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a TranscriptRepair helper for this engine's turn, transcript, and hub.

**Data flow**: It reads engine fields and returns a TranscriptRepair object.

**Call relations**: Transcript loading, persistence, parked-state saving, and unclaimed-turn resolution all go through this helper.

*Call graph*: called by 6 (_load_messages, _persist_interrupted, _persist_parked, _persist_transcript, _prepare_run, _resolve_unclaimed); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 2293–2295)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the model conversation window under a tracing span.

**Data flow**: It creates a repair helper, asks it to load messages, and returns the result.

**Call relations**: Turn preparation and intent transcript persistence call this when they need the current transcript window.

*Call graph*: calls 1 internal fn (_repair); called by 2 (_prepare_run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 2297–2386)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the generic agent loop for this turn with runtime adapters for model, tools, conversation, and events.

**Data flow**: It receives context, messages, usage, system prompt, arrival and state accumulators, builds structured-output support if this is a subagent contract, runs AgentEngine, and returns final messages, answer, and any open acts.

**Call relations**: TurnEngine.run calls this inside its answer loop.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); called by 1 (run); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__).


##### `TurnEngine._model_round.validate`  (lines 2330–2331)

```
def validate(call: HarnessToolCall) -> str
```

**Purpose**: Validates a finish-tool payload against the subagent output contract.

**Data flow**: It receives a harness tool call, validates its input with the output model, serializes the accepted payload to JSON, and returns it.

**Call relations**: The structured-output configuration passes this callback to AgentEngine when a subagent has a required output contract.


##### `TurnEngine._model_round.accept_prose`  (lines 2333–2342)

```
def accept_prose(text: str) -> str | None
```

**Purpose**: Accepts short plain prose as a structured result when the output contract allows a freeform result.

**Data flow**: It receives answer text, checks contract type and length, tries to validate it as a result field, and returns serialized JSON or null.

**Call relations**: AgentEngine uses this callback in structured-output mode to turn acceptable prose into a finish result.

*Call graph*: 1 external calls (freeform_result_contract).


##### `TurnEngine._fold_created`  (lines 2388–2416)

```
async def _fold_created(self, created: dict[ObjectRef, None], tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> None
```

**Purpose**: Records newly created object references during the turn before the terminal commit. This keeps created objects visible even if the turn later parks or fails.

**Data flow**: It receives the created accumulator plus calls and results, extracts fresh created refs, updates the accumulator, and writes the full set to the turn row while the turn is non-terminal.

**Call relations**: _RuntimeTools.after_round calls it after each tool round.

*Call graph*: calls 1 internal fn (_created_refs); 2 external calls (update, workspace_tx).


##### `TurnEngine._absorb_arrivals`  (lines 2418–2479)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Drains queued inbound messages into the active conversation window. This lets a long-running turn notice new member messages before answering.

**Data flow**: It calls the durable arrival-claim step, appends admitted or denied messages to the window and arrival log, tracks active requesters and absorbed ids, logs the drain, and publishes absorption frames for member arrivals.

**Call relations**: _RuntimeConversation.prepare calls it before each model round.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 2481–2533)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Delivers mid-turn marked replies to members and live listeners. It uses deterministic ids so crash replay does not double-send them.

**Data flow**: It receives marked replies and round index, skips subagents, writes each reply row if not already present, logs new rows, and publishes live Reply frames.

**Call relations**: _RuntimeEvents.speak forwards harness speak events here.

*Call graph*: calls 1 internal fn (_publish); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 2535–2545)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Publishes final-answer marked spans on the live text stream. This ensures terminal-style clients see the closing answer as text deltas.

**Data flow**: It receives marked replies and publishes each reply's text as a TextDelta.

**Call relations**: _RuntimeEvents.closing calls it at the end of the agent loop.

*Call graph*: calls 1 internal fn (_publish); 1 external calls (__init__).


##### `TurnEngine._carried_artifacts`  (lines 2547–2554)

```
def _carried_artifacts(self, answer: str) -> tuple[tuple[MarkedArtifact, ...], str]
```

**Purpose**: Extracts workspace file links from a final answer for top-level member turns and reduces those links to their labels in delivered text.

**Data flow**: It receives answer text, returns no artifacts for child turns, otherwise parses Markdown links and returns the carried artifacts plus projected answer.

**Call relations**: TurnEngine.run calls it just before staging artifacts and committing the final answer.

*Call graph*: called by 1 (run); 1 external calls (marked_artifacts).


##### `TurnEngine._stage_carried_artifacts`  (lines 2556–2601)

```
async def _stage_carried_artifacts(self, carried: tuple[MarkedArtifact, ...]) -> tuple[_CarriedFile, ...]
```

**Purpose**: Copies files referenced in the final answer into blob storage before terminal commit. This lets the commit add durable artifact rows that delivery can immediately read.

**Data flow**: It receives parsed artifact marks, checks each workspace path and size, stores file bytes under a deterministic blob key, logs and skips failures, and returns staged file records.

**Call relations**: TurnEngine.run calls it after _carried_artifacts; _commit_once later writes rows for the staged files.

*Call graph*: called by 1 (run); 6 external calls (__init__, warn, workspace_path, measure_file, store_artifact, uuid5).


##### `TurnEngine._discard_unreferenced`  (lines 2603–2632)

```
async def _discard_unreferenced(self, connection: AsyncConnection, staged: tuple[_CarriedFile, ...]) -> None
```

**Purpose**: Deletes staged artifact blobs that did not get a database row. This avoids orphaned blobs when a commit is refused or loses a race.

**Data flow**: It receives a database connection and staged file records, reads which blob keys are referenced by shared_artifact rows, and deletes unreferenced blobs best-effort.

**Call relations**: TurnEngine._commit_once calls it when a commit yields or discovers another terminal already exists.

*Call graph*: called by 1 (_commit_once); 3 external calls (execute, select, log).


##### `TurnEngine._render_arrival`  (lines 2634–2658)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Turns one queued inbound message into the exact text the model should see. It applies the same prompt hook behavior used for the founding message.

**Data flow**: It receives message id, body, context, speaker id, and creation time; fires the user_prompt_submit hook; returns either rendered content with context/injection or safe denial text.

**Call relations**: The durable _claim_arrivals step calls it for each row it claims.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._owed_arrivals`  (lines 2660–2682)

```
def _owed_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[sa.ColumnElement[bool], ...]
```

**Purpose**: Builds the database filter for inbound messages this turn still needs to drain. It is shared by claiming and commit guarding so they agree.

**Data flow**: It receives already absorbed ids and returns SQL conditions for pending or stamped-but-not-recorded messages, with special rules for spawned turns.

**Call relations**: _claim_arrivals uses it to drain rows; _commit_once uses it to refuse a terminal while unseen arrivals remain.

*Call graph*: called by 2 (_claim_arrivals, _commit_once); 2 external calls (and_, or_).


##### `TurnEngine._claim_arrivals`  (lines 2685–2738)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably claims and renders pending inbound messages for this turn. Because it is a DBOS step, replay gets the same batch and hook output instead of consuming messages twice.

**Data flow**: It receives absorbed ids, marks owed inbound rows as consumed by this turn, sorts them by sequence, renders each row, logs the claim, closes adoption replay, and returns Arrival records.

**Call relations**: TurnEngine._absorb_arrivals calls this before each model round.

*Call graph*: calls 2 internal fn (_owed_arrivals, _render_arrival); called by 1 (_absorb_arrivals); 5 external calls (__init__, model_validate, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 2740–2759)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns claimed-but-not-absorbed arrivals to pending when a turn exits by failure or cancellation. This prevents messages from being stranded.

**Data flow**: It receives absorbed ids, clears consumed_turn_id for this turn's unabsorbed rows, and logs any best-effort failure.

**Call relations**: TurnEngine.run calls it in failed, cancelled, and parked-error paths.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._stream_recovering_overflow`  (lines 2761–2833)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, tool_schemas: tuple[ToolSchema, ...], tool_choice: str | None, round_index: int, offe
```

**Purpose**: Runs one model stream and recovers once from context overflow by forcing compaction. Context overflow means the prompt is still too large for the provider.

**Data flow**: It receives messages, usage, system prompt, tool offer, round settings, and active requests; runs the stream retry path; turns recorded model errors into ModelStreamError; on overflow compacts, records compaction usage, and tries again.

**Call relations**: _RuntimeModel.stream calls it for every model round.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_retrying_interruption); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._stream_retrying_interruption`  (lines 2835–2874)

```
async def _stream_retrying_interruption(self, round_input: _RoundInput, usage_events: list[Usage]) -> StreamResult
```

**Purpose**: Retries a model round once when the provider stream is interrupted mid-round by a transient fault. It still records usage from the interrupted attempt.

**Data flow**: It receives round input and usage events, calls _stream_once, extends usage, may move accounts after rate limits, parks for provider retry-after, retries one interruption, and returns the final StreamResult.

**Call relations**: _stream_recovering_overflow uses it before and after forced compaction.

*Call graph*: calls 3 internal fn (_move_account, _stream_once, __init__); called by 1 (_stream_recovering_overflow); 4 external calls (now, timedelta, emit_metric, log).


##### `TurnEngine._move_account`  (lines 2876–2891)

```
async def _move_account(self, usage_events: list[Usage]) -> bool
```

**Purpose**: Moves a turn to the member's next available model account after rate limiting. This lets the same round retry on another account while preserving billing splits.

**Data flow**: It records the model being left, asks the serving model to move, appends a burn split if successful, logs the move, and returns whether it moved.

**Call relations**: _stream_retrying_interruption calls it when a stream result reports account rate limiting.

*Call graph*: called by 1 (_stream_retrying_interruption); 1 external calls (log).


##### `TurnEngine._priced`  (lines 2893–2899)

```
def _priced(self, usage_events: Sequence[Usage]) -> int
```

**Purpose**: Calculates the current attempt's cost in micro-dollars using the right model price for each burn segment.

**Data flow**: It receives usage events, splits them through the burn tracker, prices each segment, and returns the sum.

**Call relations**: _enforce_spend and _publish_cost use it for cap checks and live cost ticks.

*Call graph*: called by 2 (_enforce_spend, _publish_cost).


##### `TurnEngine._enforce_spend`  (lines 2901–2941)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks seats, workspace balance, and spending caps before another model round. If the turn has spent too much or lacks balance, it parks instead of losing work.

**Data flow**: It receives current usage and requesters, enforces seats, prices pending spend, checks balance and caps through database-backed evaluators, and raises TurnParked when the turn must wait.

**Call relations**: _RuntimeConversation.prepare and prepare_exhaust call it before model work continues.

*Call graph*: calls 3 internal fn (_enforce_seats, _priced, __init__); 6 external calls (__init__, __init__, workspace_tx, applicable_caps_absent, balance_absent, audience_member).


##### `TurnEngine._enforce_seats`  (lines 2943–2952)

```
async def _enforce_seats(self, requesters: Mapping[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks that all members whose authority is active in the turn still have seats. If not, the turn parks.

**Data flow**: It collects member ids from active requesters and on-behalf-of authority, queries seat state, and raises TurnParked if any are no longer seated.

**Call relations**: _enforce_spend and run_intent call it before spending or dispatching work.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_enforce_spend, run_intent); 2 external calls (__init__, workspace_tx).


##### `TurnEngine._enforce_authority_seat`  (lines 2954–2960)

```
async def _enforce_authority_seat(self, authority: ExecutionAuthority) -> None
```

**Purpose**: Checks the seat for the specific member authority attached to a tool call.

**Data flow**: It extracts the member id from an execution authority, returns immediately for non-member authority, otherwise checks seats and raises TurnParked on revocation.

**Call relations**: _invoke_dispatch calls it just before running a tool handler.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_invoke_dispatch); 3 external calls (__init__, workspace_tx, authority_member_id).


##### `TurnEngine._stream_once`  (lines 2963–3136)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Runs one durable model round and streams live text to listeners. As a DBOS step, replay returns the recorded model output instead of calling the provider again.

**Data flow**: It receives round input, builds tool schemas and a model request, tracks cache and latency metrics, uses ModelRoundRunner to stream text, calls, reasoning, and usage, emits metrics, and returns a StreamResult containing output or recorded error details.

**Call relations**: _stream_retrying_interruption is its caller and handles retries, rate limits, and parking based on the returned result.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_stream_retrying_interruption); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, now, emit_histogram, emit_metric, emit_up_down_metric (+2 more)).


##### `TurnEngine._publish_cost`  (lines 3138–3151)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn's live cost and token total after a model round.

**Data flow**: It totals usage events, calculates total tokens and priced cost, and publishes a CostTick frame.

**Call relations**: _RuntimeModel.stream calls it after successful model streaming.

*Call graph*: calls 3 internal fn (_priced, _publish, _total_usage); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 3153–3164)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the loaded-skill tracker from the current message window. This keeps skill loading accurate after compaction or arrival absorption.

**Data flow**: It receives messages, derives loaded skill closures, includes preloaded skills, and reseeds the compaction tracker.

**Call relations**: Runtime conversation preparation and overflow recovery call it after message windows change.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 1 (_stream_recovering_overflow).


##### `TurnEngine._resolve_call`  (lines 3166–3180)

```
def _resolve_call(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Turns a model-written tool call into either an executable resolved call or a rejected-call object. This makes later stages use one stable identity.

**Data flow**: It receives a ToolUseBlock, routes object_action calls to _resolve_action, looks up normal tools in the registry, and returns EffectiveCall or _RejectedToolCall.

**Call relations**: _RuntimeTools._resolve and run_intent use it before binding and dispatch.

*Call graph*: calls 2 internal fn (_rejected, _resolve_action); called by 1 (run_intent); 1 external calls (__init__).


##### `TurnEngine._resolve_action`  (lines 3182–3256)

```
def _resolve_action(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Resolves a generic object_action call into the specific bound object action it names. It validates kind, action, binding shape, grant, and action input.

**Data flow**: It parses ObjectActionInput, looks up the action binding, checks instance versus collection rules and grants, validates the action's own input model, and returns EffectiveCall or a rejected call with bounded metric labels.

**Call relations**: _resolve_call delegates object_action calls here.

*Call graph*: calls 1 internal fn (_rejected); called by 1 (_resolve_call); 3 external calls (__init__, model_validate, replace).


##### `TurnEngine._rejected`  (lines 3258–3273)

```
def _rejected(self, call: ToolUseBlock, error: Exception, dimensions: Mapping[str, str] | None=None, member_refs: tuple[UUID, ...]=(), input_model: type[BaseModel] | None=None, member_bound: bool=Fals
```

**Purpose**: Builds a standardized rejected tool call from an exception. This lets invalid calls occupy the same dispatch slot as real calls and give the model useful feedback.

**Data flow**: It receives the call, error, optional metric dimensions, member refs, input model, and binding flag; formats error text and returns a _RejectedToolCall.

**Call relations**: Tool resolution, action resolution, binding, and intent admission use it whenever a call should fail before handler execution.

*Call graph*: calls 1 internal fn (_error_text); called by 4 (_bind_or_error, _resolve_action, _resolve_call, run_intent); 1 external calls (__init__).


##### `TurnEngine._bind_or_error`  (lines 3275–3321)

```
async def _bind_or_error(self, context: ToolContext, item: _Resolution, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Binds a resolved call to the right requester authority and tool context, or converts binding problems into rejected calls.

**Data flow**: It receives ToolContext, a resolution, and active requesters; if already rejected it returns it; otherwise it binds requester authority, builds _BoundToolCall, meters binding failures, and returns rejection on recoverable binding errors.

**Call relations**: _RuntimeTools.prepare and run_intent call it before dispatch.

*Call graph*: calls 5 internal fn (_bind_requester, _member_refs, _own_member, _rejected, _meter_dispatch); called by 1 (run_intent); 5 external calls (__init__, __init__, meter_dimensions, replace, monotonic).


##### `TurnEngine._dispatch`  (lines 3323–3329)

```
async def _dispatch(self, bound: _DispatchInput, usage_events: list[Usage] | None=None) -> ToolResultBlock
```

**Purpose**: Runs a bound or rejected tool dispatch and returns the model-readable tool result block.

**Data flow**: It receives a dispatch input and optional usage accumulator, runs the durable dispatch-step recovery loop, then converts the DispatchResult into a ToolResultBlock.

**Call relations**: _RuntimeTools.execute calls this for normal model tool calls.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step_recovering).


##### `TurnEngine._dispatch_result`  (lines 3331–3363)

```
async def _dispatch_result(self, result: DispatchResult) -> ToolResultBlock
```

**Purpose**: Rebuilds a runtime ToolResultBlock from a DispatchResult, including rehydrating any offloaded images from blob storage.

**Data flow**: It receives a DispatchResult, returns a simple text result when no images exist, otherwise loads image blobs and builds multipart text/image content, then records the result as completed activity.

**Call relations**: _dispatch calls it after _dispatch_step_recovering.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._dispatch_step_recovering`  (lines 3365–3375)

```
async def _dispatch_step_recovering(self, bound: _DispatchInput, usage_events: list[Usage] | None) -> DispatchResult
```

**Purpose**: Repeats dispatch steps when a tool dispatch was interrupted and needs to resume with a saved target.

**Data flow**: It receives a dispatch input and usage accumulator, calls _dispatch_step with any resume target, checks whether the result is accepted, and loops until a non-interrupted result is available.

**Call relations**: _dispatch and run_intent use it to execute tools through the DBOS step boundary.

*Call graph*: calls 2 internal fn (_accept_dispatch_result, _dispatch_step); called by 2 (_dispatch, run_intent).


##### `TurnEngine._accept_dispatch_result`  (lines 3377–3388)

```
def _accept_dispatch_result(self, result: DispatchResult, usage_events: list[Usage] | None) -> bool
```

**Purpose**: Decides whether a dispatch-step result is final, and accounts for usage from replayed or live dispatches correctly.

**Data flow**: It receives a DispatchResult and optional usage accumulator, checks whether this step ran live, adds usage only when appropriate, converts live interrupted dispatches into cancellation, and returns whether to stop retrying.

**Call relations**: _dispatch_step_recovering calls it after each dispatch step.

*Call graph*: called by 1 (_dispatch_step_recovering).


##### `TurnEngine._bind_requester`  (lines 3390–3448)

```
async def _bind_requester(self, context: ToolContext, item: EffectiveCall, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Chooses the member authority a tool call acts for. It uses requested_by message refs when needed, or the owning member conversation when that is unambiguous.

**Data flow**: It receives context, effective call, and active requesters; removes requested_by from tool input, validates message refs, derives authority, swaps sandbox/subagent controls for that authority, and returns updated context plus cleaned call.

**Call relations**: _bind_or_error calls it for every executable resolved call.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error); 5 external calls (__init__, __init__, replace, authority_from_member_id, UUID).


##### `TurnEngine._own_member`  (lines 3450–3458)

```
def _own_member(self, requesters: Mapping[UUID, ActiveMessage]) -> UUID | None
```

**Purpose**: Determines whether this turn is in a single member's own conversation and that member has an active message. In that case tools can bind to the member without requested_by.

**Data flow**: It reads the audience member and active requester member ids, returning the member id only when the active messages include that same member.

**Call relations**: _bind_requester, _member_refs, and _bind_or_error use it to decide requester binding and hints.

*Call graph*: called by 3 (_bind_or_error, _bind_requester, _member_refs); 1 external calls (audience_member).


##### `TurnEngine._member_refs`  (lines 3460–3466)

```
def _member_refs(self, requesters: Mapping[UUID, ActiveMessage]) -> tuple[UUID, ...]
```

**Purpose**: Lists active member message references the model may use in requested_by. It hides refs in a member's own conversation where they add no information.

**Data flow**: It receives requesters, returns an empty tuple for own-member conversations, otherwise returns ids for active requester messages that have member ids.

**Call relations**: _RuntimeModel.stream, _RuntimeTools.definitions, and _bind_or_error use this to shape schemas and error hints.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error).


##### `TurnEngine._offload`  (lines 3468–3493)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large tool text or salvaged partial model output into the sandbox runtime output directory and returns a display path.

**Data flow**: It receives a filename and content, ensures the tool-output directory exists, writes bytes into the sandbox, logs failures, and returns a runtime display path or null.

**Call relations**: _finish_dispatch uses it for oversized tool output; _RuntimeModel.stream uses it indirectly for truncation salvage.

*Call graph*: called by 1 (_finish_dispatch); 2 external calls (emit_metric, log).


##### `TurnEngine._start_activity`  (lines 3495–3500)

```
def _start_activity(self, call: ToolUseBlock, goal: str) -> None
```

**Purpose**: Starts a background task that summarizes what a tool call is doing for live activity display.

**Data flow**: It increments an activity sequence number, creates an async summarization task, stores it, and arranges removal when done.

**Call relations**: _RuntimeTools.prepare and run_intent call it for bound calls before dispatch.

*Call graph*: calls 1 internal fn (_generate_activity); called by 1 (run_intent); 1 external calls (create_task).


##### `TurnEngine._generate_activity`  (lines 3502–3514)

```
async def _generate_activity(self, call: ToolUseBlock, goal: str, sequence: int) -> None
```

**Purpose**: Creates and publishes an activity label for a tool call in model order.

**Data flow**: It receives a call, goal, and sequence number; asks the summarizer for text; stores labels; waits under a lock until earlier activities are ready; and publishes Activity and subagent run frames.

**Call relations**: _start_activity launches it as a background task.

*Call graph*: calls 2 internal fn (_publish, _publish_run); called by 1 (_start_activity); 1 external calls (__init__).


##### `TurnEngine._stop_activity`  (lines 3516–3518)

```
def _stop_activity(self) -> None
```

**Purpose**: Cancels outstanding activity-summary tasks when the turn is terminal.

**Data flow**: It loops over tracked tasks and cancels each one.

**Call relations**: _publish_terminal calls it after publishing the terminal frame.

*Call graph*: called by 1 (_publish_terminal).


##### `TurnEngine._dispatch_step`  (lines 3521–3642)

```
async def _dispatch_step(self, bound: _DispatchInput, resume_target: ObjectActionTarget | None=None) -> DispatchResult
```

**Purpose**: Runs one tool dispatch as a durable DBOS step. It covers rejected calls, validation, hooks, handler invocation, output shaping, metrics, and interruption checkpoints.

**Data flow**: It receives a bound or rejected dispatch input and optional resume target; marks the call live; prepares dispatch, invokes handler, finishes output, or records interruption usage; meters and logs outcomes; and returns DispatchResult unless an engine-level failure escapes.

**Call relations**: _dispatch_step_recovering calls it, and replay uses DBOS records to avoid re-running completed handlers.

*Call graph*: calls 4 internal fn (_finish_dispatch, _invoke_dispatch, _prepare_dispatch, _meter_dispatch); called by 1 (_dispatch_step_recovering); 4 external calls (__init__, monotonic, span, warn).


##### `TurnEngine._prepare_dispatch`  (lines 3644–3748)

```
async def _prepare_dispatch(self, bound: _BoundToolCall, target: ObjectActionTarget | None) -> _DispatchGate
```

**Purpose**: Validates a bound call and runs pre-tool hooks before the handler executes. It can also preempt replayed read-only calls if new member guidance is waiting.

**Data flow**: It receives a bound call and optional target, checks replay preemption, validates input, resolves object action target, fires pre_tool_use, and returns either a ready dispatch gate or an error DispatchResult.

**Call relations**: _dispatch_step calls it as the first phase of executable dispatch.

*Call graph*: calls 3 internal fn (_pending_member_guidance, _redoes_on_replay, _error_text); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, log).


##### `TurnEngine._invoke_dispatch`  (lines 3750–3809)

```
async def _invoke_dispatch(self, bound: _BoundToolCall, ready: _DispatchReady, find_usages: list[Usage]) -> _HandlerOutput
```

**Purpose**: Calls the actual tool handler with the right context, authority, idempotency key, and find-usage tracking.

**Data flow**: It receives the bound call, prepared dispatch data, and a find-usage list; enforces the authority seat, builds handler context, runs the handler, collects text and images, and returns handler output or an error output. Sandbox provider outages may become TurnParked.

**Call relations**: _dispatch_step calls it after _prepare_dispatch succeeds.

*Call graph*: calls 3 internal fn (_enforce_authority_seat, _error_text, sandbox_provider_park); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, replace).


##### `TurnEngine._finish_dispatch`  (lines 3811–3877)

```
async def _finish_dispatch(self, ready: _DispatchReady, handled: _HandlerOutput, find_usages: list[Usage]) -> DispatchResult
```

**Purpose**: Turns handler output into a durable DispatchResult. It clips or offloads large text, walls untrusted text, fires post hooks, downsizes and stores images, and records tool-side model usage.

**Data flow**: It receives prepared dispatch data, handler output, and find usage; shapes text based on error/size/trust, fires success or failure hooks, stores successful images in blobs, and returns DispatchResult.

**Call relations**: _dispatch_step calls it after handler invocation.

*Call graph*: calls 3 internal fn (_bounded_image, _offload, _bounded); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, wall).


##### `TurnEngine._redoes_on_replay`  (lines 3879–3886)

```
def _redoes_on_replay(self, tool: ToolDef) -> bool
```

**Purpose**: Says whether re-running a tool would redo work and can therefore be preempted by new member guidance. Side-effecting tools are not preempted because they rely on idempotency keys to reconnect safely.

**Data flow**: It reads the tool's side-effecting flag and returns true for non-side-effecting tools.

**Call relations**: _prepare_dispatch uses it during crash-adoption replay before dispatching a call.

*Call graph*: called by 1 (_prepare_dispatch).


##### `TurnEngine._pending_member_guidance`  (lines 3888–3904)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether a member message is waiting in the conversation queue. This can stop a replayed read-only tool from running ahead of new guidance.

**Data flow**: It queries inbound_message for one pending member row in the conversation and returns whether one exists.

**Call relations**: _prepare_dispatch calls it only inside live dispatch execution during adoption replay.

*Call graph*: called by 1 (_prepare_dispatch); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 3906–3934)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Downscales large tool-result images so providers will accept them and prompt logs stay reasonable.

**Data flow**: It receives an ImageBlock, decodes base64 image bytes, opens and thumbnails oversized images, saves them in an appropriate format, re-encodes to base64, and returns the bounded image. On image errors it logs and returns the original.

**Call relations**: _finish_dispatch calls it before storing tool-result image blobs.

*Call graph*: called by 1 (_finish_dispatch); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 3936–4010)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Retries terminal commit until it is durable, then records terminal metrics and logs. It may yield instead of committing if unseen arrivals exist.

**Data flow**: It receives terminal status, usage, meter, answer/error/acts/artifacts, repeatedly calls _commit_once with exponential backoff on database errors, emits terminal metrics if it wrote the row, records execution exit, logs the result, and returns the frame or null.

**Call relations**: TurnEngine.run, run_intent, and denied-prompt preparation call it whenever a turn needs a final terminal frame.

*Call graph*: calls 2 internal fn (_commit_once, exited); called by 3 (_prepare_run, run, run_intent); 4 external calls (sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._publish_terminal`  (lines 4012–4015)

```
async def _publish_terminal(self, frame: TerminalFrame) -> None
```

**Purpose**: Publishes the terminal frame to live listeners and mirrors subagent status to the root turn.

**Data flow**: It receives a TerminalFrame, publishes it, publishes run status if this is a subagent, and stops pending activity summaries.

**Call relations**: Normal, intent, and early-denial paths call it after committing a terminal frame.

*Call graph*: calls 3 internal fn (_publish, _publish_run, _stop_activity); called by 3 (_prepare_run, run, run_intent); 1 external calls (__init__).


##### `TurnEngine._record_workspace_changes`  (lines 4017–4030)

```
async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None
```

**Purpose**: Refreshes the workspace changes view after a turn finishes. It scans only after the user is no longer waiting on the terminal and transcript.

**Data flow**: It receives changed targets, builds a WorkspaceChangeRecorder for the sandbox's owning conversation, and runs the record operation.

**Call relations**: TurnEngine.run and early-denial preparation call it after terminal publication.

*Call graph*: called by 2 (_prepare_run, run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 4032–4165)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to write billing, terminal state, and carried artifact rows. It is the atomic terminal write.

**Data flow**: It may lock the conversation and refuse if owed arrivals remain, records usage, reads billed cost, builds a TerminalFrame, updates the turn if still non-terminal, inserts shared artifact rows, and returns the frame plus whether this call committed it.

**Call relations**: _commit retries this method until a durable outcome is reached.

*Call graph*: calls 3 internal fn (_discard_unreferenced, _owed_arrivals, _record_usage); called by 1 (_commit); 12 external calls (__init__, model_validate, now, timedelta, select, update, workspace_tx, log, log_error, read_turn_cost (+2 more)).


##### `TurnEngine._park`  (lines 4167–4210)

```
async def _park(self, message: str, usage_events: list[Usage], retry_at: datetime | None=None, absorbed: tuple[UUID, ...]=(), external_retry_count: int | None=None) -> None
```

**Purpose**: Marks the turn as parked and resumable, while billing tokens already spent and releasing arrivals not absorbed. Parking is a non-terminal wait, not a failure.

**Data flow**: It receives message, usage, retry time, absorbed ids, and retry count; updates the turn row to parked, records usage, releases unabsorbed arrivals, then publishes a Parked frame and metrics if the update succeeded.

**Call relations**: TurnEngine.run and run_intent call it after catching TurnParked.

*Call graph*: calls 2 internal fn (_publish, _record_usage); called by 2 (run, run_intent); 5 external calls (__init__, update, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 4212–4221)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes a live frame without letting publish failure fail the turn. Durable database state remains the source of truth.

**Data flow**: It receives a live frame, attempts hub publication, logs any exception, and returns nothing.

**Call relations**: Arrival, reply, cost, terminal, activity, resumed, and parked paths all use this safe live-publish helper.

*Call graph*: called by 8 (_absorb_arrivals, _generate_activity, _park, _publish_cost, _publish_terminal, _speak, _stream_closing_spans, run); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 4223–4245)

```
async def _publish_run(self, activity: str='', status: str='') -> None
```

**Purpose**: Mirrors subagent activity or status onto the root turn stream. Main turns do nothing here.

**Data flow**: It receives optional activity and status, checks lineage, builds a SubagentActivity frame, publishes it to the root turn hub stream, and logs publish failures.

**Call relations**: Run start, activity generation, and terminal publication call it for subagent visibility.

*Call graph*: called by 3 (_generate_activity, _publish_terminal, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 4247–4263)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops sandbox commands only for deliberate workflow cancellation. It avoids stopping commands during ordinary executor preemption, because preempted work may resume.

**Data flow**: It asks the sandbox to stop commands and logs any failure without changing the already-durable cancellation result.

**Call relations**: TurnEngine.run and run_intent call it when DBOS reports workflow cancellation.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._record_usage`  (lines 4265–4280)

```
async def _record_usage(self, connection: AsyncConnection, usage_events: Sequence[Usage]) -> None
```

**Purpose**: Writes billing records for this attempt's model usage.

**Data flow**: It receives a database connection and usage events, splits usage by burn segment, and records each segment with model, attempt series, pricing, and BYOK status.

**Call relations**: _commit_once, _park, and _bill_cancelled call it to make consumed tokens count.

*Call graph*: called by 3 (_bill_cancelled, _commit_once, _park); 1 external calls (record_turn_usage).


##### `TurnEngine._bill_cancelled`  (lines 4282–4292)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for a cancelled or preempted normal turn. Cancellation should not hang on billing, but usage should still be counted when possible.

**Data flow**: It receives usage events, opens a transaction, calls _record_usage, and logs failures without raising.

**Call relations**: TurnEngine.run calls it in cancellation and preemption paths.

*Call graph*: calls 1 internal fn (_record_usage); called by 1 (run); 2 external calls (workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 4294–4299)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles an execution that could not claim the turn. It either republishes an existing terminal or does nothing while another execution owns the work.

**Data flow**: It creates a transcript repair helper and asks it to resolve the unclaimed turn, returning a terminal frame or null.

**Call relations**: TurnEngine.run and run_intent call it when _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 4301–4304)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes a completed transcript after adding activity labels to tool results.

**Data flow**: It receives messages, answer, system, and injected context; labels messages through _labeled; and delegates to TranscriptRepair.persist_transcript.

**Call relations**: Normal completion, intent completion, and denied-prompt completion call it after commit.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 3 (_prepare_run, run, run_intent).


##### `TurnEngine._persist_interrupted`  (lines 4306–4310)

```
async def _persist_interrupted(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes safe transcript state for an interrupted, failed, or cancelled turn.

**Data flow**: It receives the current message window; if messages exist, labels and persists them as interrupted, otherwise persists only the inbound or founding denial notice.

**Call relations**: TurnEngine.run calls it in failure, cancellation, and non-done terminal paths.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 1 (run).


##### `TurnEngine._persist_parked`  (lines 4312–4322)

```
async def _persist_parked(self, messages: tuple[Message, ...], absorbed: tuple[UUID, ...], requesters: Mapping[UUID, ActiveMessage], system: str, injected: str) -> bool
```

**Purpose**: Writes the resumable transcript state for a parked turn after adding activity labels.

**Data flow**: It receives messages, absorbed ids, requesters, system, and injected context; labels messages; and delegates to TranscriptRepair._persist_parked.

**Call relations**: TurnEngine.run calls it before parking the turn row.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 1 (run).


##### `TurnEngine._labeled`  (lines 4324–4349)

```
def _labeled(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Adds the live activity text shown for each completed tool result into the stored transcript. This makes the transcript read like the user-facing stream did.

**Data flow**: It receives messages, walks block content, and for activity tool results that completed, copies in the matching activity_text label.

**Call relations**: Transcript persistence wrappers call it before writing completed, interrupted, or parked conversations.

*Call graph*: called by 3 (_persist_interrupted, _persist_parked, _persist_transcript).


### `core/src/ufo/runtime/ext/hooks.py`

`domain_logic` · `during turn handling, whenever a hookable event fires`

Extensions can react to events in the system, a bit like plugins that get called when something important happens. This file is the small dispatcher for those reactions during a turn. It keeps hooks grouped by event, runs them in a fixed order, and folds their answers into one final result the rest of the runtime can apply.

The main idea is safety plus predictability. Some events are “gating” events, meaning they happen before an action that may need permission, such as using a tool or accepting a user prompt. If a required hook fails during one of those events, this file refuses the action rather than silently continuing. That is called “failing closed”: like a security door staying locked if the badge reader breaks. Other events are more observational, so hook failures are logged and ignored so they do not break the whole turn.

Hooks can return only certain kinds of results depending on the event. For example, a pre-tool hook may deny the call or modify the tool input, while a post-tool hook may modify the output or inject extra context. The file checks those rules. It also applies changes in order, so each hook sees the result of the previous hook’s change. This makes extension behavior deterministic and easier to reason about.

#### Function details

##### `HookChain.__post_init__`  (lines 90–93)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that every hook in the chain belongs to the same audience as the chain itself. An audience is the group or visibility context the hook is meant to operate within, so mixing audiences could leak information or apply a hook in the wrong place.

**Data flow**: It starts with the hook groups already stored on the HookChain. It flattens those groups into one list of bound hooks, checks each hook’s extension audience against the chain’s audience, and either finishes quietly or raises an error if any one does not match. Nothing is returned; the chain is either valid after construction or construction fails.

**Call relations**: This runs automatically after a HookChain is created. It protects the later HookChain.fire flow by making sure the hooks it will call are all meant for the same audience.


##### `HookChain.fire`  (lines 95–173)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: This runs all hooks registered for one event and combines their results into a single HookResolution. It is used when the runtime reaches a point where extensions are allowed to react, block something, change input or output, or add context.

**Data flow**: It receives the event name, the event payload, optional turn and agent records, and the speaker member ID. It looks up hooks for that event, skips tool-specific hooks when the current tool does not match, builds a HookContext for each hook, and gives the hook a short time limit to answer. As hooks return, it folds their results from left to right: a Deny stops everything and returns a refusal, ModifyInput updates the tool input seen by later hooks, ModifyOutput updates the tool output, and InjectContext text is collected in order. At the end it returns a HookResolution containing the final denial, modified values, injected text, or failure information.

**Call relations**: The turn engine calls this when a hookable event occurs. Inside, it creates HookContext objects to give each extension the information it needs, uses asyncio.timeout to stop slow hooks from hanging the turn, uses dataclasses.replace to pass updated payloads to later hooks, and raises HookOutcomeNotAllowed when a hook returns a result that is not valid for that event. If a required gating hook fails, it returns a denying HookResolution; otherwise it logs the problem and continues to the next hook.

*Call graph*: 5 external calls (__init__, __init__, __init__, timeout, replace).


### Agent model loop
The harness manages model conversation rounds, parses reply metadata, streams events, gathers tool calls, and produces the final agent result.

### `core/src/ufo/harness/agent.py`

`orchestration` · `main loop`

This file is the agent harness, meaning the frame that runs one complete back-and-forth with an AI model. Without it, the project would have pieces like models and tools, but no common way to make them work together safely and predictably.

It first defines the shapes of messages: plain text, images, tool calls, tool results, and reasoning notes. It also defines tool descriptions and agent settings, with small checks so invalid settings fail early. The protocol classes describe replaceable boundaries: an AgentModel knows how to ask the model for one round, AgentTools knows how to list and run tools, AgentConversation can save or prepare the transcript, and AgentEvents can report replies to observers.

AgentEngine is the main machine. Each round, it prepares the transcript, asks the model for a response, separates visible replies from internal markers, and then chooses what to do next. If the model asks for tools, the engine runs them in safe batches and feeds the results back as another user message. If the model gives text, the engine closes the run. If the run uses structured output, the engine can require a special finish tool, like asking someone to fill out a form instead of giving a free-form answer. If the round limit is reached, it asks for one final answer without normal tool use.

#### Function details

##### `ToolDefinition.__post_init__`  (lines 61–63)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that every tool has a real name. This prevents the agent from offering the model a nameless tool, which would make tool calls impossible to match reliably.

**Data flow**: After a ToolDefinition is created, it reads the name field. If the name is blank or only spaces, it stops creation with a ValueError; otherwise the tool definition remains unchanged.

**Call relations**: This validation runs automatically when code creates a ToolDefinition. Later, AgentEngine._stream collects tool definitions and sends them to the model, so this early check keeps that list usable.


##### `AgentDefinition.__post_init__`  (lines 74–78)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the agent's round limit and parallel tool-call limit are both positive numbers. This avoids impossible runs, such as an agent allowed zero rounds or zero concurrent tool calls.

**Data flow**: After an AgentDefinition is created, it reads max_rounds and max_parallel_calls. If either is less than one, it raises a ValueError; otherwise the definition is kept as written.

**Call relations**: This runs when an AgentDefinition is built. AgentEngine.run depends on max_rounds to know how long to keep trying, and AgentEngine._tool_exchange depends on max_parallel_calls when scheduling tool calls.


##### `RecoverableModelError.__init__`  (lines 126–128)

```
def __init__(self, feedback: str) -> None
```

**Purpose**: Creates an error that means a model round failed, but the agent can try again after giving the model feedback. It carries the feedback text that should be added to the conversation.

**Data flow**: It receives a feedback string, stores that string both as the normal exception message and as the feedback attribute. The result is an exception object the engine can catch and turn into a new user message.

**Call relations**: core/src/ufo/runtime/engine._RuntimeModel.stream raises or creates this when a model problem can be retried. AgentEngine.run catches it, appends its feedback to the messages, and starts the next round instead of ending the whole run.

*Call graph*: called by 1 (stream).


##### `AgentModel.stream`  (lines 134–134)

```
async def stream(self, request: ModelRequest, round_index: int) -> ModelRound
```

**Purpose**: Defines the contract for asking the language model to produce one round of output. A real model adapter implements this method.

**Data flow**: It receives a complete ModelRequest plus the round number. An implementation should use that request to contact or simulate a model, then return a ModelRound containing new messages, text, tool calls, and any reasoning records.

**Call relations**: AgentEngine._stream builds the ModelRequest and calls this method. The returned ModelRound is then interpreted by AgentEngine.run, AgentEngine._tool_exchange, AgentEngine._close, or AgentEngine._exhaust depending on what the model produced.


##### `AgentTools.definitions`  (lines 142–142)

```
def definitions(self) -> tuple[ToolDefinition, ...]
```

**Purpose**: Defines how a tool provider tells the agent which tools are available. Each definition is the information the model sees before deciding whether to call a tool.

**Data flow**: It takes no input besides the tool provider itself. An implementation returns a tuple of ToolDefinition objects describing names, descriptions, and input formats.

**Call relations**: AgentEngine._stream calls this during a normal round. It checks for duplicate names, may add a structured finish tool, and then sends the final tool list to the model.


##### `AgentTools.parallel_safe`  (lines 144–144)

```
def parallel_safe(self, call: ToolCall) -> bool
```

**Purpose**: Tells the engine whether a particular tool call is safe to run at the same time as other tool calls. This protects tools that need to run alone, such as one that changes shared state.

**Data flow**: It receives a ToolCall and returns true or false. The answer is used as a scheduling hint, not as a tool result.

**Call relations**: AgentEngine._tool_exchange passes this method to dispatch_segments. That helper groups tool calls into batches so safe calls can run together while unsafe calls are separated.


##### `AgentTools.prepare`  (lines 146–146)

```
async def prepare(self, calls: tuple[ToolCall, ...]) -> None
```

**Purpose**: Gives the tool provider a chance to get ready before a batch of tool calls runs. This can be used for setup, validation, or reserving resources.

**Data flow**: It receives the next tuple of ToolCall objects about to run. It may perform side effects, such as opening resources or checking state, and returns no user-facing value.

**Call relations**: AgentEngine._tool_exchange calls this before executing each dispatch segment. If preparation succeeds, the engine then runs the calls in that segment.


##### `AgentTools.execute`  (lines 148–148)

```
async def execute(self, call: ToolCall) -> ToolResult
```

**Purpose**: Runs one tool call and produces the result that will be sent back to the model. This is where a requested action actually happens.

**Data flow**: It receives one ToolCall containing an id, tool name, and input data. An implementation performs the requested work and returns a ToolResult, or raises an error if execution fails.

**Call relations**: AgentEngine._tool_exchange calls this for every scheduled tool call, using asyncio.gather so calls in the same safe batch can run concurrently. The collected ToolResult objects are added to the transcript for the next model round.


##### `AgentTools.after_round`  (lines 150–152)

```
async def after_round(self, calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]) -> None
```

**Purpose**: Lets the tool provider clean up or record what happened after a model round that involved tools. It runs even if a tool execution fails.

**Data flow**: It receives all tool calls from the model and the results collected so far. It can update internal state or release resources, and returns no direct value.

**Call relations**: AgentEngine._tool_exchange calls this in a finally block. That means the cleanup step is part of the bigger flow whether the tool exchange succeeds or an exception interrupts it.


##### `AgentTools.after_checkpoint`  (lines 154–154)

```
async def after_checkpoint(self) -> None
```

**Purpose**: Notifies the tool provider that the updated conversation transcript has been safely checkpointed. This is useful when tool side effects should only be considered durable after the transcript is saved.

**Data flow**: It receives no extra input. It may update internal bookkeeping and returns nothing.

**Call relations**: AgentEngine.run calls this after a successful tool exchange has been written through AgentConversation.checkpoint. It marks the boundary between provisional tool work and saved conversation state.


##### `AgentTools.interrupted`  (lines 156–156)

```
def interrupted(self) -> None
```

**Purpose**: Tells the tool provider that a previously prepared conversation indicates an interrupted final action. This gives tools a chance to notice and react to an unfinished action.

**Data flow**: It receives no input and returns no value. An implementation may mark internal state, cancel work, or record the interruption.

**Call relations**: AgentEngine.run calls this when AgentConversation.prepare returns a PreparedRound with interrupted_final_act set. It happens before the next model round starts.


##### `AgentConversation.prepare`  (lines 162–162)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: Defines how the conversation transcript is prepared before each model round. This lets storage-backed or resumable conversations adjust the messages before the model sees them.

**Data flow**: It receives the current messages and the round index. It returns a PreparedRound containing the messages to use next and a flag saying whether a final action was interrupted.

**Call relations**: AgentEngine.run calls this at the start of every round. Its output becomes the exact transcript passed into AgentEngine._stream.


##### `AgentConversation.checkpoint`  (lines 164–164)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Defines how the updated transcript is saved after a tool exchange. A checkpoint is a safe save point, like marking your place before continuing.

**Data flow**: It receives the full message tuple after assistant tool calls and user tool results have been appended. It saves or records those messages and returns nothing.

**Call relations**: AgentEngine.run calls this after AgentEngine._tool_exchange returns more messages instead of a final answer. Once checkpointing completes, the engine calls AgentTools.after_checkpoint.


##### `AgentConversation.prepare_exhaust`  (lines 166–166)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Defines how the transcript should be prepared when the agent has used all normal rounds. This gives the conversation layer a final chance to adjust state before the forced closing step.

**Data flow**: It receives the current messages at exhaustion time. It returns the messages that should be used for the final forced-answer request.

**Call relations**: AgentEngine._exhaust calls this before adding the final prompt or structured finish prompt. The returned transcript becomes the basis for the last model request.


##### `AgentEvents.speak`  (lines 172–172)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Defines how visible replies from a non-final tool-using round are reported to observers. This can stream progress to a user interface without changing the agent's decisions.

**Data flow**: It receives marked replies and a human-friendly round number. An implementation may display or record them, then returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this before running tools. It lets observers see what the assistant said in the round where it also asked for tool work.


##### `AgentEvents.closing`  (lines 174–174)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Defines how final visible replies are reported when the agent is about to finish. This is the last observer notification for the answer text.

**Data flow**: It receives the marked replies extracted from the final model text. An implementation may display or store them, and returns nothing.

**Call relations**: AgentEngine._close calls this for ordinary final text, and AgentEngine._exhaust calls it for the final no-tools answer after the round budget is spent.


##### `AgentEvents.exhausted`  (lines 176–176)

```
def exhausted(self) -> None
```

**Purpose**: Defines how observers are told that the agent ran out of normal rounds. This can be used to show that the final answer is being forced rather than naturally reached.

**Data flow**: It receives no extra data. An implementation may update state or notify listeners, and returns nothing.

**Call relations**: AgentEngine._exhaust calls this at the start of the exhaustion path, before preparing the final forced answer.


##### `MemoryConversation.prepare`  (lines 195–196)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: Provides a simple in-memory version of conversation preparation. It leaves messages exactly as they are, which is useful for local runs and tests that do not need persistence.

**Data flow**: It receives the current messages and round index, ignores the round index, and wraps the same messages in a PreparedRound. Nothing is saved or changed.

**Call relations**: This is the default conversation boundary used by AgentEngine when no custom AgentConversation is supplied. It calls PreparedRound.__init__ to return the expected shape.

*Call graph*: 1 external calls (__init__).


##### `MemoryConversation.checkpoint`  (lines 198–199)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Provides a no-op checkpoint for runs that do not need transcript storage. It accepts the messages but deliberately saves nothing.

**Data flow**: It receives the messages that would normally be checkpointed. It returns None and makes no changes.

**Call relations**: AgentEngine.run can call this after tool results are added. Because this in-memory implementation does nothing, the engine simply continues to the next round.


##### `MemoryConversation.prepare_exhaust`  (lines 201–202)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Provides a simple exhaustion preparation step for in-memory runs. It returns the transcript unchanged before the final forced-answer step.

**Data flow**: It receives the current messages and returns the same messages. No extra markers or storage operations are added.

**Call relations**: AgentEngine._exhaust can call this through the AgentConversation boundary. With MemoryConversation, the exhaustion path proceeds using the existing transcript.


##### `NullEvents.speak`  (lines 209–210)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Provides a silent event observer for non-final replies. It is used when the run should not stream or record user-visible progress events.

**Data flow**: It receives marked replies and a round number, ignores both, and returns None. Nothing outside the function changes.

**Call relations**: This is the default AgentEvents implementation used by AgentEngine. When AgentEngine._tool_exchange reports spoken replies, this version quietly discards them.


##### `NullEvents.closing`  (lines 212–213)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Provides a silent event observer for final replies. It lets the engine use the same event flow even when nobody is listening.

**Data flow**: It receives the final marked replies, ignores them, and returns None. It has no side effects.

**Call relations**: AgentEngine._close and AgentEngine._exhaust may call this through the events boundary. With NullEvents, the final answer is still returned in Finished, just not separately announced.


##### `NullEvents.exhausted`  (lines 215–216)

```
def exhausted(self) -> None
```

**Purpose**: Provides a silent notification for round exhaustion. It keeps the event interface optional.

**Data flow**: It receives no input and returns None. It does not change any state.

**Call relations**: AgentEngine._exhaust calls this through the events boundary. With NullEvents, exhaustion affects the returned Finished object but produces no observer event.


##### `AgentEngine.run`  (lines 230–261)

```
async def run(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: Runs one complete agent session until it gets an answer, completes a structured output, or reaches the round limit. This is the main method a caller uses to make the agent do work.

**Data flow**: It starts with an incoming tuple of messages. For each round, it asks the conversation boundary to prepare them, streams a model response, strips out marked reply annotations, and then either runs tools, closes with text, nudges the model after one empty response, or eventually forces a final answer. It returns a Finished object containing the final transcript, answer text, and flags such as exhausted or structured.

**Call relations**: This is the top-level driver inside the file. It calls AgentEngine._stream for model output, AgentEngine._tool_exchange when tools are requested, AgentEngine._close when text can finish the run, and AgentEngine._exhaust when the round budget is gone; it also uses marked_replies and creates Message and ModelRound objects as it reshapes the transcript.

*Call graph*: calls 4 internal fn (_close, _exhaust, _stream, _tool_exchange); 3 external calls (__init__, __init__, marked_replies).


##### `AgentEngine._stream`  (lines 263–299)

```
async def _stream(self, messages: tuple[Message, ...], round_index: int, mode: RoundMode) -> ModelRound
```

**Purpose**: Builds the exact request sent to the model for one round. It decides which tools, if any, the model is allowed to use.

**Data flow**: It receives the current messages, the round index, and a mode. In normal mode it gathers tool definitions and optionally adds a structured finish tool; in no-tools mode it sends no tools; in force-finish mode it sends only the required finish tool and forces that choice. It then creates a ModelRequest, calls the model, and returns the ModelRound it receives.

**Call relations**: AgentEngine.run uses this for ordinary rounds, AgentEngine._force_finish uses it when a structured answer must be forced, and AgentEngine._exhaust uses it for the last no-tools or structured finish round. It is the narrow gate between the engine and AgentModel.stream.

*Call graph*: called by 3 (_exhaust, _force_finish, run); 1 external calls (__init__).


##### `AgentEngine._tool_exchange`  (lines 301–367)

```
async def _tool_exchange(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished | tuple[Message, ...]
```

**Purpose**: Carries out the back-and-forth that happens when the model asks to use tools. It reports any visible speech, runs tool calls in safe batches, and turns the results into messages for the next model round.

**Data flow**: It receives the model round, the visible marked replies from that round, and the round index. It first announces the replies, then checks whether a structured finish tool was called; a valid lone finish call immediately becomes a Finished result, while an invalid finish call becomes an error result for the model. For ordinary tools, it groups calls with dispatch_segments, prepares each group, executes calls concurrently inside the group, gathers ToolResult objects, runs after_round cleanup, and returns an expanded transcript containing the assistant's tool calls and the user's tool results.

**Call relations**: AgentEngine.run calls this whenever a streamed model round contains tool calls. This function uses dispatch_segments to choose execution batches, asyncio.gather to run calls in a batch, and creates Finished, Message, Text, and ToolResult objects to either finish the run or continue the conversation.

*Call graph*: called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, gather, dispatch_segments).


##### `AgentEngine._close`  (lines 369–386)

```
async def _close(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished
```

**Purpose**: Finishes the run when the model produced text instead of tool calls. If structured output is required, it may convert acceptable prose or ask the model for a formal finish call.

**Data flow**: It receives the model round, the marked visible replies, and the round index. If structured output is active and not blocked, it first asks the structured-output rules whether the prose can be accepted; if not, it appends a prompt asking for the finish format and calls _force_finish. Without that structured detour, it announces the closing replies and returns a Finished object with the model text.

**Call relations**: AgentEngine.run calls this when a model response has non-empty text and no tool calls. It may hand off to AgentEngine._force_finish, or it may directly create the final Finished result.

*Call graph*: calls 1 internal fn (_force_finish); called by 1 (run); 2 external calls (__init__, __init__).


##### `AgentEngine._force_finish`  (lines 388–397)

```
async def _force_finish(self, messages: tuple[Message, ...], round_index: int) -> Finished
```

**Purpose**: Forces a structured-output run to end through the special finish tool. This is used when free-form text is not enough and the answer must match an expected shape.

**Data flow**: It receives a transcript and round index. It streams one model round in force-finish mode, checks that the model returned exactly one call to the finish tool, validates that call, and returns a Finished object with the validated answer. If the model does not obey or validation fails, it raises a RuntimeError.

**Call relations**: AgentEngine._close calls this when a structured run needs to turn prose into the required finish format. AgentEngine._exhaust also calls it when the agent has run out of normal rounds but still needs a structured final answer. It uses AgentEngine._stream to make the forced model request.

*Call graph*: calls 1 internal fn (_stream); called by 2 (_close, _exhaust); 1 external calls (__init__).


##### `AgentEngine._exhaust`  (lines 399–416)

```
async def _exhaust(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: Produces a final answer after the agent has used all allowed normal rounds. It marks the result as exhausted so callers know the answer was forced by the budget.

**Data flow**: It starts with the current messages, notifies events that the run is exhausted, and asks the conversation boundary to prepare the transcript. For structured output, it adds the structured force-finish prompt and calls _force_finish. Otherwise, it adds the normal final prompt, streams one no-tools model round, extracts marked replies, announces the closing reply, and returns a Finished object with exhausted set to true.

**Call relations**: AgentEngine.run calls this after the main loop reaches max_rounds. It may hand off to AgentEngine._force_finish for structured output, or call AgentEngine._stream directly for a final no-tools response, using marked_replies before closing.

*Call graph*: calls 2 internal fn (_force_finish, _stream); called by 1 (run); 3 external calls (__init__, __init__, marked_replies).


### `core/src/ufo/harness/replies.py`

`domain_logic` · `request handling and live response streaming`

This file solves a presentation problem. The model can address an earlier message with a `<reply-to ...>` span or link a workspace file with Markdown. Reply markup and workspace paths should not appear to the user.

The file has two batch readers for completed text. `marked_replies` finds closed reply spans, extracts the spoken text inside them, links it to the message id if the id is valid, and returns the full text with the reply markup removed. `marked_artifacts` finds Markdown links to workspace files, records their paths, and reduces them to their labels in the delivered answer.

It also has `SpanRedaction`, which does the same kind of hiding while text is still streaming in chunks. This matters because a tag can be split across chunk boundaries, like receiving “<arti” now and “fact .../>” later. The redactor temporarily holds suspicious partial text until it knows whether it is a real tag or ordinary prose. Like a stagehand pulling cue cards out of view before the curtain opens, it lets normal words through but keeps control markup off every visible surface.

#### Function details

##### `marked_replies`  (lines 87–96)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: Finds completed `<reply-to message="...">...</reply-to>` sections in a finished model response. It returns the reply messages as structured data, and also returns the same text with the reply tags removed so the transcript stays readable.

**Data flow**: It receives one full text string. It scans for complete reply spans, strips any reply markup from the words inside each span, ignores empty replies, and tries to turn the named message into a real message id. It outputs a tuple of `MarkedReply` records plus a cleaned version of the original text with reply openers and closers removed.

**Call relations**: This is used after a round of text is complete, when the system wants to know whether the model addressed a specific earlier message. For each found span it asks `_named_message` to validate the message reference, then creates a `MarkedReply` record for the rest of the system to deliver or store.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `marked_artifacts`  (lines 99–117)

```
def marked_artifacts(text: str) -> tuple[tuple[MarkedArtifact, ...], str]
```

**Purpose**: Finds workspace file links in a finished closing answer and turns them into detail records. It reduces each link to its label while the file appears beside the reply.

**Data flow**: It receives one full text string. It scans for Markdown links to local paths, records each usable path with a safe display name, and outputs the artifact records plus the answer with those links reduced to their labels.

**Call relations**: This runs when a final answer is being prepared for display. It calls `_artifact_name` to decide the download/display name and `_link_text` to clean and limit the optional link text, then creates `MarkedArtifact` records that can travel alongside the cleaned reply.

*Call graph*: calls 2 internal fn (_artifact_name, _link_text); 1 external calls (__init__).


##### `_named_message`  (lines 120–124)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: Checks whether a tag’s `message` value is a valid UUID, which is a standard unique identifier. If it is not valid, the reply is still kept, but it is not linked to a specific message.

**Data flow**: It receives the raw message name from a reply tag. It trims surrounding whitespace and tries to parse it as a UUID. It returns the UUID when parsing succeeds, or `None` when the text is not a valid id.

**Call relations**: `marked_replies` calls this for every reply span it finds. The result is placed into the `MarkedReply` record, letting later code distinguish a real message reference from an invalid or informal one.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `_artifact_name`  (lines 127–129)

```
def _artifact_name(named: str) -> str
```

**Purpose**: Turns an artifact path into a safe file name for display or download. It prevents a path from smuggling in unwanted directory pieces and gives extension-less names a Markdown `.md` suffix.

**Data flow**: It receives the path from a Markdown link. It trims it, asks `contained_leaf` for only the safe final file-name part, falls back to `artifact` if there is no usable name, and adds `.md` when the name has no dot. It returns the final display name.

**Call relations**: `marked_artifacts` calls this when building each `MarkedArtifact`. This keeps artifact naming predictable before the artifact information is handed to the user-facing layer.

*Call graph*: called by 1 (marked_artifacts); 1 external calls (contained_leaf).


##### `_link_text`  (lines 132–134)

```
def _link_text(named: str | None) -> str | None
```

**Purpose**: Cleans the optional text that should be shown as the clickable label for an artifact. It accepts only short, non-empty one-line text so a tag cannot create a huge or messy link label.

**Data flow**: It receives a raw link label or `None`. It collapses all whitespace into single spaces, then checks the length. It returns the cleaned text if it is between 1 and 80 characters, otherwise it returns `None`.

**Call relations**: `marked_artifacts` calls this while creating artifact records. Its output becomes the optional visible link wording; if it returns `None`, the surrounding interface can use its own default label.

*Call graph*: called by 1 (marked_artifacts).


##### `SpanRedaction.feed`  (lines 149–172)

```
def feed(self, chunk: str) -> str
```

**Purpose**: Filters one incoming stream chunk so reply markup and workspace paths do not appear while the model response is still being generated. It is careful with syntax split across chunks.

**Data flow**: It receives a new text chunk and appends it to any text held back from earlier chunks. If it is currently inside a span, it looks for the matching closer and withholds everything until that closer is found. If it sees a new opener, it publishes only the normal text before it and starts withholding the tagged span. If no opener is found, it publishes only the part that is safe, keeping any trailing fragment that might still become a tag. It returns the text that can safely be shown now, with any stray span markup stripped.

**Call relations**: This method is used during live streaming, before the full answer exists. It relies on `_first_opener` to spot the next tag, `_growing_suffix` to preserve a possible split closer, and `_settled_chars` to decide how much ordinary text is safe to release.

*Call graph*: calls 3 internal fn (_first_opener, _growing_suffix, _settled_chars); 1 external calls (find).


##### `_first_opener`  (lines 175–178)

```
def _first_opener(text: str) -> tuple[re.Match[str], _Tag] | None
```

**Purpose**: Finds the earliest span-opening tag in a piece of text. It tells the streaming redactor where hidden markup begins.

**Data flow**: It receives a text buffer. It searches for each known opener pattern, collects the ones that appear, and chooses the match that starts first. It returns that match together with the tag description, or `None` if no opener is present.

**Call relations**: `SpanRedaction.feed` calls this whenever it is not already inside a hidden span. If an opener is found, `feed` publishes the text before it and starts withholding the tagged content.

*Call graph*: called by 1 (feed).


##### `_growing_suffix`  (lines 181–186)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: Keeps only the tail of a buffer that could still grow into a specific closing token when the next stream chunk arrives. This prevents the redactor from accidentally publishing half of a closing tag.

**Data flow**: It receives the current held text and the closing token it is waiting for. It checks suffixes at the end of the text and returns the longest suffix that matches the beginning of that closing token. If no suffix could become the token, it returns an empty string.

**Call relations**: `SpanRedaction.feed` calls this while it is inside a reply span and has not yet seen the closer. The returned tail stays held for the next chunk, while non-useful hidden content is dropped.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 189–199)

```
def _settled_chars(text: str) -> int
```

**Purpose**: Decides how much buffered text is definitely ordinary text and can be shown now. It holds back syntax that might become a reply span or Markdown file link in the next chunk.

**Data flow**: It receives the current held text. If there is no `<`, all text is safe. If there is one, it examines the tail starting at the last `<` and checks whether it could be the start of a known opener, closer, or partial tag. It returns the number of characters safe to publish now.

**Call relations**: `SpanRedaction.feed` calls this after it finds no complete opener in the current buffer. Its answer lets `feed` stream normal prose promptly while still protecting against tags that are arriving piece by piece.

*Call graph*: called by 1 (feed).


### `core/src/ufo/harness/rounds.py`

`orchestration` · `request handling, during one model round`

A model provider does not usually return one neat answer all at once. It sends a stream of small events: text chunks, tool-call fragments, usage records, and sometimes errors. This file is the part of the harness that watches that stream and turns it into something the rest of the system can trust.

The main piece is `ModelRoundRunner`. Given a request and a provider-specific stream function, it consumes events one by one. Text is not published immediately without checks. It first passes through a `TextFilter`, which is like a safety screen that removes text that must not appear in the live output. To avoid sending every tiny character separately, `_RoundState` buffers text and flushes it either after enough bytes arrive or after a short time passes.

The same state object also remembers tool calls. Tool-call arguments may arrive as many small JSON text fragments, so the file saves them in order and parses them only when the stream finishes. If parsing fails, the round is marked as interrupted so the caller can discard and retry it.

Errors are treated carefully. A failed stream still returns useful facts, such as partial output and usage records. Special errors record whether the provider asked for a retry delay or whether the stream was interrupted in a way that may be safe to retry.

#### Function details

##### `TextFilter.feed`  (lines 23–23)

```
def feed(self, chunk: str) -> str
```

**Purpose**: Defines the contract for a text filter used while streaming model output. A filter receives new text and returns only the part that is safe to show live.

**Data flow**: A chunk of text goes in. The filter updates whatever internal memory it needs, removes or delays unsafe content, and returns the visible text that may be published. This file only defines the shape of the method; actual filters provide the behavior elsewhere.

**Call relations**: The round runner relies on this method when `_RoundState.flush` is ready to publish buffered text. It sits between raw model text and the live output, so unsafe text has a chance to be removed before users see it.


##### `ModelStreamInterrupted.__init__`  (lines 35–37)

```
def __init__(self, kind: str, message: str) -> None
```

**Purpose**: Creates an error that means a model stream failed partway through for a retryable or provider-related reason. The `kind` label tells the caller what kind of interruption happened.

**Data flow**: A short kind code and a human-readable message go in. The message becomes the normal exception text, and the kind is stored on the exception. Later, result-building code can copy that kind into the collected round.

**Call relations**: Provider clients raise this when transport problems, provider status errors, incomplete tool-call JSON, or missing usage data make the streamed round unsafe to commit. `_RoundState.result` also creates this error when tool-call arguments cannot be parsed, so the higher-level engine can treat that round as discardable and retryable.

*Call graph*: called by 10 (status, transport, _complete_chat, _complete_responses, status, transport, result, _generation_usage, complete, stream_error).


##### `ModelRetryAfter.__init__`  (lines 43–45)

```
def __init__(self, seconds: float) -> None
```

**Purpose**: Creates an error for the case where a provider explicitly says, “try this request again after this many seconds.” It keeps the delay in a simple field so callers do not have to parse it from text.

**Data flow**: A number of seconds goes in. The exception stores that number both as its normal exception value and as `seconds`. When the round result is built, that value can be recorded as retry timing information.

**Call relations**: Retry helpers for model providers create this when they see a provider response asking for delayed retry. `_RoundState.result` recognizes it and copies the delay into the collected round, so later code can decide how long to wait.

*Call graph*: called by 2 (status, status).


##### `ModelRoundRunner.run`  (lines 107–138)

```
async def run(self, request: RequestT, text_filter: TextFilter) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: Runs exactly one provider stream and returns one `CollectedRound`. It is the main doorway into this file: give it a model request and a text filter, and it consumes the live response safely.

**Data flow**: A request and a text filter go in. The method starts a timer, creates `_RoundState` to collect everything, starts a small background pacing task, then reads each provider event. As events arrive, the state records text, tool calls, reasoning, usage, and timing. At the end, or after an error, it flushes remaining text and returns a collected round with either the finished answer or details about the failure.

**Call relations**: This method builds the state object that does the detailed event work and starts `ModelRoundRunner.run.pace` in the background to make live text appear regularly. It is the coordinator for the whole round: provider events flow into `_RoundState`, and the final state is turned into the result handed back to the caller.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (Event, create_task).


##### `ModelRoundRunner.run.pace`  (lines 116–121)

```
async def pace() -> None
```

**Purpose**: Keeps live output from sitting too long in the buffer when the provider is sending text slowly. It acts like a metronome that asks the state to flush every short interval until the round stops.

**Data flow**: It reads the shared stop signal and the runner’s flush interval. It waits for either the stop signal or the timeout. If the timeout happens first, it asks the state to flush any buffered text. It produces no direct return value; its effect is timely publishing.

**Call relations**: This helper exists only inside `ModelRoundRunner.run`, which starts it as a background task. It uses `asyncio.wait_for` to sleep until either the round finishes or the next scheduled flush time arrives.

*Call graph*: 1 external calls (wait_for).


##### `_RoundState.__init__`  (lines 142–161)

```
def __init__(self, runner: ModelRoundRunner[RequestT, ToolCallT, ReasoningT, UsageT], text_filter: TextFilter, started: float) -> None
```

**Purpose**: Sets up the scratchpad for one model round. It prepares places to store streamed text, buffered text waiting to publish, tool-call fragments, reasoning events, usage events, and timing milestones.

**Data flow**: The runner, the text filter, and the start time go in. The constructor saves them and creates empty lists and dictionaries for everything the stream may produce. It also creates a lock, which is a small guard that prevents two flushes from changing the text buffer at the same time.

**Call relations**: `ModelRoundRunner.run` creates this state at the beginning of a round. After that, incoming events are fed into `_RoundState.accept`, buffered text is sent through `_RoundState.flush`, and the final collected answer is built by `_RoundState.result`.

*Call graph*: called by 1 (run); 1 external calls (Lock).


##### `_RoundState.accept`  (lines 163–197)

```
async def accept(self, event: object) -> None
```

**Purpose**: Takes one event from the provider stream and records it in the right place. It is the event sorter for a model round.

**Data flow**: One provider event goes in. If it is a stream-start event, the method records when the provider began responding. If it is text, it saves the text, adds it to the publish buffer, marks the first visible moment, and may flush if the buffer is large enough. If it is a tool-call start or tool-call argument fragment, it records the call name and JSON pieces. If it is reasoning or usage, it stores those records. Unknown event types cause an error because the runner cannot safely interpret them.

**Call relations**: `ModelRoundRunner.run` feeds provider events into this method during the round. While sorting events, it calls `_mark_visible` for the first user-visible sign of output, `_elapsed_ms` for timing, and `_RoundState.flush` when enough text has accumulated.

*Call graph*: calls 3 internal fn (_elapsed_ms, _mark_visible, flush); 1 external calls (cast).


##### `_RoundState.flush`  (lines 199–209)

```
async def flush(self) -> None
```

**Purpose**: Publishes buffered text to the live stream after passing it through the safety filter. It prevents text from being sent too often or before it has been checked.

**Data flow**: It reads the current text buffer. If the buffer is empty, nothing happens. Otherwise it joins the buffered chunks, sends them through `TextFilter.feed`, clears the buffer, and resets the pending byte count. If the filter returns visible text, it calls the runner’s publishing function and waits for it if that publisher is asynchronous.

**Call relations**: `_RoundState.accept` calls this when buffered text grows large enough. The runner’s pacing task also exists to trigger regular flushing during a round, so live output feels smooth instead of arriving only at the end.

*Call graph*: called by 1 (accept); 1 external calls (isawaitable).


##### `_RoundState.result`  (lines 211–263)

```
def result(self, error: Exception | None, wall_ms: int) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: Turns everything collected during the stream into one final `CollectedRound`. It also translates failures into a structured result that preserves useful partial information.

**Data flow**: An optional error and the total elapsed wall-clock time go in. If there was an error, the method returns a result containing usage records, error name and message, retry details when available, and partial output from text and tool-call fragments. If there was no error, it requires at least one usage record, parses accumulated tool-call JSON into dictionaries, builds proper tool-call objects, and returns the finished text, tool calls, reasoning, usage, and timing. If tool-call JSON cannot be parsed, it converts that into a stream-interruption result.

**Call relations**: `ModelRoundRunner.run` asks this method for the final answer after streaming and flushing are done. It uses `ModelStreamInterrupted` to mark malformed tool-call JSON as a retryable interrupted round, and it calls the runner’s `new_tool_call` factory to turn raw tool-call pieces into the caller’s own tool-call type.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (__init__, loads, cast).


##### `_RoundState._mark_visible`  (lines 265–270)

```
def _mark_visible(self) -> None
```

**Purpose**: Records the first moment when the round produced something visible, such as text or the start of a tool call. This helps measure how long the user waited before seeing activity.

**Data flow**: No outside data goes in. The method checks whether the first visible time is already recorded. If not, it calculates elapsed milliseconds from the round start, stores that value, and optionally reports a milestone named `first_visible_event`.

**Call relations**: `_RoundState.accept` calls this when it sees text or a tool-call start. It relies on `_elapsed_ms` for the timing calculation and informs the runner’s milestone callback when one is available.

*Call graph*: calls 1 internal fn (_elapsed_ms); called by 1 (accept).


##### `_RoundState._elapsed_ms`  (lines 272–273)

```
def _elapsed_ms(self) -> int
```

**Purpose**: Calculates how many milliseconds have passed since this model round began. It gives all timing fields a common reference point.

**Data flow**: It reads the runner’s monotonic clock and the stored start time. It subtracts start from now, converts seconds to milliseconds, and returns the integer result. It does not change state.

**Call relations**: `_RoundState.accept` uses this when recording provider-start timing, and `_RoundState._mark_visible` uses it when recording the first visible event. This keeps timing measurements consistent across the round.

*Call graph*: called by 2 (_mark_visible, accept).

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-feature-flags` — The shared on/off switches that let the service enable or disable behavior at runtime.
- `reg-model-catalog` — The live menu of AI models, their capabilities, providers, and calling rules.
- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-live-update-streams` — The shared live progress channels that stream text, status, costs, and completion events to clients.
- `reg-inbound-delivery-ledger` — The durable deduplication and delivery records for inbound messages, writebacks, and mid-turn replies.
- `reg-environment-documents` — The saved per-agent run environment describing prompts, tools, skills, files, and model overrides.
- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-browser-sessions` — The active browser automation workbench for a turn, including Chrome sessions, tabs, and downloads.
- `reg-subagent-delivery-state` — The parent-child task links and owed-result records used when agents spawn helper agents.
- `reg-memory-store` — The remembered facts and searchable memory chunks that can be retrieved or condensed later.
- `reg-artifact-blob-store` — The shared files, media blobs, previews, metadata, and signed-download records created by agent work.
- `reg-runtime-fleet-liveness` — The shared record of running service and worker instances, heartbeats, listener claims, and stuck work.
- `reg-usage-ledger-balance` — The money and usage ledger that tracks costs, prepaid balances, limits, exports, and billing status.
- `reg-telemetry-context` — The shared trace, metric, log, health, and redaction context used to observe work across the system.
- `reg-credential-request-state` — Pending and fulfilled credential-connection requests, OAuth/device-code callback context, and idempotency markers for credential fulfillment.
- `reg-turn-billing-snapshot` — Per-turn frozen billing identity and BYOK attempt state captured before execution and consumed later for stable accounting.
- `reg-turn-resource-budget` — Per-turn context-window, token, image, reasoning, and cost/resource budgets derived before execution and consumed by prompt assembly, model calls, tools, and accounting.
- `reg-active-workflow-handles` — In-process handles for currently executing turns/workflows, including cancellation tokens and cleanup callbacks used to stop, tear down, or recover live work.
- `reg-workflow-checkpoints` — Durable per-turn workflow checkpoints, serialized runner state, and step/tool-output idempotency records used to resume, cancel, or recover work without rerunning completed actions.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.
