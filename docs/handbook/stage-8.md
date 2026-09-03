# Agent engine and model conversation loop  `stage-8`

This stage is the agent’s main work loop. After a user request or subagent task is placed in the queue, core/src/ufo/runtime/queue.py claims that work, prepares the needed model, tools, sandbox, and settings, and starts real execution. core/src/ufo/runtime/engine.py then runs the turn in a crash-safe way: it records progress, avoids repeating completed model calls or tool actions, tracks billing, and publishes the final result.

Inside that turn, core/src/ufo/harness/agent.py is the steering wheel. It asks the model what to do, runs any requested tools, sends the tool results back, and repeats until the model gives a final answer or the turn runs out. The model provider adapters are the plug converters that let this same loop talk to Anthropic, OpenAI, OpenRouter, and similar services. The streaming normalizer turns live, messy model output into clean text, tool calls, usage data, and final records. If the conversation gets too long, the compaction system summarizes older messages so the model can keep going. Extension hooks can approve, block, or modify actions, and activity labels turn tool calls into friendly user status messages.

## Sub-stages

- [Model provider request/stream adapters](stage-8.1.md) `stage-8.1` — 6 files
- [Conversation compaction and context-window management](stage-8.2.md) `stage-8.2` — 2 files
- [Streaming model output normalization](stage-8.3.md) `stage-8.3` — 1 files

## Files in this stage

### Turn dispatch
Queue-level orchestration claims queued work, prepares the execution environment, and hands each turn to the runtime engine.

### `core/src/ufo/runtime/queue.py`

`orchestration` · `main loop / queued turn execution`

A “turn” is one unit of agent work, like one message in a conversation or one spawned subtask. This file is the bridge between durable queue records in the database and the live work needed to answer them. It sets up DBOS workflows, which are restartable background jobs, so a turn can survive process crashes without losing its place.

The file first defines small rules for fairness, billing, skill visibility, tool grants, subagent model choice, and sandbox access. Then the main workflow claims a turn, loads its database row, resolves the agent and audience, freezes the billing identity for this attempt, asks the host environment to assemble prompts and tools, opens or reuses a sandbox, and hands everything to `TurnEngine`, which performs the actual model/tool loop.

It also handles important edge cases. Scheduled background turns cannot starve normal user turns. Billing rates are frozen so crash recovery cannot re-price old model calls. Subagents that need a member’s own provider account fail with a clear message if that account is gone or rate-limited. If setup fails before the engine can write a terminal result, this file writes a failed terminal frame itself so callers do not wait forever. Think of it as the station dispatcher: it does not drive every train, but it decides which train may leave, gives it the right crew and route, and records when it arrives.

#### Function details

##### `_turn_gates`  (lines 154–170)

```
def _turn_gates(parent_turn_id: UUID | None, admission_source: TurnAdmissionSource) -> tuple[asyncio.Semaphore, ...]
```

**Purpose**: Chooses any fairness locks a turn must hold before it runs. In practice, only top-level scheduled turns are throttled, so a flood of scheduled work cannot consume the whole process.

**Data flow**: It receives the parent turn id and the source that admitted the turn. If the turn is not a root scheduled turn, it returns no locks. If it is a root scheduled turn, it finds or creates a per-event-loop semaphore, which is a counter-style lock, and returns it for the caller to acquire.

**Call relations**: _execute_turn asks this helper before running the body of a turn. The helper relies on asyncio’s current event loop and semaphore so the workflow can pause until there is a fair scheduled-turn slot available.

*Call graph*: called by 1 (_execute_turn); 2 external calls (Semaphore, get_running_loop).


##### `_Rates.of`  (lines 198–206)

```
def of(cls, price: ModelPrice) -> '_Rates'
```

**Purpose**: Copies the token price fields from a model price card into the local `_Rates` shape. This is used when freezing billing details onto a turn.

**Data flow**: It takes a `ModelPrice` object with input, output, and cache token rates. It copies those numbers into a new `_Rates` object. The result is a small serializable price snapshot.

**Call relations**: This is a conversion helper for the billing objects in this file. It is used when creating billing identities so stored turn billing can later be turned back into pricing data.


##### `_Rates.price`  (lines 208–216)

```
def price(self) -> ModelPrice
```

**Purpose**: Turns the local stored rate snapshot back into a `ModelPrice`. This lets saved billing information be reused wherever the rest of the model system expects a normal price card.

**Data flow**: It reads the rate fields on `_Rates`, builds a `ModelPrice` with the same values, and returns that object. It does not change stored data.

**Call relations**: _BillingIdentity.pricing uses this method when rebuilding a full `Pricing` object for the engine. It hands the rates back in the standard model-pricing format.

*Call graph*: 1 external calls (__init__).


##### `_BillingIdentity.pricing`  (lines 232–239)

```
def pricing(self) -> Pricing
```

**Purpose**: Builds the full pricing table for the model that actually serves this turn attempt, including alternate models the attempt may move to. This keeps token billing consistent throughout the run.

**Data flow**: It reads the frozen billing identity: main model, digest, and any alternate model rates. It converts those stored rates into `ModelPrice` objects and returns a `Pricing` object keyed by model name.

**Call relations**: _run_turn passes this pricing object into `TurnEngine`. The engine can then price every round using the same frozen rate card, even after recovery or alternate-model movement.

*Call graph*: 1 external calls (__init__).


##### `_plan_pricing`  (lines 242–243)

```
def _plan_pricing(models: tuple[str, ...]) -> Pricing
```

**Purpose**: Creates a zero-dollar pricing table for plan-funded models. This is used when a connected account or plan subscription already covers the model use, so token counts remain tracked but dollar cost is zero.

**Data flow**: It receives model names, pairs each one with the special zero-price card, and returns a `Pricing` object with a digest. The output says these models are priced consistently as plan-served.

**Call relations**: _TurnBilling._identity uses this when a resolved model is plan-funded. `_TurnBilling._validate` also uses it to check that a stored billing record still matches the funding situation.

*Call graph*: called by 2 (_identity, _validate); 1 external calls (pricing_from).


##### `_TurnBilling.resolve`  (lines 254–268)

```
async def resolve(self) -> tuple[_BillingIdentity, ResolvedModelClient, bool]
```

**Purpose**: Finds or freezes the billing identity for one turn attempt, then verifies that the chosen model and payer have not changed underneath it. This protects both recovery and accounting.

**Data flow**: It starts with a turn id, attempt id, candidate model, and possible alternates. It first looks for an already stored identity for this attempt; if none exists, it resolves the candidate model and stores a new identity. It validates the stored identity, freezes whether the turn used the workspace’s own key, and returns the billing identity, resolved model client, and bring-your-own-key flag.

**Call relations**: _run_turn calls this after deciding which model should serve the turn. It delegates storage to `_stored_billing_identity`, `_frozen_billing_identity`, and `_frozen_byok`, and uses `_identity` and `_validate` to make the billing record safe.

*Call graph*: calls 5 internal fn (_identity, _validate, _frozen_billing_identity, _frozen_byok, _stored_billing_identity); 1 external calls (__init__).


##### `_TurnBilling._identity`  (lines 270–286)

```
def _identity(self, model: str, resolved: ResolvedModelClient) -> _BillingIdentity
```

**Purpose**: Creates the billing snapshot that should be stored for a turn attempt. It records the chosen model, its rates, payer information, and rates for any allowed alternate models.

**Data flow**: It receives a model name and its resolved client information. If the model is plan-funded, it uses the zero-dollar plan pricing; otherwise it uses the registry’s normal pricing table. It returns a `_BillingIdentity` ready to save on the turn row.

**Call relations**: _TurnBilling.resolve calls this when no billing identity has already been stored for the attempt. It uses `_plan_pricing` when needed so all plan-funded rates are frozen together.

*Call graph*: calls 1 internal fn (_plan_pricing); called by 1 (resolve); 1 external calls (__init__).


##### `_TurnBilling._validate`  (lines 289–296)

```
def _validate(billing: _BillingIdentity, model: ResolvedModelClient) -> None
```

**Purpose**: Checks that a saved billing identity still matches the model client now being used. It prevents a turn attempt from silently switching funding source or payer during execution.

**Data flow**: It receives the frozen billing identity and the resolved model client. It compares funding, payer, and whether the pricing digest looks like plan pricing. If the facts no longer line up, it raises a model-funding-changed error; otherwise it returns nothing.

**Call relations**: _TurnBilling.resolve calls this before returning billing details to `_run_turn`. It uses `_plan_pricing` as the reference for what a plan-funded digest should look like.

*Call graph*: calls 1 internal fn (_plan_pricing); called by 1 (resolve); 1 external calls (__init__).


##### `_without_workspace_skills`  (lines 299–302)

```
async def _without_workspace_skills(name: str) -> None
```

**Purpose**: Represents the no-op loader used when workspace skills are disabled. It exists so code can point to a loader function even when that loader intentionally loads nothing.

**Data flow**: It receives a skill name and ignores it. It returns `None` and makes no changes.

**Call relations**: This is a small compatibility helper for skill-loading paths. It stands in for real workspace-skill loading when the agent setting says saved workspace skills should not be used.


##### `_member_skill_turn`  (lines 305–317)

```
def _member_skill_turn(turn: Turn) -> bool
```

**Purpose**: Decides whether a turn is the kind that should see member-saved skill information. It avoids adding skill hints to machine-only internal turns that have no meaningful user prompt.

**Data flow**: It reads the turn’s admission source, speaker, and parent information. Prepared intent turns are rejected. Speakerless root internal turns are also rejected. All other turns are accepted.

**Call relations**: _member_skill_block uses this to decide whether to render the skill block. `_run_turn` also uses it when deciding whether to launch shadow skill-selection logging.

*Call graph*: called by 2 (_member_skill_block, _run_turn).


##### `_member_skill_block`  (lines 320–327)

```
def _member_skill_block(turn: Turn, view: MemberVisibility, enabled: bool) -> str
```

**Purpose**: Returns the saved-skills text that should be included with the turn, or an empty string if it should not appear. This lets configuration and turn type control whether member skills influence the prompt.

**Data flow**: It receives a turn, a member-skill visibility view, and an enabled flag. If disabled or not a member-skill turn, it returns an empty string. Otherwise it returns the prepared block from the view.

**Call relations**: It builds on `_member_skill_turn` so prompt rendering and memory-style recall use matching rules. The assembled environment can use its output as the member skill block for the model.

*Call graph*: calls 1 internal fn (_member_skill_turn).


##### `_prompt_skill_index`  (lines 330–334)

```
def _prompt_skill_index(skills: SkillRegistry, enabled: bool) -> tuple[tuple[str, str], ...]
```

**Purpose**: Chooses what skill index text should be rendered into the prompt. When member skills are enabled, it uses the fold-aware prompt index; otherwise it shows only the registry’s normal index.

**Data flow**: It receives a skill registry and an enabled flag. If enabled, it asks the skill-selection code for a prompt-ready index. If disabled, it returns the registry’s plain index.

**Call relations**: This helper belongs to the prompt assembly side of turn setup. It connects skill registry data to the text the model can see.

*Call graph*: calls 1 internal fn (index); 1 external calls (prompt_index).


##### `_fire_shadow_selection`  (lines 340–347)

```
def _fire_shadow_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Starts a background comparison of two skill-selection methods without delaying the turn. This is used to collect evidence about skill retrieval quality safely.

**Data flow**: It receives an index, embedding client, turn, and skill cards. It creates an asynchronous task to run `_shadow_skill_selection`, stores a reference so it is not garbage-collected, and removes that reference when the task finishes.

**Call relations**: _run_turn calls this only for eligible main-agent turns where the skill catalog did not fit in the prompt. It hands the real work to `_shadow_skill_selection` and never waits for it.

*Call graph*: calls 1 internal fn (_shadow_skill_selection); called by 1 (_run_turn); 1 external calls (create_task).


##### `_shadow_skill_selection`  (lines 350–376)

```
async def _shadow_skill_selection(index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]) -> None
```

**Purpose**: Compares lexical skill matching with vector search for one turn and logs the difference. It is best-effort observation, not part of the user-visible turn result.

**Data flow**: It reads the inbound turn text, trims it to the skill-query limit, embeds it into a numeric vector, searches the index, also runs text-based top-k selection, and logs both result lists. If anything fails or takes too long, it logs a failure instead of raising.

**Call relations**: _fire_shadow_selection launches this as a background task. It uses the embedding and index services, then records the comparison through logging so later analysis can improve skill selection.

*Call graph*: calls 2 internal fn (embed, vector); called by 1 (_fire_shadow_selection); 3 external calls (timeout, log, select_top_k).


##### `with_implied_grants`  (lines 386–389)

```
def with_implied_grants(names: set[str]) -> set[str]
```

**Purpose**: Expands a set of allowed tool or action names with companion grants that must travel with them. For example, a skill loader also needs the skill search action to be useful.

**Data flow**: It receives a mutable set of names. For each original name, it looks up any implied companion names and adds them into the same set. It returns the expanded set.

**Call relations**: _agent_actions, `_agent_tools`, `_subagent_actions`, and `_subagent_tools` all call this before filtering what an agent or subagent may use. It centralizes the rule so companion grants are applied consistently.

*Call graph*: called by 4 (_agent_actions, _agent_tools, _subagent_actions, _subagent_tools).


##### `_agent_actions`  (lines 392–407)

```
def _agent_actions(actions: Mapping[str, Mapping[str, BoundAction]], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> frozenset[str]
```

**Purpose**: Calculates which canonical object-action ids the main agent may hold for this turn. A canonical id is the stable internal name for an action, even if tools or extensions expose it differently.

**Data flow**: It receives the active action registry, an optional allowlist, the admission source, and possibly the speaker member. If there is no allowlist, or a speaking prepared intent is being submitted, it grants all non-profile-only actions. Otherwise it expands the allowlist with implied grants and returns only matching canonical ids.

**Call relations**: This mirrors the tool-selection rules used by `_agent_tools`. It is used by turn assembly code to decide which object actions the model can discover and dispatch.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `SubagentKeyWithdrawn.__init__`  (lines 422–437)

```
def __init__(self, profile: str, connect_url: str | None=None, rate_limited: bool=False) -> None
```

**Purpose**: Builds a clear error for the case where a subagent must run on the member’s own provider account but no usable account is available. The message tells the user whether the problem is disconnection or rate limiting.

**Data flow**: It receives the profile name, optional account-connection URL, and a rate-limit flag. It formats a human-readable exception message, stores the profile name, and records whether rate limiting was the cause.

**Call relations**: _subagent_model raises this when a profile that requires a member-owned model key cannot find one. The error can surface to the turn failure path with a useful repair instruction.

*Call graph*: called by 1 (_subagent_model).


##### `_member_accounts_connectable`  (lines 440–442)

```
def _member_accounts_connectable(runtime: 'Runtime') -> bool
```

**Purpose**: Checks whether this deployment has any extension capable of storing member-owned provider accounts. This changes how subagent account failures should be interpreted.

**Data flow**: It reads the runtime’s installed manifests. If any manifest says it can connect member accounts, it returns true; otherwise false.

**Call relations**: _run_turn passes this fact into the subagent helper setup. `_subagent_model` uses it to decide whether missing account data should produce a member-account error.

*Call graph*: called by 2 (_run_turn, _subagent_model).


##### `_subagent_model`  (lines 445–476)

```
def _subagent_model(profile: SubagentProfile, connected: str | None, agent: Agent, runtime: 'Runtime', pinned: str | None, document: str | None) -> str
```

**Purpose**: Chooses the model that a subagent turn should run on. It respects special profiles that must use the member’s own account, explicit environment choices, pinned models, profile defaults, and the parent agent default.

**Data flow**: It receives the subagent profile, connected provider name if any, agent, runtime, pinned model, and environment-document model. For own-account profiles, it prefers an environment document, then the connected account’s bound model, and otherwise raises a clear account error or falls back only when allowed. For normal profiles, it uses the pinned model if present, otherwise the profile or agent model.

**Call relations**: _run_turn calls this after resolving a subagent profile and connected member providers. It may raise `SubagentKeyWithdrawn` to stop a turn that would otherwise accidentally spend the deployment’s key.

*Call graph*: calls 2 internal fn (__init__, _member_accounts_connectable); called by 1 (_run_turn).


##### `_own_account_alternates`  (lines 479–506)

```
def _own_account_alternates(profile: SubagentProfile, connected: tuple[str, ...], chosen: str, document: str | None, runtime: 'Runtime') -> tuple[str, ...] | None
```

**Purpose**: Lists other member-owned account models that a subagent may move to if the first provider account is rate-limited. This lets work continue on another subscription the member connected.

**Data flow**: It receives the profile, all connected provider names, the chosen model, any environment-document model, and the runtime. If the profile is not an own-account profile, or the environment document chose the model, it returns `None`. Otherwise it resolves models for connected providers and returns the models other than the one currently chosen.

**Call relations**: _run_turn calls this after `_subagent_model`. Its output becomes alternates for billing and serving, so the model layer can move between member accounts without changing the billing meaning.

*Call graph*: called by 1 (_run_turn).


##### `_subagent_actions`  (lines 509–527)

```
def _subagent_actions(actions: Mapping[str, Mapping[str, BoundAction]], profile: SubagentProfile, grants: frozenset[str]) -> frozenset[str]
```

**Purpose**: Calculates which canonical object-action ids a subagent profile may use. It combines the profile’s declared actions with shared grants unless the profile is isolated.

**Data flow**: It receives the action registry, the profile, and grants from the parent or deployment. It builds the allowed names, adds implied companions, and returns canonical action ids that match allowed names or default subagent actions when isolation permits them.

**Call relations**: This is the action-side partner to `_subagent_tools`. It relies on `with_implied_grants` so subagents get required companion actions just like main agents.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_with_action_verbs`  (lines 530–551)

```
def _with_action_verbs(selected: tuple[ToolDef, ...], all_tools: tuple[ToolDef, ...], granted_actions: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Adds the basic object-action dispatcher and read tools when a turn has any granted object actions. This prevents a model from having permission to act but no way to discover or invoke those actions.

**Data flow**: It receives selected tools, all available tools, and granted action ids. It removes any existing dispatcher from the selected set. If there are no granted actions, it returns the selected tools without the dispatcher. If actions exist, it adds the dispatcher and read tools that are present and not already selected.

**Call relations**: This helper is used by tool assembly logic to keep action permissions and visible tools aligned. It makes allowlists less brittle by adding the structural tools needed for granted actions.


##### `_agent_tools`  (lines 554–578)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource, speaker_member_id: UUID | None=None) -> tuple[ToolDef, ...]
```

**Purpose**: Selects the actual tool definitions the main agent may call during a turn. It enforces allowlists while keeping profile-only tools away from ordinary agent turns.

**Data flow**: It receives all registered tools, an optional allowlist, the admission source, and possibly a speaker member id. Without an allowlist, or for a speaking prepared intent, it returns all non-profile-only tools. With an allowlist, it expands implied grants and returns only tools whose names are allowed.

**Call relations**: This mirrors `_agent_actions` for callable tools. The environment assembly can use it to build the tool registry passed into the turn engine.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_resolve_profile`  (lines 581–596)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: Looks up the subagent profile named on a queued turn and logs useful details if the profile no longer exists. This gives operators enough information to diagnose a dropped extension or stale queued child turn.

**Data flow**: It receives the subagent registry, turn id, and requested profile name. It returns the matching profile if found. If not found, it logs the requested name and registered names, then re-raises the profile error.

**Call relations**: _run_turn calls this for subagent turns before choosing model and tools. It delegates the lookup to the subagent registry and adds context to failures.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 599–611)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Selects the tool definitions a subagent profile may call. It follows the profile’s tool list, shared grants, isolation setting, implied companion grants, and default subagent tools.

**Data flow**: It receives all tools, the profile, and granted names. It builds the allowed set, optionally includes grants, adds implied companions, filters live tools against that set, and includes default subagent tools when the profile is not isolated.

**Call relations**: This is the tool-side partner to `_subagent_actions`. It uses `with_implied_grants` so profile tool declarations automatically include required companions.

*Call graph*: calls 1 internal fn (with_implied_grants).


##### `_apply_provisions`  (lines 617–626)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: Installs extension-provided agents into a workspace once per process. This makes sure a workspace has the agents shipped by active extensions before its first turn in this process runs.

**Data flow**: It receives the runtime and workspace id. If this process already provisioned that workspace, it returns. Otherwise it runs agent provisioning from the runtime manifests and remembers the workspace id.

**Call relations**: _execute_turn calls this after fairness gates and before running the turn. It keeps provisioning close to turn startup while making repeated calls cheap.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `TurnEnvironment.assemble`  (lines 696–696)

```
async def assemble(self, request: AssembleRequest) -> AssembledTurn
```

**Purpose**: Defines the host-provided operation that builds the full environment for a turn. The runtime asks for prompts, tools, hooks, skills, and seeded files without knowing host-specific assembly details.

**Data flow**: It receives an `AssembleRequest` containing the turn, agent/profile, model, skills, subagents, grants, and environment document id. An implementation returns an `AssembledTurn` containing the prompt, tool registry, hooks, skill preload, files, and related metadata.

**Call relations**: _run_turn calls this through `runtime.environment`. The protocol boundary lets the queue runner stay focused on execution while another layer decides how prompts and tools are composed.


##### `TurnEnvironment.environment_model`  (lines 698–698)

```
async def environment_model(self, environment: str, profile: str | None) -> str | None
```

**Purpose**: Defines how the host reports whether an environment document pins a model. This lets billing and model validation happen before the full environment is assembled.

**Data flow**: It receives an environment document id and optional subagent profile name. An implementation returns a model name if the document chooses one, or `None` if it does not.

**Call relations**: _run_turn calls this before resolving the final model. The result can override a runtime pin and becomes part of the billing decision.


##### `TurnEnvironment.clis`  (lines 700–700)

```
def clis(self) -> dict[str, CliCredential]
```

**Purpose**: Defines how the host exposes command-line credential helpers available inside sandboxes. These helpers let tools like git authenticate safely.

**Data flow**: It takes no arguments besides the implementation object. It returns a dictionary of CLI credential definitions keyed by name.

**Call relations**: _run_turn reads this before building the sandbox opener. `_open_sandbox` and `SandboxAuthorizer.authorize` then use the CLI definitions to prepare environment variables.


##### `TurnEnvironment.slots`  (lines 702–702)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: Defines which credential slots the environment may request for sandbox use. A slot is a declared place where an extension can supply a secret or account credential.

**Data flow**: It takes no arguments besides the implementation object and returns the declared credential slots. It does not read the actual secrets by itself.

**Call relations**: _run_turn passes these slots into `_open_sandbox`, which derives sandbox environment variables only when the sandbox is actually opened.


##### `init_runtime`  (lines 737–748)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the process-wide runtime object used by queued turn workflows. It also seeds system skills into sandbox carriers so sandboxes can start with built-in skill content.

**Data flow**: It receives a fully built `Runtime`. If one is already installed, it raises an error. Otherwise it builds a system skill bundle from bundled skills, seeds compatible sandbox carriers, and stores the runtime in the module global.

**Call relations**: The serving process calls this during startup before any turn workflow runs. `_execute_turn` later reads the stored runtime and fails fast if initialization never happened.

*Call graph*: calls 1 internal fn (from_skills).


##### `reset_runtime`  (lines 751–756)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed runtime so tests can install a fresh one. Production serving initializes once and does not normally call this.

**Data flow**: It takes no input. It sets the module-level runtime back to `None` and returns nothing.

**Call relations**: This is a testing seam around `init_runtime`’s single-initialization guard. It avoids tests having to mutate the global variable directly.


##### `_execute_turn`  (lines 759–841)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the outer workflow body for one queued turn. It binds the workspace, enforces fairness, applies provisions, sets authority context, runs the turn, and performs after-run delivery and dispatch.

**Data flow**: It receives workspace and turn ids as strings. It loads basic turn routing facts from the database, decides authority and scheduled-turn gates, applies provisions, runs `_run_turn`, and catches setup failures by writing a failed terminal. Afterward it delivers child results to parents, offers the conversation’s next turn, and returns a status string.

**Call relations**: The DBOS `turn_workflow` calls this as the main workflow body. It orchestrates helpers such as `_turn_gates`, `_apply_provisions`, `_run_turn`, `_commit_failed_terminal`, `_deliver_to_parent`, and `_offer_next_turn`.

*Call graph*: calls 6 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _offer_next_turn, _run_turn, _turn_gates); called by 1 (turn_workflow); 11 external calls (AsyncExitStack, select, monotonic, workspace_tx, emit_histogram, turn_span, agent, turn_authority, model_authority, ws (+1 more)).


##### `_offer_next_turn`  (lines 844–856)

```
async def _offer_next_turn(runtime: Runtime, conversation_id: UUID) -> None
```

**Purpose**: Asks the dispatcher to start the next queued turn in the same conversation after the current workflow ends. If this handoff fails, it logs and lets a later sweep retry.

**Data flow**: It receives the runtime and conversation id. It calls the turn dispatcher for that conversation. On failure, it logs the error class and returns without raising.

**Call relations**: _execute_turn calls this after every turn exit path. It deliberately swallows errors because the current turn’s terminal result is already decided.

*Call graph*: called by 1 (_execute_turn); 2 external calls (log, dispatch_next_turn).


##### `_deliver_to_parent`  (lines 859–891)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: Delivers a finished child turn’s terminal result back to the parent conversation that spawned it. This is how subagent results become visible to their caller.

**Data flow**: It receives the runtime and child turn id. It reloads the turn row from the database, exits if the turn has no parent or no terminal result, and otherwise asks `SubagentResult` to deliver the durable result. If delivery fails, it logs and returns.

**Call relations**: _execute_turn calls this after `_run_turn` or after failure handling. It uses the runtime’s invoker and subagent registry, but avoids letting delivery errors relabel an already-finished turn.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_run_turn`  (lines 894–1179)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs the actual agent execution for a claimed turn. This is the central assembly line that turns database facts into a configured `TurnEngine` run.

**Data flow**: It receives the runtime and turn id. It claims the turn, loads the turn and agent, resolves lineage, subagent profile, environment model, final serving model, billing identity, tools, prompt, hooks, skills, sandbox, credentials, subagents, and pricing. It then constructs `TurnEngine`, runs either normal mode or intent mode, and returns the resulting status, or a special status for parked, superseded, or failed turns.

**Call relations**: _execute_turn calls this inside workspace and tracing context. It calls many helpers in this file, including `_load_turn`, `_resolve_profile`, `_subagent_model`, `_own_account_alternates`, `_previous_turn_ended_at`, `_run_lineage`, `_fire_shadow_selection`, and `_commit_failed_terminal`, then hands execution to `TurnEngine`.

*Call graph*: calls 10 internal fn (_commit_failed_terminal, _fire_shadow_selection, _load_turn, _member_accounts_connectable, _member_skill_turn, _own_account_alternates, _previous_turn_ended_at, _resolve_profile, _run_lineage, _subagent_model); called by 1 (_execute_turn); 27 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 933–935)

```
def subagents_for(authority: ExecutionAuthority) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates an authority-specific subagent interface for a running turn. It lets code inside the engine spawn subagents under the correct execution authority.

**Data flow**: It receives an execution authority. It asks the prepared `Subagents` object to authorize that authority, then returns the spawn function and authorized subagent controller.

**Call relations**: This nested helper is created inside `_run_turn` and passed into `TurnEngine`. The engine can call it when a tool or hook needs subagent access under a specific authority.


##### `_commit_failed_terminal`  (lines 1182–1253)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, attempt: str, error: BaseException) -> None
```

**Purpose**: Writes a failed terminal result when something goes wrong before or outside the engine’s own failure handling. This ensures clients waiting on the turn receive an end state instead of hanging forever.

**Data flow**: It receives the hub, turn id, attempt id, and error. It builds a failed terminal frame, repeatedly tries to update the turn row only if the current attempt still owns it or it is still queued, emits metrics and logs on success, publishes the terminal to listeners, and backs off before retrying if the database update path itself fails.

**Call relations**: _execute_turn and `_run_turn` call this when exceptions escape setup or assembly. It publishes through the hub so waiting clients and surfaces see the failure.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 12 external calls (__init__, __init__, sleep, and_, or_, update, workspace_tx, emit_metric, formatted_stack, log (+2 more)).


##### `turn_workflow`  (lines 1257–1258)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the durable DBOS workflow entry for running a turn. DBOS can replay or resume this workflow after failures.

**Data flow**: It receives workspace and turn ids as strings from the queue/workflow system. It passes them to `_execute_turn` and returns that status.

**Call relations**: This is the file’s workflow entrypoint for DBOS. It keeps the decorated workflow tiny and delegates all real orchestration to `_execute_turn`.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 1261–1354)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Loads the full turn, its agent, and the conversation audience from the database. It converts raw database fields into the structured objects used by the runtime.

**Data flow**: It receives a turn id. It queries the turn, agent, and conversation tables, validates nested JSON fields such as context and runtime config, builds a `Turn`, builds an `Agent`, parses the audience, and returns all three.

**Call relations**: _run_turn calls this after claiming a turn, and also when a claim is superseded and transcript repair may be needed. It is the main database-to-runtime conversion point for turn execution.

*Call graph*: called by 1 (_run_turn); 9 external calls (__init__, __init__, model_validate, model_validate, model_validate, model_validate, select, workspace_tx, parse_audience).


##### `_run_lineage`  (lines 1357–1389)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: Finds where a spawned turn should publish its live activity. For child turns, activity is tied back to the root turn stream that user interfaces already follow.

**Data flow**: It receives a turn. If the turn has no parent, it returns `None`. Otherwise it walks parent links in the database until it finds the root turn, derives a profile label if needed, and returns a `RunLineage` object with root, parent, profile, and display name.

**Call relations**: _run_turn calls this before creating `TurnEngine`. The engine uses the lineage to publish child activity under the right parent/root stream.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 1392–1404)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: Finds when the previous turn in the same conversation ended. This gives the engine timing context for the current turn.

**Data flow**: It receives a turn. If it is the first turn in the conversation, it returns `None`. Otherwise it queries the previous sequence number’s updated time and ensures the returned datetime has a timezone.

**Call relations**: _run_turn calls this for non-intent turns and passes the result into `TurnEngine`. Intent turns skip it because they are dispatched differently.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_frozen_billing_identity`  (lines 1407–1428)

```
async def _frozen_billing_identity(turn_id: UUID, candidate: _BillingIdentity) -> _BillingIdentity
```

**Purpose**: Stores the billing identity for a turn attempt if needed, and returns the identity that should be used. This makes concurrent recovery attempts agree on one billing snapshot.

**Data flow**: It receives a turn id and candidate billing identity. It locks the turn row, checks whether a stored identity for the same attempt already exists, and returns it if so. Otherwise it writes the candidate to the turn row and returns the candidate.

**Call relations**: _TurnBilling.resolve calls this when no stored identity has already been found. It is the database write that freezes model rates and payer data for the attempt.

*Call graph*: called by 1 (resolve); 4 external calls (model_dump, select, update, workspace_tx).


##### `_stored_billing_identity`  (lines 1431–1441)

```
async def _stored_billing_identity(turn_id: UUID, attempt: str) -> _BillingIdentity | None
```

**Purpose**: Reads any billing identity already stored for the current turn attempt. It ignores identities from older attempts.

**Data flow**: It receives a turn id and attempt id. It loads the billing identity JSON from the turn row, validates it into `_BillingIdentity`, and returns it only if its attempt matches. Otherwise it returns `None`.

**Call relations**: _TurnBilling.resolve calls this first so crash recovery or duplicate execution can reuse the existing billing snapshot instead of creating a new one.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `_frozen_byok`  (lines 1444–1493)

```
async def _frozen_byok(turn_id: UUID, decided: bool, attempt: str) -> bool
```

**Purpose**: Freezes whether this attempt used a workspace/member-provided key rather than the platform default. This matters because billing depends on who actually paid for model access.

**Data flow**: It receives a turn id, the caller’s current decision, and attempt id. It reads the stored BYOK flag and attempt. If the same attempt already has a flag, it returns that. Otherwise it writes the decision for this attempt, rereads the row, and returns the settled stored value or the original decision as a fallback.

**Call relations**: _TurnBilling.resolve calls this after model resolution. The returned boolean is passed into `TurnEngine` so token accounting matches the key source frozen for this attempt.

*Call graph*: called by 1 (resolve); 4 external calls (or_, select, update, workspace_tx).


##### `_open_sandbox`  (lines 1496–1556)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens or attaches to the sandbox container for a turn and prepares the environment variables it needs. The sandbox is where shell commands, files, git operations, and tool bridge work happen.

**Data flow**: It receives sandbox services, token codec, turn, optional grant store, CLI credentials, credential store, credential slots, and a cache-rewrite flag. It creates a signed run token, builds environment variables for conversation id, tool bridge URL, git proxy authentication, CLI grants, and keyed provider credentials, then asks the conversation sandbox service to open the session.

**Call relations**: _run_turn installs this as the lazy sandbox opener through `_LateSandbox`, so credentials are derived only if the turn actually opens the sandbox. It uses run tokens and sandbox execution-environment helpers to make proxy and credential access safe.

*Call graph*: calls 2 internal fn (open, encode); 7 external calls (__init__, span, cache_git_config, _git_config_env, _grant_cli_env, _keyed_provider_env, cli_git_config).


##### `SandboxAuthorizer.authorize`  (lines 1567–1584)

```
async def authorize(self, authority: ExecutionAuthority) -> Sandbox
```

**Purpose**: Re-authorizes an existing sandbox for a different execution authority. This lets a running turn expose sandbox access to tools or subagents with the right permissions.

**Data flow**: It receives an execution authority. It creates a new signed run token for the same workspace and turn but with that authority, prepares grant-based CLI environment variables, and asks the sandbox object to authorize with the new token while replacing relevant CLI variables.

**Call relations**: _run_turn creates a `SandboxAuthorizer` and passes its `authorize` method into `TurnEngine` for normal turns. The engine can call it when sandbox work needs authority-specific access.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### Agent execution loop
The runtime engine and harness agent loop drive model conversations, tool calls, durable recording, billing, and final turn results.

### `core/src/ufo/runtime/engine.py`

`orchestration` · `turn execution`

This file is the engine room for a single agent “turn,” meaning one unit of work in a conversation. A turn may be a normal chat request, a scheduled job, a subagent’s private task, or a direct tool intent from a UI panel. The engine claims the turn from the database so only one worker owns it, loads the transcript, applies prompt hooks, asks the model for rounds of output, runs any requested tools, absorbs new messages that arrived while it was working, and finally commits a terminal result.

A key idea here is durable replay. Expensive or risky steps, such as calling the model, dispatching a tool, draining incoming messages, and compacting context, are recorded through DBOS, a workflow system that can replay recorded step results after a crash. This is like writing receipts after each important errand: after a restart, the worker reads the receipts instead of doing the errand again.

The file also protects boundaries. It checks spending limits and member seats, binds tool calls to the right member authority, walls off untrusted tool output, offloads huge text and images so the model context and workflow logs stay small, and writes transcripts carefully so later turns do not repeat already completed work. Without this file, the agent would have no reliable way to turn a request into a safe, billed, persisted, user-visible answer.

#### Function details

##### `_claim_turn`  (lines 297–344)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued, parked, or crash-replayed turn for one workflow attempt. This prevents two workers from running the same turn at the same time.

**Data flow**: It receives a turn id and attempt id, reads the turn and locks its conversation, then updates the turn to running only if the status and attempt match the safe cases. It returns whether this was a fresh claim, a replay adoption, or no claim at all.

**Call relations**: TurnEngine._mark_running calls this at the start of normal and intent turns. Its answer decides whether the engine runs the turn or falls back to repair for an already-finished or still-owned turn.

*Call graph*: called by 1 (_mark_running); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_activity_goal`  (lines 431–432)

```
def _activity_goal(requesters: Mapping[UUID, ActiveMessage]) -> str
```

**Purpose**: Builds the short goal text used when summarizing tool activity for users. It turns the currently active member messages into readable text.

**Data flow**: It receives active request messages, extracts member-facing text from each rendered message, and joins them into one string. It does not change stored state.

**Call relations**: _RuntimeTools.prepare and TurnEngine.run_intent use it just before starting activity summaries, so tool progress labels reflect what users asked for.

*Call graph*: called by 2 (run_intent, prepare); 1 external calls (member_message_text).


##### `_RoundInput.__repr__`  (lines 447–452)

```
def __repr__(self) -> str
```

**Purpose**: Produces a compact debug string for a model round input. It avoids dumping full prompts or messages into logs.

**Data flow**: It reads counts and flags from the round input and returns a short text summary. No external state changes.

**Call relations**: It is used implicitly when round input objects are logged or inspected, especially around DBOS step execution.


##### `EffectiveCall.parallel_safe`  (lines 473–477)

```
def parallel_safe(self) -> bool
```

**Purpose**: Says whether a resolved tool call can run in parallel with neighboring calls. This comes from the actual resolved tool declaration.

**Data flow**: It reads the tool’s parallel-safety flag and returns it as a boolean. It makes no changes.

**Call relations**: _RuntimeTools.parallel_safe relies on this after resolving a model’s tool call, so the harness can group calls without violating tool ordering rules.


##### `EffectiveCall.semantic_call`  (lines 479–490)

```
def semantic_call(self) -> ToolUseBlock
```

**Purpose**: Returns the tool call under the identity that should matter to the system. For bound object actions, it replaces the generic wire name with the specific action name and validated action input.

**Data flow**: It reads the original call and resolved action information, then returns either the original call or a copied call with semantic name and input. It does not mutate the original.

**Call relations**: Activity summaries, final-act parsing, and dispatch metrics use this so they talk about the real action, not just the generic dispatcher.


##### `EffectiveCall.meter_dimensions`  (lines 492–502)

```
def meter_dimensions(self) -> dict[str, str]
```

**Purpose**: Builds the labels used for tool metrics. It makes dashboards group work by the real call or bound action.

**Data flow**: It reads the resolved tool, bound-action metadata, and extension context, then returns a dictionary of metric labels. Nothing is persisted.

**Call relations**: TurnEngine._bind_or_error, _dispatch_step, and rejection paths pass these labels into _meter_dispatch to keep observability meaningful.


##### `EffectiveCall.__repr__`  (lines 504–505)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug representation of a resolved tool call. It keeps logs readable by showing identity rather than full inputs.

**Data flow**: It reads the semantic call id and model call id, then returns a short string. It changes nothing.

**Call relations**: Used implicitly by logging and debugging when resolved calls appear in traces.


##### `_BoundToolCall.call`  (lines 515–516)

```
def call(self) -> ToolUseBlock
```

**Purpose**: Gives convenient access to the original model tool call inside a bound dispatch. It hides one layer of wrapping.

**Data flow**: It reads the effective call stored in the bound object and returns its ToolUseBlock. No changes occur.

**Call relations**: Dispatch preparation and execution use this property whenever they need the model-assigned call id and raw call name.


##### `_BoundToolCall.__repr__`  (lines 518–519)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug string for a bound tool call. It avoids printing large inputs or contexts.

**Data flow**: It reads the call name and call id and returns a compact string. It has no side effects.

**Call relations**: Used implicitly by logs, traces, and DBOS diagnostics when bound calls are represented.


##### `_RejectedToolCall.parallel_safe`  (lines 531–534)

```
def parallel_safe(self) -> bool
```

**Purpose**: Marks invalid tool calls as not safe to run in parallel. A bad call becomes an ordering barrier so the transcript keeps the model’s intended order.

**Data flow**: It always returns false. It reads no outside data and changes nothing.

**Call relations**: _RuntimeTools.parallel_safe can treat resolved calls and rejected calls uniformly because rejected calls expose this property.


##### `_RejectedToolCall.__repr__`  (lines 536–540)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug string for a rejected call. It shows the failed tool, call id, outcome, and error class.

**Data flow**: It reads fields from the rejected call and returns a string. No state changes.

**Call relations**: Used implicitly when rejected calls are logged or recorded around dispatch.


##### `_Burn.segments`  (lines 615–624)

```
def segments(self, serving: str, attempt: str, usage_events: Sequence[Usage]) -> tuple[_Segment, ...]
```

**Purpose**: Splits a turn’s token usage across the model accounts that served it. This lets billing charge each part at the right model and ledger series.

**Data flow**: It receives the current model, attempt id, and usage events, cuts the events at recorded account-switch points, totals each slice, and returns segment records. It does not write billing itself.

**Call relations**: TurnEngine._priced and _record_usage use these segments to compute cost and write ledger rows, especially after rate-limit failover.

*Call graph*: calls 1 internal fn (_total_usage); 1 external calls (__init__).


##### `ModelStreamError.__init__`  (lines 679–680)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Creates an exception that preserves a model stream failure plus any partial output. This lets the engine bill usage and optionally salvage truncated content.

**Data flow**: It receives the provider error class, message, and partial output, then stores them in the exception arguments. It does not log or persist by itself.

**Call relations**: TurnEngine._stream_recovering_overflow raises this after a recorded stream step reports an error instead of throwing inside the DBOS step.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 682–684)

```
def __str__(self) -> str
```

**Purpose**: Formats the model stream error for logs and terminal frames. It includes the provider’s error class and message but not the partial output.

**Data flow**: It reads the stored exception arguments and returns a human-readable string. It leaves the exception unchanged.

**Call relations**: Commit and error handling use normal exception string behavior, so this controls what users and logs see.


##### `ModelStreamError.model_error_class`  (lines 687–689)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original model provider error class. This keeps provider-specific failures recognizable after wrapping.

**Data flow**: It reads the first stored exception argument and returns it. No state changes.

**Call relations**: TurnEngine._commit_once uses it for terminal error metadata, and _RuntimeModel.stream uses it to detect recoverable truncation.


##### `ModelStreamError.partial_output`  (lines 692–694)

```
def partial_output(self) -> str
```

**Purpose**: Returns text that arrived before a failed model stream stopped. This can be saved so the agent can recover work instead of regenerating it.

**Data flow**: It reads the stored partial output from the exception and returns it. It does not write files itself.

**Call relations**: _RuntimeModel.stream reads this when handling truncation and may ask TurnEngine._offload to save it in the sandbox.


##### `ModelStreamError.model_error_message`  (lines 697–699)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original model provider error message. This keeps useful failure detail available after wrapping.

**Data flow**: It reads the stored message argument and returns it. Nothing else changes.

**Call relations**: TurnEngine._commit_once uses this when building the terminal error frame.


##### `TurnParked.__init__`  (lines 706–708)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates the special exception used when a turn must pause instead of finish. Parking is used for spend caps or revoked seats so work can resume later.

**Data flow**: It receives a member-facing message, stores it on the exception, and makes it available to the parking path. It does not update the database itself.

**Call relations**: Spend and seat checks raise this; TurnEngine.run and run_intent catch it and call _park.

*Call graph*: called by 3 (_enforce_authority_seat, _enforce_seats, _enforce_spend).


##### `_intent_admits`  (lines 714–719)

```
def _intent_admits(tool: ToolDef) -> bool
```

**Purpose**: Decides whether a prepared UI intent is allowed to call a given tool. It limits member-submitted intents to object CRUD tools or tools that explicitly present themselves for that lane.

**Data flow**: It reads the tool name and presentation marker and returns true or false. It changes no state.

**Call relations**: TurnEngine.run_intent uses it after resolving a member intent; if it returns false, the intent is converted into a rejected tool call.

*Call graph*: called by 1 (run_intent).


##### `_context_tag`  (lines 728–746)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the metadata block placed before a member message. It tells the model when the message was admitted, who sent it, and what surface context came with it.

**Data flow**: It receives a message id, optional turn context, and admission time, formats them using the sender’s timezone when available, and returns a text tag. It does not persist anything.

**Call relations**: TranscriptRepair.load_messages uses it for founding messages, and TurnEngine._render_arrival uses it for messages drained mid-turn.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 749–754)

```
def _bounded(content: str) -> str
```

**Purpose**: Cuts overly long tool text down to the maximum size allowed in model context. It adds a notice explaining how much was removed.

**Data flow**: It receives text and either returns it unchanged or returns a shortened version plus a truncation marker. No external state changes.

**Call relations**: TurnEngine._finish_dispatch uses it for error output or fallback cases when full offload fails.

*Call graph*: called by 1 (_finish_dispatch).


##### `_meter_dispatch`  (lines 757–791)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str, semantic: Mapping[str, str] | None=None) -> None
```

**Purpose**: Records metrics for one tool dispatch. It tells operators which tools ran, how long they took, and how they ended.

**Data flow**: It receives registry information, the call, start time, outcome, error class, profile, and semantic labels; it emits a count and duration measurement. It returns nothing.

**Call relations**: TurnEngine._bind_or_error and _dispatch_step call this when binding or dispatch ends, including success, invalid calls, hook denials, and infrastructure failures.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 794–837)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedRef, ...]]
```

**Purpose**: Finds which skills have already been successfully loaded in the visible conversation window. This prevents reloading skill instructions that are still in context.

**Data flow**: It scans messages for completed load-skill tool calls, checks their results were complete, asks the skill registry for each closure, and yields those closures. It ignores stale or unreadable loads.

**Call relations**: TurnEngine._reseed_loaded_skills calls it after compaction or arrival changes so the compaction tracker knows what skill text is already present.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 840–862)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Parses a structured final act when the last tool call in a round successfully produced it. It is used for acts that only count if they end the round, such as a final question.

**Data flow**: It receives tool calls, results, a tool name, and a payload model; it reads the last call/result, parses JSON from the result text, validates it, and returns the payload or none. It does not modify messages.

**Call relations**: _round_acts calls this for declarations whose final-act rule says the act must be the last call.

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_pending_act`  (lines 865–893)

```
def _pending_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Parses a structured act that remains pending even if other work happened after it. This is used for things like credential or connection requests that only the member can satisfy.

**Data flow**: It receives round calls, results, a tool name, and a payload model; it searches successful matching calls from newest to oldest, parses and validates the result JSON, and returns the newest valid payload or none.

**Call relations**: _round_acts calls this for final-act declarations whose rule is “pending act.”

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_round_acts`  (lines 896–927)

```
def _round_acts(resolved: tuple[_Resolution, ...], results: tuple[ToolResultBlock, ...]) -> dict[str, BaseModel]
```

**Purpose**: Collects structured requests left open by a completed tool round. It translates successful final-act tool results into typed terminal-frame fields.

**Data flow**: It receives resolved calls and tool results, converts bound calls to semantic names, applies pending-act and last-call rules, and returns a dictionary of frame-field names to typed payloads.

**Call relations**: _RuntimeTools.after_round uses it during normal model turns, and TurnEngine.run_intent uses it after a direct intent dispatch.

*Call graph*: calls 2 internal fn (_final_act, _pending_act); called by 2 (run_intent, after_round).


##### `_act`  (lines 930–934)

```
def _act(acts: dict[str, BaseModel], frame_field: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Safely extracts one typed act payload from the act dictionary. It prevents accidentally using a payload under the wrong type.

**Data flow**: It receives an act dictionary, field name, and expected model class; it returns the payload only if it is an instance of that class. It changes nothing.

**Call relations**: _RuntimeTools.after_round and TurnEngine.run_intent use it to populate question, credential request, and connection request fields.

*Call graph*: called by 2 (run_intent, after_round).


##### `_created_refs`  (lines 937–969)

```
def _created_refs(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> tuple[ObjectRef, ...]
```

**Purpose**: Reads object references created by object-apply tool results. This lets terminals and resumed turns know which new objects already exist.

**Data flow**: It receives tool calls and results, matches successful object_apply results, parses their JSON, builds valid ObjectRef values for created objects, and returns them. Bad or rewritten results are skipped.

**Call relations**: TurnEngine._fold_created accumulates these during normal turns, and TurnEngine.run_intent includes them in intent terminal frames.

*Call graph*: called by 2 (_fold_created, run_intent); 2 external calls (__init__, loads).


##### `_total_usage`  (lines 972–980)

```
def _total_usage(usage_events: Sequence[Usage]) -> Usage
```

**Purpose**: Adds many token-usage events into one total usage record. It is the shared calculator for billing, cost display, and metrics.

**Data flow**: It receives usage events, sums each token category, and returns a new Usage object. It does not write the totals anywhere.

**Call relations**: _Burn.segments, TurnEngine._publish_cost, and TurnEngine._stream_once use it whenever they need a summarized view of model usage.

*Call graph*: called by 3 (_publish_cost, _stream_once, segments); 1 external calls (__init__).


##### `_to_harness_reasoning`  (lines 983–984)

```
def _to_harness_reasoning(block: ReasoningBlock) -> HarnessReasoning
```

**Purpose**: Converts a runtime reasoning block into the harness format. The harness is the generic agent runner used by this engine.

**Data flow**: It receives a runtime reasoning block, records its type and serialized payload, and returns a HarnessReasoning object. It changes no source block.

**Call relations**: _to_harness_message and _RuntimeModel.stream use it when passing reasoning content between the runtime and harness layers.

*Call graph*: called by 2 (stream, _to_harness_message); 2 external calls (__init__, model_dump).


##### `_from_harness_reasoning`  (lines 987–996)

```
def _from_harness_reasoning(block: HarnessReasoning) -> ReasoningBlock
```

**Purpose**: Converts a harness reasoning block back into the runtime model format. It preserves provider reasoning in the shape expected by model messages.

**Data flow**: It receives a HarnessReasoning value, looks at its kind, validates the payload into the matching runtime block type, and returns it. Unknown kinds raise an error.

**Call relations**: _from_harness_message calls it while translating completed harness conversations back into runtime messages.

*Call graph*: called by 1 (_from_harness_message); 3 external calls (model_validate, model_validate, model_validate).


##### `_to_harness_call`  (lines 999–1000)

```
def _to_harness_call(call: ToolUseBlock) -> HarnessToolCall
```

**Purpose**: Converts a runtime tool-use block into the harness tool-call format. This lets the generic harness understand model tool calls.

**Data flow**: It receives a ToolUseBlock and returns a HarnessToolCall with the same id, name, and input dictionary. It has no side effects.

**Call relations**: _to_harness_message and _RuntimeModel.stream use it when handing calls to the harness or returning model rounds.

*Call graph*: called by 2 (stream, _to_harness_message); 1 external calls (__init__).


##### `_from_harness_call`  (lines 1003–1004)

```
def _from_harness_call(call: HarnessToolCall) -> ToolUseBlock
```

**Purpose**: Converts a harness tool call back into the runtime format. This lets runtime code resolve and dispatch calls produced inside the harness.

**Data flow**: It receives a HarnessToolCall and returns a ToolUseBlock with matching id, name, and input. It does not persist anything.

**Call relations**: _RuntimeTools._resolve, _RuntimeTools.after_round, and _from_harness_message use it before runtime-specific processing.

*Call graph*: called by 3 (_resolve, after_round, _from_harness_message); 1 external calls (__init__).


##### `_to_harness_result`  (lines 1007–1024)

```
def _to_harness_result(result: ToolResultBlock) -> HarnessToolResult
```

**Purpose**: Converts a runtime tool result into the harness result format. It supports plain text as well as text-and-image block results.

**Data flow**: It receives a ToolResultBlock, converts nested text and image blocks as needed, and returns a HarnessToolResult with error and activity flags preserved.

**Call relations**: _RuntimeTools.execute uses it after dispatch, and _to_harness_message uses it when translating prior conversation history.

*Call graph*: called by 2 (execute, _to_harness_message); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_result`  (lines 1027–1044)

```
def _from_harness_result(result: HarnessToolResult) -> ToolResultBlock
```

**Purpose**: Converts a harness tool result back into runtime message blocks. This restores text and image data into the model-interface types.

**Data flow**: It receives a HarnessToolResult, converts content parts into runtime TextBlock and ImageBlock values when needed, and returns a ToolResultBlock. No state changes.

**Call relations**: _RuntimeTools.after_round and _from_harness_message use it after the harness finishes tool execution.

*Call graph*: called by 2 (after_round, _from_harness_message); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_message`  (lines 1047–1065)

```
def _to_harness_message(message: Message) -> HarnessMessage
```

**Purpose**: Converts one runtime chat message into the harness message format. It translates every supported block type.

**Data flow**: It receives a Message; if the content is text, it copies it directly, otherwise it converts each block and returns a HarnessMessage. It leaves the original message unchanged.

**Call relations**: _to_harness_messages calls it for whole conversations before entering the harness agent loop.

*Call graph*: calls 3 internal fn (_to_harness_call, _to_harness_reasoning, _to_harness_result); called by 1 (_to_harness_messages); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_message`  (lines 1068–1084)

```
def _from_harness_message(message: HarnessMessage) -> Message
```

**Purpose**: Converts one harness message back into the runtime format. It restores text, image, tool-call, tool-result, and reasoning blocks.

**Data flow**: It receives a HarnessMessage, converts content directly or block by block, and returns a runtime Message. It does not write state.

**Call relations**: _from_harness_messages calls it when the harness returns updated conversation history.

*Call graph*: calls 3 internal fn (_from_harness_call, _from_harness_reasoning, _from_harness_result); called by 1 (_from_harness_messages); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_messages`  (lines 1087–1088)

```
def _to_harness_messages(messages: tuple[Message, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Converts a whole tuple of runtime messages into harness messages. It is a small bridge between the runtime engine and generic harness.

**Data flow**: It receives runtime messages, converts each one, and returns a tuple of harness messages. No external state changes.

**Call relations**: TurnEngine._model_round, _RuntimeConversation.prepare, _RuntimeConversation.prepare_exhaust, and _RuntimeModel.stream use it at harness boundaries.

*Call graph*: calls 1 internal fn (_to_harness_message); called by 4 (_model_round, prepare, prepare_exhaust, stream).


##### `_from_harness_messages`  (lines 1091–1092)

```
def _from_harness_messages(messages: tuple[HarnessMessage, ...]) -> tuple[Message, ...]
```

**Purpose**: Converts a whole tuple of harness messages back into runtime messages. It is the return bridge from the harness to this engine.

**Data flow**: It receives harness messages, converts each one, and returns runtime messages. It changes no stored data.

**Call relations**: TurnEngine._model_round, _RuntimeConversation.prepare, checkpoint, prepare_exhaust, and _RuntimeModel.stream use it whenever harness state re-enters runtime logic.

*Call graph*: calls 1 internal fn (_from_harness_message); called by 5 (_model_round, checkpoint, prepare, prepare_exhaust, stream).


##### `_RuntimeConversation.prepare`  (lines 1111–1132)

```
async def prepare(self, messages: tuple[HarnessMessage, ...], round_index: int) -> HarnessPreparedRound
```

**Purpose**: Prepares the conversation before each model round. It absorbs new arrivals, checks spend, refreshes loaded skills, and compacts context if needed.

**Data flow**: It receives harness messages and a round index, converts messages to runtime format, appends newly claimed arrivals, enforces spend limits, compacts messages, records compaction usage, and returns a prepared harness round.

**Call relations**: The harness calls this between rounds through AgentEngine. It delegates arrival handling, spend checks, skill reseeding, and compaction back to TurnEngine.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); 2 external calls (__init__, span).


##### `_RuntimeConversation.checkpoint`  (lines 1134–1136)

```
async def checkpoint(self, messages: tuple[HarnessMessage, ...]) -> None
```

**Purpose**: Records a consistent conversation window after a round has completed. This is what an interrupted turn can safely persist.

**Data flow**: It receives harness messages, converts them into runtime messages, stores them in the engine’s window, and marks that this turn ran at least one completed round.

**Call relations**: The harness calls it after completed rounds so TurnEngine.run can later persist interrupted work without saving half-finished tool calls.

*Call graph*: calls 1 internal fn (_from_harness_messages).


##### `_RuntimeConversation.prepare_exhaust`  (lines 1138–1147)

```
async def prepare_exhaust(self, messages: tuple[HarnessMessage, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Prepares messages for the forced final answer path when the round limit is reached. It still checks spending and compacts context.

**Data flow**: It receives harness messages, enforces spend, compacts the runtime messages with active requests, records compaction usage, and returns compacted harness messages.

**Call relations**: AgentEngine uses it when exhausting the allowed round budget, before asking the model for a final response.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages).


##### `_RuntimeModel.stream`  (lines 1156–1211)

```
async def stream(self, request: HarnessModelRequest, round_index: int) -> HarnessModelRound
```

**Purpose**: Runs one model round for the harness using TurnEngine’s durable streaming logic. It also handles recoverable truncation by giving the harness corrective feedback.

**Data flow**: It receives a harness model request, converts messages and tools to runtime types, calls the engine’s streaming-with-recovery path, adds seat and cost checks, and returns a harness model round. On truncation, it may offload partial output and raise a recoverable model error.

**Call relations**: AgentEngine calls this whenever it needs the model. It hands off to TurnEngine._stream_recovering_overflow and then converts the result back for the harness.

*Call graph*: calls 5 internal fn (__init__, _from_harness_messages, _to_harness_call, _to_harness_messages, _to_harness_reasoning); 4 external calls (__init__, __init__, emit_metric, log).


##### `_RuntimeTools.definitions`  (lines 1224–1234)

```
def definitions(self) -> tuple[HarnessToolDefinition, ...]
```

**Purpose**: Lists the tools available to the harness in harness format. It includes the requested_by field only when needed.

**Data flow**: It reads the engine’s tool registry and active requester situation, builds harness tool definitions from runtime schemas, and returns them. No state changes.

**Call relations**: AgentEngine asks this wrapper what tools it may offer to the model during a normal turn.

*Call graph*: 1 external calls (__init__).


##### `_RuntimeTools.parallel_safe`  (lines 1236–1237)

```
def parallel_safe(self, call: HarnessToolCall) -> bool
```

**Purpose**: Tells the harness whether a tool call may run beside others. It resolves the call first so bound object actions use their real declaration.

**Data flow**: It receives a harness call, resolves or reuses its runtime resolution, and returns the parallel-safety flag. It may cache the resolution in round state.

**Call relations**: AgentEngine calls it while segmenting tool calls; it relies on _RuntimeTools._resolve and EffectiveCall or _RejectedToolCall.

*Call graph*: calls 1 internal fn (_resolve).


##### `_RuntimeTools.prepare`  (lines 1239–1255)

```
async def prepare(self, calls: tuple[HarnessToolCall, ...]) -> None
```

**Purpose**: Resolves and binds all tool calls before execution. It also starts user-visible activity summaries for valid bound calls.

**Data flow**: It receives harness calls, resolves each, binds each to the right ToolContext and requester authority, stores bindings by call id, and starts activity generation for real dispatches.

**Call relations**: AgentEngine calls it before executing a batch of tool calls. It delegates binding to TurnEngine._bind_or_error and uses _activity_goal for progress labels.

*Call graph*: calls 2 internal fn (_resolve, _activity_goal); 1 external calls (gather).


##### `_RuntimeTools.execute`  (lines 1257–1259)

```
async def execute(self, call: HarnessToolCall) -> HarnessToolResult
```

**Purpose**: Executes one already-prepared tool call for the harness. It returns the result in harness format.

**Data flow**: It receives a harness call, looks up the stored bound dispatch input, asks TurnEngine._dispatch to run it, converts the runtime result, and returns it.

**Call relations**: AgentEngine calls this for each prepared tool call, possibly in parallel depending on the earlier safety decision.

*Call graph*: calls 1 internal fn (_to_harness_result).


##### `_RuntimeTools.after_round`  (lines 1261–1287)

```
async def after_round(self, calls: tuple[HarnessToolCall, ...], results: tuple[HarnessToolResult, ...]) -> None
```

**Purpose**: Processes the results of a completed tool round. It records workspace-change targets, created objects, and any open structured acts.

**Data flow**: It receives harness calls and results, converts them to runtime types, updates change paths and created refs, parses final acts if every call has a result, and clears per-round caches afterward.

**Call relations**: AgentEngine calls it after tool execution. It hands created-object folding to TurnEngine._fold_created and parses acts through _round_acts and _act.

*Call graph*: calls 5 internal fn (_resolve, _act, _from_harness_call, _from_harness_result, _round_acts); 2 external calls (__init__, change_targets).


##### `_RuntimeTools.interrupted`  (lines 1289–1290)

```
def interrupted(self) -> None
```

**Purpose**: Clears a pending question when the current tool round is interrupted. A half-finished round should not leave a member question open.

**Data flow**: It reads the runtime tool state, replaces only the question field with none, and keeps other pending acts. It returns nothing.

**Call relations**: AgentEngine calls it when a round is interrupted, so later terminal construction does not expose stale last-call questions.

*Call graph*: 1 external calls (replace).


##### `_RuntimeTools._resolve`  (lines 1292–1298)

```
def _resolve(self, call: HarnessToolCall) -> _Resolution
```

**Purpose**: Resolves a harness call once and caches the answer for this round. This avoids resolving the same tool call differently in different phases.

**Data flow**: It receives a harness call, checks the resolution cache by call id, converts and resolves it through TurnEngine if absent, stores it, and returns the resolution.

**Call relations**: _RuntimeTools.parallel_safe, prepare, and after_round all call this so segmentation, binding, and act parsing agree.

*Call graph*: calls 1 internal fn (_from_harness_call); called by 3 (after_round, parallel_safe, prepare).


##### `_RuntimeEvents.speak`  (lines 1306–1307)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Forwards mid-turn marked replies from the harness to the engine’s delivery path. These are short pieces of assistant text aimed at members before the turn ends.

**Data flow**: It receives marked replies and a round number, then delegates to TurnEngine._speak. It returns nothing of its own.

**Call relations**: AgentEngine calls this when reply redaction finds speakable spans during a tool-using round.


##### `_RuntimeEvents.closing`  (lines 1309–1310)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Forwards marked replies from a closing answer back onto the live text stream. This prevents a terminal-only answer from appearing blank in streaming clients.

**Data flow**: It receives marked replies and delegates to TurnEngine._stream_closing_spans. It does not store state directly.

**Call relations**: AgentEngine calls it as the final answer is being closed, using the engine to publish missing live text deltas.


##### `_RuntimeEvents.exhausted`  (lines 1312–1319)

```
def exhausted(self) -> None
```

**Purpose**: Records that the turn hit its maximum number of model-tool rounds. It marks the incomplete reason and emits observability signals.

**Data flow**: It updates the meter’s incomplete reason, emits a metric, and logs the forced-final event. It returns nothing.

**Call relations**: AgentEngine calls it when round budget is exhausted so TurnEngine._commit can include the reason in the terminal frame.

*Call graph*: 2 external calls (emit_metric, log).


##### `TranscriptRepair.resolve`  (lines 1334–1358)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes an already-committed terminal for a duplicate or redelivered execution. This helps a waiting client finish even if the original worker crashed after committing.

**Data flow**: It reads the turn’s terminal from the database; if absent, returns none. If present, it validates the frame, persists inbound transcript fallback, publishes the terminal, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed calls this when _mark_running fails, meaning this execution should not run the turn itself.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 1360–1368)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the finished conversation transcript including the assistant’s final answer. This is the durable history future turns will read.

**Data flow**: It receives messages, answer, system prompt, and injected context, appends the assistant answer, and delegates the actual write. It returns nothing.

**Call relations**: TurnEngine._persist_transcript uses this after a done terminal or intent completion.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_interrupted`  (lines 1370–1386)

```
async def persist_interrupted(self, messages: tuple[Message, ...], ran: bool) -> None
```

**Purpose**: Writes the safe transcript for a turn that ended without a final answer. If the turn completed work, it adds a notice telling future turns not to repeat it.

**Data flow**: It receives the consistent message window and whether this turn ran, optionally appends an interrupted-turn notice, and writes the conversation. It does not commit terminal state.

**Call relations**: TurnEngine._persist_interrupted calls this after failures, cancellations, or non-done terminal outcomes when there is a message window to preserve.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 1388–1407)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves the founding inbound message and any absorbed arrivals when a turn does not produce a full transcript. It avoids losing user messages on failure paths.

**Data flow**: It receives optional arrival messages and an optional safe founding-denial message, loads or builds the founding conversation, appends arrivals, and writes it. It never persists partial assistant text.

**Call relations**: TranscriptRepair.resolve calls it during repair, and TurnEngine._persist_interrupted reaches it when no completed message window exists.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 1409–1418)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads prior conversation history and appends this turn’s founding user message. For member turns, it prefixes a context tag so the model sees admission details.

**Data flow**: It reads prior messages, builds the inbound content with _context_tag when appropriate, appends it as a user message, and returns the tuple. It does not write.

**Call relations**: TurnEngine._load_messages and TranscriptRepair.persist_inbound use it to build the model’s starting conversation.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 1420–1426)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads conversation history before this turn only. It avoids letting a replay read its own transcript write as prior context.

**Data flow**: It reads the transcript store; if missing or already at this turn’s sequence or later, it returns an empty tuple, otherwise stored messages. No state changes.

**Call relations**: TranscriptRepair.load_messages and persist_inbound use it when constructing the safe conversation history.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 1428–1454)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None, from_run: bool=False) -> None
```

**Purpose**: Writes a conversation snapshot to transcript storage with a few retries. This makes transcript persistence more tolerant of temporary storage failures.

**Data flow**: It receives messages and optional system/injected metadata, builds a Conversation record, tries to write it, logs failures, and sleeps before retrying. It returns after success or final failed attempt.

**Call relations**: persist_transcript, persist_interrupted, and persist_inbound all delegate their actual storage write here.

*Call graph*: called by 3 (persist_inbound, persist_interrupted, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 1488–1505)

```
def exited(self, status: str) -> None
```

**Purpose**: Records wall-clock and round-count metrics when this execution reaches an exit. It ensures each execution is counted only once.

**Data flow**: It receives an exit status, checks if it already ended, measures elapsed time, emits duration and round metrics, and marks itself ended. It returns nothing.

**Call relations**: TurnEngine._commit and exception paths call it so completed, failed, parked, cancelled, and preempted executions are visible in metrics.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 1596–1607)

```
def __post_init__(self) -> None
```

**Purpose**: Validates important wiring after the engine is created. It catches mismatched audiences and illegal subagent tool naming before a turn runs.

**Data flow**: It reads tool extension contexts, hook audience, output contract, and tool registry; it raises if the setup is inconsistent. It does not otherwise mutate state.

**Call relations**: Dataclass construction calls this automatically before TurnEngine.run or run_intent can be used.


##### `TurnEngine.__repr__`  (lines 1609–1613)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug representation of the engine. It identifies the turn, agent, and profile without dumping large dependencies.

**Data flow**: It reads ids and profile, then returns a short string. It changes nothing.

**Call relations**: Used implicitly in logs, traces, and debugging when a TurnEngine is represented.


##### `TurnEngine.profile`  (lines 1616–1619)

```
def profile(self) -> str
```

**Purpose**: Returns the telemetry profile for this turn, such as main, agent, or a subagent profile. Metrics use this to separate different kinds of work.

**Data flow**: It reads the turn’s subagent and spawned flags and returns the normalized profile string. No state changes.

**Call relations**: Many logging and metric paths read this property, including run startup, model metrics, dispatch metrics, and terminal commits.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 1621–1765)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal model-driven turn from start to finish. It is the main workflow body for conversation requests.

**Data flow**: It claims the turn, builds tool context, prepares messages, loops through model rounds and tool calls, absorbs arrivals, commits a terminal result, writes transcript and workspace changes, and publishes live frames. On parking, cancellation, preemption, or errors, it bills and preserves the safest durable state.

**Call relations**: This is the central orchestrator. It calls nearly every major helper in the file and is invoked by the higher-level turn workflow outside this file.

*Call graph*: calls 15 internal fn (_bill_cancelled, _commit, _mark_running, _model_round, _park, _persist_interrupted, _persist_transcript, _prepare_run, _publish, _publish_run (+5 more)); 9 external calls (__init__, __init__, __init__, __init__, __init__, partial, monotonic, emit_metric, log).


##### `TurnEngine._rank_find`  (lines 1767–1788)

```
async def _rank_find(self, usage_events: list[Usage], system: str, user: str) -> str
```

**Purpose**: Runs a small model completion used by tools that need ranking or finding. It tracks the extra usage so tool-driven model calls are billed.

**Data flow**: It receives accumulated usage, a system prompt, and user query, streams completion text from the current model, appends usage both to the turn and current dispatch usage bucket, and returns the text.

**Call relations**: The ToolContext created in TurnEngine.run exposes this as find, and _invoke_dispatch sets the context variable that makes usage attribution possible.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._prepare_run`  (lines 1790–1846)

```
async def _prepare_run(self, usage_events: list[Usage], meter: _TurnMeter, absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage], pending_guard: bool) -> _PreparedRun
```

**Purpose**: Builds the initial prompt state for a normal turn. It applies scheduled memory recall, prompt-submit hooks, denial handling, member skill injection, and founding requester tracking.

**Data flow**: It receives usage, meter, absorbed ids, requesters, and a pending-arrival guard; it returns prepared system text, messages, injected context, and possibly an already-committed terminal for denied prompts.

**Call relations**: TurnEngine.run calls it after claiming the turn. It uses transcript loading, hooks, _commit, publishing, and transcript persistence for early-denial cases.

*Call graph*: calls 7 internal fn (_commit, _load_messages, _persist_transcript, _publish_terminal, _record_workspace_changes, _repair, _scheduled_system); called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, escape, span).


##### `TurnEngine.run_intent`  (lines 1848–1982)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a direct tool intent without asking the model. This supports prepared UI actions and sandbox bridge calls.

**Data flow**: It claims the turn, parses the inbound intent into one tool call, enforces seats and intent policy, resolves and binds the call, dispatches it through the same durable tool path, commits success or failure, persists transcript, and publishes the terminal.

**Call relations**: This is the sibling entry flow to TurnEngine.run for intent turns. It reuses resolution, binding, dispatch, billing, parking, and terminal helpers.

*Call graph*: calls 19 internal fn (_bind_or_error, _commit, _dispatch_step_recovering, _enforce_seats, _load_messages, _mark_running, _park, _persist_transcript, _publish_terminal, _rejected (+9 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, model_validate_json, monotonic, emit_metric (+1 more)).


##### `TurnEngine._scheduled_system`  (lines 1984–2018)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. If memory search fails, it keeps the original prompt so the turn can still run.

**Data flow**: It receives system text, searches memory for the scheduled inbound request with a timeout, formats matches into safe escaped text, and returns the augmented or original system prompt.

**Call relations**: TurnEngine._prepare_run calls it only for scheduled admissions before prompt-submit hooks and transcript loading.

*Call graph*: called by 1 (_prepare_run); 5 external calls (__init__, timeout, escape, log, audience_subjects).


##### `TurnEngine._mark_running`  (lines 2020–2028)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn for the current engine attempt. It is the engine method wrapper around the database claim helper.

**Data flow**: It sends the turn id and attempt id to _claim_turn and returns true if a claim was obtained. It changes the database through that helper.

**Call relations**: TurnEngine.run and run_intent call it first; failure sends execution to _resolve_unclaimed instead of running duplicate work.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 2030–2031)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a TranscriptRepair helper bound to this turn. It keeps transcript-repair behavior separate from the full engine.

**Data flow**: It reads the engine’s turn, transcript, and hub fields and returns a TranscriptRepair object. It does not perform repair itself.

**Call relations**: _load_messages, _persist_transcript, _persist_interrupted, _prepare_run, and _resolve_unclaimed use it whenever transcript helper behavior is needed.

*Call graph*: called by 5 (_load_messages, _persist_interrupted, _persist_transcript, _prepare_run, _resolve_unclaimed); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 2033–2035)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the model’s starting messages under a tracing span. This is a thin wrapper around TranscriptRepair.

**Data flow**: It creates a repair helper, asks it to load messages, and returns them. It does not change the transcript.

**Call relations**: TurnEngine._prepare_run and run_intent call it before building prompts or persisting intent transcripts.

*Call graph*: calls 1 internal fn (_repair); called by 2 (_prepare_run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 2037–2126)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the generic harness loop for one full agent turn segment, including model calls, tool execution, and structured-output handling. It returns the final answer and any open member requests.

**Data flow**: It receives context, messages, usage, system prompt, arrival state, requesters, meter, change tracking, and created refs; it builds runtime adapters and optional finish-tool validation, runs AgentEngine, then converts the finished messages and acts back to runtime values.

**Call relations**: TurnEngine.run calls it inside its loop. It wires _RuntimeModel, _RuntimeTools, _RuntimeConversation, and _RuntimeEvents together.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); called by 1 (run); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__).


##### `TurnEngine._model_round.validate`  (lines 2070–2071)

```
def validate(call: HarnessToolCall) -> str
```

**Purpose**: Validates a subagent finish-tool payload against the required output contract. It turns a valid payload into canonical JSON.

**Data flow**: It receives a harness tool call, validates the call input with the output model, and returns serialized JSON. Validation errors bubble to the harness.

**Call relations**: TurnEngine._model_round passes it into HarnessStructuredOutput so AgentEngine can enforce structured subagent returns.


##### `TurnEngine._model_round.accept_prose`  (lines 2073–2082)

```
def accept_prose(text: str) -> str | None
```

**Purpose**: Accepts short free-form prose as a structured result when the output contract allows it. This gives simple subagent answers a convenient path.

**Data flow**: It receives text, checks contract type and length, validates it as a result field, and returns JSON if valid or none if not accepted.

**Call relations**: TurnEngine._model_round passes it to HarnessStructuredOutput, where the harness uses it before forcing a finish tool call.

*Call graph*: 1 external calls (freeform_result_contract).


##### `TurnEngine._fold_created`  (lines 2128–2156)

```
async def _fold_created(self, created: dict[ObjectRef, None], tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> None
```

**Purpose**: Accumulates objects created during tool rounds and writes the current set onto the turn row. This preserves created object names even if the turn later parks or fails.

**Data flow**: It receives the accumulator, calls, and results, extracts fresh created refs, updates the accumulator, and writes the full list to the database while the turn is non-terminal.

**Call relations**: _RuntimeTools.after_round calls it after each tool round, and later commits use the accumulated created refs.

*Call graph*: calls 1 internal fn (_created_refs); 2 external calls (update, workspace_tx).


##### `TurnEngine._absorb_arrivals`  (lines 2158–2218)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Adds queued inbound messages to the current model window. This lets a running turn respond to messages that arrived while it was thinking.

**Data flow**: It receives current messages, arrival log, absorbed ids, and optional requester map; it claims arrivals, appends rendered or denied user messages, updates requesters and absorbed ids, logs, and publishes absorbed frames for member messages.

**Call relations**: _RuntimeConversation.prepare calls it before each model round, using _claim_arrivals as the durable drain step.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 2220–2272)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Publishes mid-turn marked replies to members. It both writes delivery rows and sends live Reply frames.

**Data flow**: It receives marked replies and a round index, skips subagent turns, creates deterministic reply ids, inserts pending delivery rows if new, logs, and publishes Reply frames. Duplicate replay inserts are ignored.

**Call relations**: _RuntimeEvents.speak delegates here when the harness finds speakable spans during a tool-using round.

*Call graph*: calls 1 internal fn (_publish); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 2274–2284)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Publishes marked spans from a closing answer as live text deltas. This ensures streaming clients see the final answer text.

**Data flow**: It receives marked replies and publishes each reply text as a TextDelta. It does not write delivery rows.

**Call relations**: _RuntimeEvents.closing delegates here at the end of a model response.

*Call graph*: calls 1 internal fn (_publish); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 2286–2310)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Applies prompt-submit hooks to one queued arrival and formats it for the model. Denied messages become safe denial text instead of exposing the original body.

**Data flow**: It receives arrival id, body, context, speaker, and creation time, fires the hook, and returns either rendered content with context/injection or a denial string. It does not claim the row.

**Call relations**: TurnEngine._claim_arrivals calls it while building the memoized arrival batch.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 2313–2382)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably drains pending inbound messages for this turn. It is a DBOS step so crash replay reuses the same consumed batch and rendered hook results.

**Data flow**: It receives ids already absorbed, stamps eligible inbound rows as consumed by this turn, renders each row in sequence, logs the claim, and returns Arrival objects. For spawned turns, it only claims internal arrivals.

**Call relations**: TurnEngine._absorb_arrivals calls it before each round. Because it is memoized, replay does not re-fire hooks or consume different messages.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 7 external calls (__init__, model_validate, and_, or_, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 2384–2403)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrivals that were stamped but not safely absorbed back to pending. This protects messages after failures or cancellations.

**Data flow**: It receives absorbed ids, clears consumed_turn_id for this turn except those ids, and logs any failure without raising. It is best-effort.

**Call relations**: TurnEngine.run calls it on failure, DBOS cancellation, and preemption paths after billing or before exit.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._stream_recovering_overflow`  (lines 2405–2477)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, tool_schemas: tuple[ToolSchema, ...], tool_choice: str | None, round_index: int, offe
```

**Purpose**: Runs one model round and recovers once from context overflow by forcing compaction. Context overflow means the prompt was too large for the provider.

**Data flow**: It receives messages, usage, prompt, tool schemas, round options, and active requests; it streams with interruption retry, turns recorded stream errors into ModelStreamError, and on overflow compacts, records compaction usage, and retries once.

**Call relations**: _RuntimeModel.stream calls this for every model request. It delegates actual streaming to _stream_retrying_interruption.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_retrying_interruption); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._stream_retrying_interruption`  (lines 2479–2513)

```
async def _stream_retrying_interruption(self, round_input: _RoundInput, usage_events: list[Usage]) -> StreamResult
```

**Purpose**: Retries a model round once when the provider stream is interrupted mid-response. It still records and bills usage from failed attempts.

**Data flow**: It receives round input and usage events, calls _stream_once, appends usage, may fail over to another account on rate limit, retries transient interruption once, and returns the final StreamResult.

**Call relations**: _stream_recovering_overflow uses it both before and after forced compaction.

*Call graph*: calls 2 internal fn (_move_account, _stream_once); called by 1 (_stream_recovering_overflow); 2 external calls (emit_metric, log).


##### `TurnEngine._move_account`  (lines 2515–2530)

```
async def _move_account(self, usage_events: list[Usage]) -> bool
```

**Purpose**: Switches to another member model account after rate limiting. This lets the same round retry under a different account when available.

**Data flow**: It records the current model as a completed billing segment boundary, asks the serving model to move, logs success, and returns whether movement happened.

**Call relations**: _stream_retrying_interruption calls it when a stream result reports account rate limiting.

*Call graph*: called by 1 (_stream_retrying_interruption); 1 external calls (log).


##### `TurnEngine._priced`  (lines 2532–2538)

```
def _priced(self, usage_events: Sequence[Usage]) -> int
```

**Purpose**: Computes the current attempt’s cost in micro-dollars. It prices each account segment under the model that served that segment.

**Data flow**: It receives usage events, splits them through the burn tracker, asks pricing for each segment cost, and returns the sum. It does not write billing.

**Call relations**: _enforce_spend and _publish_cost call it to check caps and display live cost.

*Call graph*: called by 2 (_enforce_spend, _publish_cost).


##### `TurnEngine._enforce_spend`  (lines 2540–2580)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks whether the turn may continue spending before another model round. If limits or balance are exceeded, it parks the turn instead of losing work.

**Data flow**: It receives usage so far and active requesters, enforces seats, prices pending spend, checks workspace balance and applicable spend caps, and raises TurnParked if not allowed. Otherwise it returns normally.

**Call relations**: _RuntimeConversation.prepare and prepare_exhaust call it before model work so TurnEngine.run can catch TurnParked and call _park.

*Call graph*: calls 3 internal fn (_enforce_seats, _priced, __init__); 6 external calls (__init__, __init__, workspace_tx, applicable_caps_absent, balance_absent, audience_member).


##### `TurnEngine._enforce_seats`  (lines 2582–2591)

```
async def _enforce_seats(self, requesters: Mapping[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks that all active member requesters still have seats in the workspace. Revoked access parks the turn.

**Data flow**: It collects member ids from requesters and on-behalf-of authority, queries seat status in the database, and raises TurnParked if any are not seated. No return value on success.

**Call relations**: _enforce_spend calls it for normal rounds, and run_intent calls it before direct tool dispatch.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_enforce_spend, run_intent); 2 external calls (__init__, workspace_tx).


##### `TurnEngine._enforce_authority_seat`  (lines 2593–2599)

```
async def _enforce_authority_seat(self, authority: ExecutionAuthority) -> None
```

**Purpose**: Checks the seat for the specific member authority behind a tool call. This protects tool execution after requester binding.

**Data flow**: It receives an execution authority, extracts a member id if present, checks that member’s seat, and raises TurnParked if revoked. Non-member authority passes through.

**Call relations**: _invoke_dispatch calls it immediately before running the tool handler.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_invoke_dispatch); 3 external calls (__init__, workspace_tx, authority_member_id).


##### `TurnEngine._stream_once`  (lines 2602–2771)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Performs one durable model streaming call. It streams visible text live, collects tool calls, reasoning, usage, and errors into a memoized StreamResult.

**Data flow**: It receives round input, builds the provider ModelRequest with tools and cache settings, runs ModelRoundRunner, publishes text deltas, emits timing/token/cache metrics, and returns a StreamResult. Mid-stream provider errors are returned inside the result.

**Call relations**: _stream_retrying_interruption calls it. Because it is a DBOS step, crash replay gets the recorded result instead of calling the model again.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_stream_retrying_interruption); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, now, emit_histogram, emit_metric, emit_up_down_metric (+1 more)).


##### `TurnEngine._publish_cost`  (lines 2773–2786)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn’s live cost and token count so clients can show a running meter. Publish failures do not fail the turn.

**Data flow**: It receives usage events, totals tokens, prices spend, builds a CostTick frame, and publishes it. It does not commit billing.

**Call relations**: _RuntimeModel.stream calls it after each model round, before returning the round to the harness.

*Call graph*: calls 3 internal fn (_priced, _publish, _total_usage); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2788–2799)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skills already present in the model window. This avoids reloading skills that survived compaction.

**Data flow**: It receives messages, derives loaded skill closures from the message window plus preloaded skills, and reseeds the compaction tracker. It returns nothing.

**Call relations**: _RuntimeConversation.prepare and _stream_recovering_overflow call it after absorption or compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 1 (_stream_recovering_overflow).


##### `TurnEngine._resolve_call`  (lines 2801–2815)

```
def _resolve_call(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Turns a raw model tool call into either a real resolved call or a structured rejection. This gives later phases one consistent identity to use.

**Data flow**: It receives a ToolUseBlock, routes object_action calls to _resolve_action, otherwise looks up the tool registry, and returns EffectiveCall or _RejectedToolCall. It does not dispatch the tool.

**Call relations**: _RuntimeTools._resolve and run_intent use it before binding, scheduling, and act parsing.

*Call graph*: calls 2 internal fn (_rejected, _resolve_action); called by 1 (run_intent); 1 external calls (__init__).


##### `TurnEngine._resolve_action`  (lines 2817–2891)

```
def _resolve_action(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Resolves a generic object_action call into the specific bound action it names. It validates target shape, binding rules, grants, and action input.

**Data flow**: It receives a ToolUseBlock, parses ObjectActionInput, looks up the kind and action, checks instance/collection rules and grants, validates input, and returns EffectiveCall or rejection. It may include validated action args.

**Call relations**: _resolve_call delegates here for object_action calls, and later dispatch uses the semantic action identity it produces.

*Call graph*: calls 1 internal fn (_rejected); called by 1 (_resolve_call); 3 external calls (__init__, model_validate, replace).


##### `TurnEngine._rejected`  (lines 2893–2905)

```
def _rejected(self, call: ToolUseBlock, error: Exception, dimensions: Mapping[str, str] | None=None) -> _RejectedToolCall
```

**Purpose**: Builds a rejected-tool-call object from an exception. This lets invalid calls flow through dispatch as normal tool results instead of crashing the turn.

**Data flow**: It receives the call, error, and optional metric labels, formats error text, chooses an outcome, and returns _RejectedToolCall. It writes nothing.

**Call relations**: _resolve_call, _resolve_action, _bind_or_error, and run_intent use it whenever a call cannot safely become a real dispatch.

*Call graph*: called by 4 (_bind_or_error, _resolve_action, _resolve_call, run_intent); 1 external calls (__init__).


##### `TurnEngine._bind_or_error`  (lines 2907–2946)

```
async def _bind_or_error(self, context: ToolContext, item: _Resolution, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Binds a resolved call to the correct requester authority and tool context, or converts binding failures into rejected calls. Binding decides whose permissions and sandbox apply.

**Data flow**: It receives a base ToolContext, a resolution, and active requesters; rejected calls pass through, valid calls go through requester binding, and failures become _RejectedToolCall except cancellation or terminal loss cases that must raise.

**Call relations**: _RuntimeTools.prepare and run_intent call it before dispatch. It calls _bind_requester and records dispatch metrics for exceptional binding exits.

*Call graph*: calls 4 internal fn (_bind_requester, _member_refs, _rejected, _meter_dispatch); called by 1 (run_intent); 5 external calls (__init__, __init__, meter_dimensions, replace, monotonic).


##### `TurnEngine._dispatch`  (lines 2948–2954)

```
async def _dispatch(self, bound: _DispatchInput, usage_events: list[Usage] | None=None) -> ToolResultBlock
```

**Purpose**: Runs a prepared dispatch and returns a runtime ToolResultBlock. It wraps durable step execution plus result rehydration.

**Data flow**: It receives a bound or rejected dispatch input and optional usage list, runs _dispatch_step_recovering, converts the DispatchResult into a ToolResultBlock, and returns it.

**Call relations**: _RuntimeTools.execute calls it for normal model-requested tools.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step_recovering).


##### `TurnEngine._dispatch_result`  (lines 2956–2988)

```
async def _dispatch_result(self, result: DispatchResult) -> ToolResultBlock
```

**Purpose**: Turns a serialized DispatchResult into the model-facing ToolResultBlock. It rehydrates image blobs only at this point.

**Data flow**: It receives text, error flag, activity flag, and optional image references; it fetches image bytes from the blob store when needed, builds text/image blocks, records the activity result id, and returns the tool result.

**Call relations**: _dispatch calls it after durable dispatch execution so DBOS logs do not carry large image bytes.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._dispatch_step_recovering`  (lines 2990–3000)

```
async def _dispatch_step_recovering(self, bound: _DispatchInput, usage_events: list[Usage] | None) -> DispatchResult
```

**Purpose**: Repeats dispatch steps when an interrupted live dispatch needs a fresh recorded step. It stops when a non-interrupted DispatchResult is accepted.

**Data flow**: It receives bound input and optional usage events, calls _dispatch_step with any resume target, passes each result to _accept_dispatch_result, and returns the first accepted result.

**Call relations**: _dispatch and run_intent use it as the safe wrapper around the DBOS dispatch step.

*Call graph*: calls 2 internal fn (_accept_dispatch_result, _dispatch_step); called by 2 (_dispatch, run_intent).


##### `TurnEngine._accept_dispatch_result`  (lines 3002–3013)

```
def _accept_dispatch_result(self, result: DispatchResult, usage_events: list[Usage] | None) -> bool
```

**Purpose**: Decides whether a dispatch result is final or represents live cancellation that must be retried or raised. It also accounts for tool-internal model usage.

**Data flow**: It receives a DispatchResult and optional usage list, checks whether this execution actually ran the step body, appends usage for replayed recorded results, raises cancellation for live interruptions, and returns whether to accept the result.

**Call relations**: _dispatch_step_recovering calls it after each _dispatch_step result.

*Call graph*: called by 1 (_dispatch_step_recovering).


##### `TurnEngine._bind_requester`  (lines 3015–3069)

```
async def _bind_requester(self, context: ToolContext, item: EffectiveCall, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Chooses which member authority a tool call acts for and returns a context customized for that authority. It honors requested_by when needed.

**Data flow**: It receives context, effective call, and active requesters, removes requested_by from tool input, validates message refs, chooses member or on-behalf authority, selects the matching sandbox/spawn controls, and returns updated context plus updated call.

**Call relations**: _bind_or_error calls it before creating _BoundToolCall. It uses _own_member and can influence later seat checks and tool permissions.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error); 4 external calls (__init__, replace, authority_from_member_id, UUID).


##### `TurnEngine._own_member`  (lines 3071–3079)

```
def _own_member(self, requesters: Mapping[UUID, ActiveMessage]) -> UUID | None
```

**Purpose**: Detects whether the current audience is a single member who has an active message in the turn. In that case, omitted requested_by can safely mean that member.

**Data flow**: It receives active requesters, reads the audience member, checks whether that member authored an active message, and returns the member id or none. It changes no state.

**Call relations**: _bind_requester uses it to infer authority, and _member_refs uses it to decide whether requested_by refs are needed.

*Call graph*: called by 2 (_bind_requester, _member_refs); 1 external calls (audience_member).


##### `TurnEngine._member_refs`  (lines 3081–3087)

```
def _member_refs(self, requesters: Mapping[UUID, ActiveMessage]) -> tuple[UUID, ...]
```

**Purpose**: Lists active member message refs that tools may use in requested_by. It omits refs in a one-member conversation where they add no information.

**Data flow**: It receives requesters, checks _own_member, and returns either an empty tuple or the ids of active messages with member authors. No state changes.

**Call relations**: _bind_or_error uses it for helpful errors, and tool schema generation uses the same idea to include or omit requested_by.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error).


##### `TurnEngine._offload`  (lines 3089–3114)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text to the sandbox’s tool-output directory and returns a display path. This keeps huge tool output or salvaged model text out of the prompt.

**Data flow**: It receives a file name and content, ensures the output directory exists, writes bytes to a runtime file, logs failures, and returns a display path or none. Callers degrade if it fails.

**Call relations**: _finish_dispatch uses it for oversized tool output, and _RuntimeModel.stream may use it when salvaging truncated model output.

*Call graph*: called by 1 (_finish_dispatch); 2 external calls (emit_metric, log).


##### `TurnEngine._start_activity`  (lines 3116–3121)

```
def _start_activity(self, call: ToolUseBlock, goal: str) -> None
```

**Purpose**: Starts an asynchronous task to summarize a tool call as user-visible activity. This lets the UI show progress while tools run.

**Data flow**: It receives a semantic call and goal text, increments an activity sequence, creates a background task, stores it, and arranges cleanup when it completes.

**Call relations**: _RuntimeTools.prepare and run_intent call it before dispatching valid bound calls.

*Call graph*: calls 1 internal fn (_generate_activity); called by 1 (run_intent); 1 external calls (create_task).


##### `TurnEngine._generate_activity`  (lines 3123–3135)

```
async def _generate_activity(self, call: ToolUseBlock, goal: str, sequence: int) -> None
```

**Purpose**: Generates and publishes a short activity label for a tool call. It preserves publication order even if summaries finish out of order.

**Data flow**: It receives the call, goal, and sequence number, asks the summarizer for text, stores labels, waits under a lock until earlier labels are ready, then publishes Activity and subagent-run frames. It updates activity state.

**Call relations**: _start_activity creates this as a task, and _labeled later uses stored labels when writing transcripts.

*Call graph*: calls 2 internal fn (_publish, _publish_run); called by 1 (_start_activity); 1 external calls (__init__).


##### `TurnEngine._stop_activity`  (lines 3137–3139)

```
def _stop_activity(self) -> None
```

**Purpose**: Cancels any pending activity-summary tasks. Once a terminal is published, unfinished progress labels are no longer useful.

**Data flow**: It iterates active activity tasks and cancels each one. It does not wait for their completion here.

**Call relations**: _publish_terminal calls it after sending the terminal frame.

*Call graph*: called by 1 (_publish_terminal).


##### `TurnEngine._dispatch_step`  (lines 3142–3247)

```
async def _dispatch_step(self, bound: _DispatchInput, resume_target: ObjectActionTarget | None=None) -> DispatchResult
```

**Purpose**: Runs one tool dispatch as a durable DBOS step. It covers rejected calls, hooks, requester-gated preparation, handler execution, output bounding, image offload, usage capture, interruption recovery, and metrics.

**Data flow**: It receives bound or rejected input and optional resume target, records the call as live, prepares the dispatch, invokes the handler when allowed, finishes the output, returns a DispatchResult, or returns an interrupted checkpoint on cancellation. It meters the outcome in all exits.

**Call relations**: _dispatch_step_recovering calls it. Because it is memoized, replay does not re-run completed handlers.

*Call graph*: calls 4 internal fn (_finish_dispatch, _invoke_dispatch, _prepare_dispatch, _meter_dispatch); called by 1 (_dispatch_step_recovering); 3 external calls (__init__, monotonic, span).


##### `TurnEngine._prepare_dispatch`  (lines 3249–3345)

```
async def _prepare_dispatch(self, bound: _BoundToolCall, target: ObjectActionTarget | None) -> _DispatchGate
```

**Purpose**: Validates and gates a bound tool call before handler execution. It handles replay preemption, input validation, object target lookup, and pre-tool hooks.

**Data flow**: It receives a bound call and optional resume target, may return an immediate error result, or returns validated args, target, and context in a ready object. It does not call the handler.

**Call relations**: _dispatch_step calls it before _invoke_dispatch. It uses _pending_member_guidance and _redoes_on_replay during crash-adoption windows.

*Call graph*: calls 2 internal fn (_pending_member_guidance, _redoes_on_replay); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, log).


##### `TurnEngine._invoke_dispatch`  (lines 3347–3404)

```
async def _invoke_dispatch(self, bound: _BoundToolCall, ready: _DispatchReady, find_usages: list[Usage]) -> _HandlerOutput
```

**Purpose**: Actually calls the tool handler with the prepared context and arguments. It captures text, images, untrusted status, handler errors, and tool-internal find usage.

**Data flow**: It receives the bound call, ready dispatch data, and a usage bucket, checks the authority seat, builds handler context with extension and idempotency key, calls the handler, separates text and images, and returns a handler-output record. Exceptions become error outputs except terminal-loss cases.

**Call relations**: _dispatch_step calls it after preparation and before _finish_dispatch.

*Call graph*: calls 1 internal fn (_enforce_authority_seat); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, replace).


##### `TurnEngine._finish_dispatch`  (lines 3406–3472)

```
async def _finish_dispatch(self, ready: _DispatchReady, handled: _HandlerOutput, find_usages: list[Usage]) -> DispatchResult
```

**Purpose**: Turns raw handler output into a durable DispatchResult. It bounds or offloads text, walls untrusted content, fires post hooks, shrinks and stores images, and attaches tool-internal usage.

**Data flow**: It receives ready dispatch data, handler output, and find usage; it edits content according to error/size/trust rules, fires success or failure hooks, writes image blobs when present, and returns DispatchResult.

**Call relations**: _dispatch_step calls it after _invoke_dispatch. Later _dispatch_result rehydrates any stored image references.

*Call graph*: calls 3 internal fn (_bounded_image, _offload, _bounded); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, wall).


##### `TurnEngine._redoes_on_replay`  (lines 3474–3481)

```
def _redoes_on_replay(self, tool: ToolDef) -> bool
```

**Purpose**: Says whether a tool should be preempted during crash replay if fresh member guidance is waiting. Read-only tools can be safely skipped and reissued; side-effecting tools rely on idempotency and should continue.

**Data flow**: It reads the resolved tool’s side-effecting flag and returns true for tools whose re-execution would redo work. It changes nothing.

**Call relations**: _prepare_dispatch uses it during adoption replay before deciding whether queued member guidance should block a re-run call.

*Call graph*: called by 1 (_prepare_dispatch).


##### `TurnEngine._pending_member_guidance`  (lines 3483–3499)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether an unconsumed member message is waiting in the conversation. This can stop replayed read-only work from running ahead of new user guidance.

**Data flow**: It queries the inbound-message table for one pending member row in this conversation and returns true if found. It does not claim the message.

**Call relations**: _prepare_dispatch calls it during adoption replay after _redoes_on_replay says preemption is allowed.

*Call graph*: called by 1 (_prepare_dispatch); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 3501–3529)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Downscales oversized tool-result images before they are sent back to the model. This avoids provider limits and wasted image tokens.

**Data flow**: It receives an ImageBlock with base64 data, decodes and opens it, shrinks it if the largest edge exceeds the limit, re-encodes it, and returns a new ImageBlock. If image processing fails, it logs and returns the original image.

**Call relations**: _finish_dispatch calls it before writing successful tool-result images to the blob store.

*Call graph*: called by 1 (_finish_dispatch); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 3531–3603)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Reliably commits a terminal state for the turn, retrying database failures. It also records terminal metrics and execution timing.

**Data flow**: It receives status, usage, meter, answer or error, open acts, created refs, and arrival-guard options; it retries _commit_once until it gets a result, emits terminal metrics for real transitions, marks the meter exited, logs, and returns the frame or none.

**Call relations**: TurnEngine.run, _prepare_run, and run_intent call it for done, failed, and guarded-answer outcomes.

*Call graph*: calls 2 internal fn (_commit_once, exited); called by 3 (_prepare_run, run, run_intent); 4 external calls (sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._publish_terminal`  (lines 3605–3608)

```
async def _publish_terminal(self, frame: TerminalFrame) -> None
```

**Purpose**: Publishes the terminal frame to live listeners and mirrors subagent status when needed. It also stops background activity summaries.

**Data flow**: It receives a TerminalFrame, publishes it on this turn’s hub stream, publishes run status for lineage if applicable, and cancels activity tasks. It does not write the database.

**Call relations**: TurnEngine.run, _prepare_run, and run_intent call it after a terminal frame is committed.

*Call graph*: calls 3 internal fn (_publish, _publish_run, _stop_activity); called by 3 (_prepare_run, run, run_intent); 1 external calls (__init__).


##### `TurnEngine._record_workspace_changes`  (lines 3610–3623)

```
async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None
```

**Purpose**: Refreshes the workspace change record after a turn finishes. This lets the portal show files or targets changed by the turn.

**Data flow**: It receives accumulated target paths, builds a WorkspaceChangeRecorder for the sandbox conversation, and runs the scan/write. It returns nothing.

**Call relations**: TurnEngine.run calls it after terminal publication and transcript persistence; _prepare_run calls it for early denied prompts.

*Call graph*: called by 2 (_prepare_run, run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 3625–3734)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one transactional attempt to write the terminal frame and billing data. It optionally refuses to close if unabsorbed arrivals are still pending.

**Data flow**: It receives terminal details and guard options, locks the conversation when needed, checks pending arrivals, records usage, reads billed cost, builds a TerminalFrame, updates the turn if still non-terminal, and returns the frame plus whether this call committed it.

**Call relations**: _commit wraps this with retry and metrics. _record_usage is called inside the transaction.

*Call graph*: calls 1 internal fn (_record_usage); called by 1 (_commit); 10 external calls (__init__, model_validate, and_, or_, select, update, workspace_tx, log, log_error, read_turn_cost).


##### `TurnEngine._park`  (lines 3736–3765)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Moves a running turn into a parked, resumable state because a cap or seat check stopped it. It bills what was already spent and releases claimed arrivals.

**Data flow**: It receives a message and usage events, updates the turn to parked if still non-terminal, records usage, clears consumed arrivals, then publishes Parked and emits metrics. It returns nothing.

**Call relations**: TurnEngine.run and run_intent call it after catching TurnParked.

*Call graph*: calls 2 internal fn (_publish, _record_usage); called by 2 (run, run_intent); 5 external calls (__init__, update, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 3767–3776)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes a live frame for this turn without letting publish failures fail the turn. The database remains the source of truth.

**Data flow**: It receives a live frame, tries to send it through the hub, and logs any error. It returns nothing.

**Call relations**: Run startup, arrivals, replies, cost ticks, activity, parking, terminal publication, and closing spans all use this helper.

*Call graph*: called by 8 (_absorb_arrivals, _generate_activity, _park, _publish_cost, _publish_terminal, _speak, _stream_closing_spans, run); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 3778–3800)

```
async def _publish_run(self, activity: str='', status: str='') -> None
```

**Purpose**: Mirrors subagent activity or status onto the root turn stream. Main turns do nothing here.

**Data flow**: It receives optional activity text and status, checks lineage, builds a SubagentActivity frame, publishes it to the root turn, and logs failures. It does not affect turn state.

**Call relations**: TurnEngine.run, _generate_activity, and _publish_terminal call it so surfaces tailing the root turn can see child-agent progress.

*Call graph*: called by 3 (_generate_activity, _publish_terminal, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 3802–3818)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops sandbox commands left running after a deliberate workflow cancellation. It distinguishes user cancellation from worker preemption.

**Data flow**: It asks the sandbox to stop commands and logs any failure without changing the already-durable cancel outcome. It returns nothing.

**Call relations**: TurnEngine.run and run_intent call it on DBOS cancellation paths, not on ordinary preemption that may replay.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._record_usage`  (lines 3820–3835)

```
async def _record_usage(self, connection: AsyncConnection, usage_events: Sequence[Usage]) -> None
```

**Purpose**: Writes billing ledger rows for this attempt’s model usage. It respects account failover segments and BYOK pricing rules.

**Data flow**: It receives a database connection and usage events, splits usage through the burn tracker, and records each segment with workspace, turn, model, attempt, pricing, and BYOK flag. It returns nothing.

**Call relations**: _commit_once, _park, and _bill_cancelled call it whenever consumed tokens must be recorded.

*Call graph*: called by 3 (_bill_cancelled, _commit_once, _park); 1 external calls (record_turn_usage).


##### `TurnEngine._bill_cancelled`  (lines 3837–3847)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for a cancelled or preempted normal turn. It tries not to let billing trouble block cancellation handling.

**Data flow**: It receives usage events, opens a transaction, calls _record_usage, and logs any failure instead of raising. It returns nothing.

**Call relations**: TurnEngine.run calls it on DBOS cancellation, asyncio preemption, and some failure paths before releasing arrivals or persisting interrupted state.

*Call graph*: calls 1 internal fn (_record_usage); called by 1 (run); 2 external calls (workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 3849–3854)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles an execution that failed to claim the turn. It either republishes an already committed terminal or quietly exits while another worker owns the turn.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the unclaimed situation, returning a terminal frame or none. It does not run model or tools.

**Call relations**: TurnEngine.run and run_intent call it when _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 3856–3859)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes a completed transcript after adding activity labels to tool results. It is the engine-facing wrapper around TranscriptRepair.

**Data flow**: It receives messages, answer, system prompt, and injected context, labels tool results with activity text, then delegates to TranscriptRepair.persist_transcript. It returns nothing.

**Call relations**: TurnEngine.run, _prepare_run, and run_intent call it after successful or early-denied terminal commits.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 3 (_prepare_run, run, run_intent).


##### `TurnEngine._persist_interrupted`  (lines 3861–3865)

```
async def _persist_interrupted(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes the safest transcript for a turn that did not complete normally. It preserves completed work and founding inbound without saving partial assistant output.

**Data flow**: It receives messages; if present, it labels them and delegates interrupted persistence, otherwise it persists only inbound or denial fallback. It returns nothing.

**Call relations**: TurnEngine.run calls it in cancellation, failure, and non-done terminal paths.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 1 (run).


##### `TurnEngine._labeled`  (lines 3867–3892)

```
def _labeled(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Adds stored activity text to tool-result blocks before transcript persistence. This makes the saved transcript match what users saw live.

**Data flow**: It receives messages, walks message content, and copies tool-result blocks that had activity with their activity_text filled in from the activity label map. It returns the updated message tuple without mutating originals.

**Call relations**: _persist_transcript and _persist_interrupted call it before handing messages to TranscriptRepair.

*Call graph*: called by 2 (_persist_interrupted, _persist_transcript).


### `core/src/ufo/harness/agent.py`

`orchestration` · `active during each agent run, from the first model round through final answer or exhaustion`

This file is the harness for running one AI agent safely and predictably. Think of it like a meeting chairperson: it keeps the transcript, asks the model for the next move, allows approved tools to be used, records what happened, and decides when the meeting is over.

The file starts by defining the pieces that can appear in a conversation: plain text, images, tool calls, tool results, and model reasoning notes. It also defines settings such as the system prompt, the maximum number of rounds, and how many tool calls may run at once. Several Protocol classes act like contracts: a model must know how to stream one round, tools must know how to describe and run themselves, a conversation store must know how to prepare and save messages, and event observers may publish visible replies.

The main worker is AgentEngine. Its run method repeats rounds until the model gives a final text answer, calls a special structured-output finish tool, or the round limit is reached. If tools are requested, the engine runs them in safe batches, sends their results back as user messages, and continues. If the model gives an empty answer, it nudges it once. If the model uses the structured finish flow incorrectly, the engine turns that into feedback or an error. Without this file, the project would have model calls, tool calls, and transcripts, but no central set of rules tying them into a reliable agent conversation.

#### Function details

##### `ToolDefinition.__post_init__`  (lines 61–63)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that every tool has a usable name. A tool with a blank name could not be reliably called by the model or matched to an implementation.

**Data flow**: It receives a newly created ToolDefinition and reads its name. If the name is empty or only spaces, it raises an error; otherwise the object remains unchanged and ready to advertise to the model.

**Call relations**: This validation happens automatically when a ToolDefinition is created. Later, AgentEngine._stream collects tool definitions and depends on their names being meaningful so it can detect duplicates and route calls correctly.


##### `AgentDefinition.__post_init__`  (lines 74–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that the agent’s round and parallel-tool limits are positive. These limits are safety rails, so zero or negative values would make the run loop impossible or misleading.

**Data flow**: It receives a newly created AgentDefinition and reads max_rounds and max_parallel_calls. If either is less than one, it raises an error; otherwise the definition is accepted as usable configuration.

**Call relations**: This validation runs when the agent definition is built. AgentEngine.run later uses max_rounds to stop runaway conversations, and AgentEngine._tool_exchange uses max_parallel_calls to limit how many tool calls happen at once.


##### `RecoverableModelError.__init__`  (lines 126–128)

```
def __init__(self, feedback: str) -> None
```

**Purpose**: This creates an error that means a model round failed in a way the agent may recover from. It carries feedback text that can be added to the conversation so the model can try again.

**Data flow**: It takes a feedback string, stores it both as the normal exception message and as a feedback field. The result is an exception object that explains what the model should be told next.

**Call relations**: The runtime model layer can raise this when a model response is bad but fixable. AgentEngine.run catches it, appends the feedback as a user message, and starts the next round instead of ending the whole run.

*Call graph*: called by 1 (stream).


##### `AgentModel.stream`  (lines 134–134)

```
async def stream(self, request: ModelRequest, round_index: int) -> ModelRound
```

**Purpose**: This is the contract for asking the language model to produce one round of output. Implementations turn a complete ModelRequest into model text, tool calls, and any reasoning records.

**Data flow**: It receives the current request, including the system prompt, transcript, available tools, and round mode, plus the round index. It returns a ModelRound containing the updated model-side messages, visible text, requested tool calls, and optional reasoning.

**Call relations**: AgentEngine._stream builds the request and calls this method. The returned ModelRound then flows back to AgentEngine.run, which decides whether to close, run tools, recover, or continue.


##### `AgentTools.definitions`  (lines 142–142)

```
def definitions(self) -> tuple[ToolDefinition, ...]
```

**Purpose**: This is the contract for listing the tools the agent may offer to the model. Each definition tells the model the tool’s name, purpose, and expected input shape.

**Data flow**: It takes no arguments besides the tool provider itself and returns a tuple of ToolDefinition objects. Nothing is changed directly; it simply describes what is available for the next model request.

**Call relations**: AgentEngine._stream calls this during normal rounds. It checks the returned tool names for duplicates, may add a special finish tool, and passes the final list to the model.


##### `AgentTools.parallel_safe`  (lines 144–144)

```
def parallel_safe(self, call: ToolCall) -> bool
```

**Purpose**: This tells the engine whether a particular tool call is safe to run at the same time as other calls. This protects tools that touch shared state, like a file or browser session.

**Data flow**: It receives a ToolCall and returns true or false. That answer becomes a scheduling hint: safe calls may share a batch, while unsafe calls need more careful ordering.

**Call relations**: AgentEngine._tool_exchange passes this method to dispatch_segments. That helper uses the answer to split model-requested tool calls into batches before execution.


##### `AgentTools.prepare`  (lines 146–146)

```
async def prepare(self, calls: tuple[ToolCall, ...]) -> None
```

**Purpose**: This gives the tool layer a chance to get ready before a batch of tool calls runs. For example, it might open resources, validate shared assumptions, or reserve state.

**Data flow**: It receives the next batch of ToolCall objects. It performs any needed setup and returns nothing; the important output is that the tool environment is ready for execution.

**Call relations**: AgentEngine._tool_exchange calls this immediately before running each batch. After preparation, the engine calls AgentTools.execute for each call in that batch.


##### `AgentTools.execute`  (lines 148–148)

```
async def execute(self, call: ToolCall) -> ToolResult
```

**Purpose**: This is the contract for actually running one tool call requested by the model. It turns the model’s requested action into a ToolResult that can be fed back into the conversation.

**Data flow**: It receives one ToolCall with an id, name, and input dictionary. It performs the requested work and returns a ToolResult tied to the call id, containing either normal content or an error result.

**Call relations**: AgentEngine._tool_exchange runs this for each call in a prepared batch, using asyncio.gather so safe calls can happen concurrently. The collected ToolResult objects are then placed into the next user message.


##### `AgentTools.after_round`  (lines 150–152)

```
async def after_round(self, calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]) -> None
```

**Purpose**: This lets the tool layer clean up or record what happened after a model round’s tool calls have been attempted. It runs even if a tool execution raises an exception.

**Data flow**: It receives all calls requested in the round and the results collected before any failure stopped the batch. It may update external state, release resources, or log outcomes, and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this in a finally block, which means it is part of the cleanup path no matter how the tool exchange ends.


##### `AgentTools.interrupted`  (lines 154–154)

```
def interrupted(self) -> None
```

**Purpose**: This tells the tool layer that a previously prepared conversation indicates an interrupted final action. It gives tools a chance to mark that interruption or reset related state.

**Data flow**: It takes no extra input and returns nothing. Its effect is whatever the tool implementation needs to do when the conversation boundary reports an interrupted final act.

**Call relations**: AgentEngine.run calls this after AgentConversation.prepare returns a PreparedRound with interrupted_final_act set. It happens before the next model round begins.


##### `AgentConversation.prepare`  (lines 160–160)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: This is the contract for preparing the transcript before each model round. A conversation store can restore, trim, or adjust messages before the model sees them.

**Data flow**: It receives the current tuple of Message objects and the round index. It returns a PreparedRound containing the messages to use next and a flag saying whether a final action was interrupted.

**Call relations**: AgentEngine.run calls this at the start of every round. The returned messages become the input to AgentEngine._stream, and the interruption flag may trigger AgentTools.interrupted.


##### `AgentConversation.checkpoint`  (lines 162–162)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: This is the contract for saving the transcript after tool results have been added. It creates a recovery point so later work can resume from a known conversation state.

**Data flow**: It receives the updated messages after a tool exchange. It saves or records them as needed and returns nothing.

**Call relations**: AgentEngine.run calls this after AgentEngine._tool_exchange returns more messages instead of a final answer. This happens before the loop continues to the next model round.


##### `AgentConversation.prepare_exhaust`  (lines 164–164)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This is the contract for preparing the transcript when the agent has used all allowed rounds. It gives the conversation boundary a final chance to shape messages before forcing an answer.

**Data flow**: It receives the current messages and returns the messages that should be used for the exhaustion flow. It may leave them unchanged or adjust them for storage or recovery needs.

**Call relations**: AgentEngine._exhaust calls this when AgentEngine.run reaches the round limit. The returned messages are then followed by a final prompt or structured finish prompt.


##### `AgentEvents.speak`  (lines 170–170)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: This is the contract for publishing model replies that should be visible while the run is still continuing. It lets the outside world see progress before tools are executed.

**Data flow**: It receives marked replies, which are pieces of model text labeled for presentation, and the human-friendly round number. It emits or records them and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this before running tools, because the model may have said something visible while also requesting tool actions.


##### `AgentEvents.closing`  (lines 172–172)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: This is the contract for publishing the final visible reply when the run is ending normally. It separates final output events from mid-run speech.

**Data flow**: It receives the marked replies from the closing model text. It emits or records them and returns nothing.

**Call relations**: AgentEngine._close calls this for normal unstructured final answers, and AgentEngine._exhaust calls it after forcing a no-tools final answer when the round budget is exhausted.


##### `AgentEvents.exhausted`  (lines 174–174)

```
def exhausted(self) -> None
```

**Purpose**: This is the contract for reporting that the agent ran out of allowed rounds. It lets observers mark the run as budget-limited before a forced answer is requested.

**Data flow**: It takes no extra data and returns nothing. Its effect is an outside notification that the normal round loop has been exhausted.

**Call relations**: AgentEngine._exhaust calls this as soon as the round limit is reached. The engine then tries to obtain a final answer anyway.


##### `MemoryConversation.prepare`  (lines 193–194)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: This simple local conversation boundary returns the transcript exactly as it was given. It is useful for tests or local runs that do not need persistence or transcript rewriting.

**Data flow**: It receives messages and a round index, ignores the round index, and wraps the same messages in a PreparedRound. No external state is read or changed.

**Call relations**: AgentEngine.run can use this as the default AgentConversation. It creates a PreparedRound so the engine can follow the same path it would use with a real conversation store.

*Call graph*: 1 external calls (__init__).


##### `MemoryConversation.checkpoint`  (lines 196–197)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: This default checkpoint does nothing. It exists so local runs can use the same engine interface without needing a database or storage layer.

**Data flow**: It receives the messages that would be saved, ignores them, and returns nothing. The transcript only continues in memory through the caller’s variable.

**Call relations**: AgentEngine.run calls this after tool exchanges when MemoryConversation is being used. Because it is a no-op, the engine simply continues with its in-memory messages.


##### `MemoryConversation.prepare_exhaust`  (lines 199–200)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This default exhaustion preparation leaves the transcript unchanged. It keeps the simple in-memory behavior consistent even when the agent reaches its round limit.

**Data flow**: It receives the current messages and returns the same messages. No storage, cleanup, or rewriting is performed.

**Call relations**: AgentEngine._exhaust calls this through the AgentConversation interface. With MemoryConversation, the forced-final-answer flow uses exactly the messages already in memory.


##### `NullEvents.speak`  (lines 207–208)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: This event observer intentionally publishes nothing for mid-run replies. It is the quiet default for runs that do not need streaming or user-visible progress events.

**Data flow**: It receives marked replies and a round number, ignores both, and returns nothing. No outside state changes.

**Call relations**: AgentEngine._tool_exchange may call this through the AgentEvents interface. With NullEvents, the engine’s behavior is unchanged but no speech event is emitted.


##### `NullEvents.closing`  (lines 210–211)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: This event observer intentionally publishes nothing for final replies. It lets local or test runs finish without setting up an event channel.

**Data flow**: It receives the marked final replies, ignores them, and returns nothing. The final answer still comes back in the Finished result.

**Call relations**: AgentEngine._close and AgentEngine._exhaust may call this through AgentEvents. With NullEvents, the only visible final output is the returned Finished object.


##### `NullEvents.exhausted`  (lines 213–214)

```
def exhausted(self) -> None
```

**Purpose**: This event observer intentionally does nothing when the run is exhausted. It is the quiet default for callers that do not need exhaustion notifications.

**Data flow**: It takes no extra input and returns nothing. No notification is sent and no state is changed.

**Call relations**: AgentEngine._exhaust calls this through AgentEvents. With NullEvents, exhaustion is still recorded in the returned Finished result but not announced elsewhere.


##### `AgentEngine.run`  (lines 228–258)

```
async def run(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: This is the main agent loop. It repeatedly prepares the transcript, asks the model for a round, runs tools if requested, and stops when it has a final answer or must force one.

**Data flow**: It starts with incoming messages. Each round, it asks the conversation boundary to prepare them, streams a model response, separates marked visible replies from plain answer text, and then either runs tools, closes with text, nudges an empty response, or continues after recoverable feedback. It returns a Finished object containing the final transcript and answer, possibly marked as exhausted or structured.

**Call relations**: This method is the top-level driver inside the file. It calls AgentEngine._stream for model rounds, AgentEngine._tool_exchange when the model asks for tools, AgentEngine._close when the model gives final text, and AgentEngine._exhaust when the round budget is used up.

*Call graph*: calls 4 internal fn (_close, _exhaust, _stream, _tool_exchange); 3 external calls (__init__, __init__, marked_replies).


##### `AgentEngine._stream`  (lines 260–296)

```
async def _stream(self, messages: tuple[Message, ...], round_index: int, mode: RoundMode) -> ModelRound
```

**Purpose**: This builds the exact request sent to the model for one round. It decides which tools are visible, whether a specific tool must be chosen, and what round mode the model should know about.

**Data flow**: It receives the current messages, round index, and mode. In normal mode it gathers tool definitions, checks for duplicate names, and may add a structured finish tool; in no-tools mode it sends no tools; in force-finish mode it sends only the finish tool and requires it. It creates a ModelRequest, sends it to the model, and returns the resulting ModelRound.

**Call relations**: AgentEngine.run uses this for ordinary rounds. AgentEngine._force_finish uses it to demand a structured finish call, and AgentEngine._exhaust uses it to force a final no-tools answer or structured finish after the round limit.

*Call graph*: called by 3 (_exhaust, _force_finish, run); 1 external calls (__init__).


##### `AgentEngine._tool_exchange`  (lines 298–364)

```
async def _tool_exchange(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished | tuple[Message, ...]
```

**Purpose**: This runs the tools requested by a model round and turns their results into the next conversation messages. It also recognizes the special structured-output finish tool and can end the run immediately when that tool is valid.

**Data flow**: It receives the model round, the marked visible replies, and the round index. It first emits the visible replies, then checks whether any call is the structured finish tool. If a finish call is valid and alone, it returns Finished; otherwise it builds error feedback for that finish call. For normal tool calls, it splits them into safe execution batches, prepares each batch, runs the calls, collects ToolResult objects, performs after-round cleanup, and returns an updated transcript containing the assistant’s tool calls and the user’s tool results.

**Call relations**: AgentEngine.run calls this whenever a streamed model round contains tool calls. It uses dispatch_segments to schedule calls, asyncio.gather to run a safe batch concurrently, and AgentTools methods to prepare, execute, and clean up.

*Call graph*: called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, gather, dispatch_segments).


##### `AgentEngine._close`  (lines 366–383)

```
async def _close(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished
```

**Purpose**: This finishes a run when the model has produced text instead of tool calls. If structured output is required, it may accept the prose, or ask the model to convert it into the required finish tool format.

**Data flow**: It receives the streamed round, marked replies, and round index. If structured output is active and not blocked, it tries to turn the prose into an accepted structured answer; if that fails, it appends a prompt asking for the finish format and calls the forced-finish path. Without that extra structured step, it emits closing events and returns Finished with the model’s text.

**Call relations**: AgentEngine.run calls this when a model round has non-empty text and no tool calls. It may hand off to AgentEngine._force_finish, or it may complete the run directly.

*Call graph*: calls 1 internal fn (_force_finish); called by 1 (run); 2 external calls (__init__, __init__).


##### `AgentEngine._force_finish`  (lines 385–394)

```
async def _force_finish(self, messages: tuple[Message, ...], round_index: int) -> Finished
```

**Purpose**: This forces a structured-output run to end through its special finish tool. It is used when the engine needs a machine-checkable answer rather than free-form text.

**Data flow**: It receives messages and a round index, then streams a model round in force-finish mode. It requires exactly one tool call and requires that call to be the configured finish tool. It validates the call and returns Finished with the validated answer; if the model does not comply or validation fails, it raises an error.

**Call relations**: AgentEngine._close calls this when prose must be converted into structured output. AgentEngine._exhaust calls it when the run is out of rounds but still needs a structured final answer. It relies on AgentEngine._stream to send the restricted finish-only request.

*Call graph*: calls 1 internal fn (_stream); called by 2 (_close, _exhaust); 1 external calls (__init__).


##### `AgentEngine._exhaust`  (lines 396–413)

```
async def _exhaust(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: This is the fallback path when the agent has used all allowed rounds without finishing. It notifies observers, prepares the transcript for exhaustion, and asks the model for one final answer.

**Data flow**: It receives the current messages. It marks exhaustion through events, lets the conversation boundary prepare the transcript, and then either adds a structured force-finish prompt and calls AgentEngine._force_finish, or adds a plain final prompt and streams one no-tools model round. It returns Finished with exhausted set to true.

**Call relations**: AgentEngine.run calls this after the normal round loop reaches the configured maximum. It uses AgentEngine._force_finish for structured output, or AgentEngine._stream plus marked_replies and closing events for an ordinary final answer.

*Call graph*: calls 2 internal fn (_force_finish, _stream); called by 1 (run); 3 external calls (__init__, __init__, marked_replies).


### Turn mediation and activity
Extension hooks coordinate safety and tool modifications while activity labeling translates internal tool calls into user-friendly status text.

### `core/src/ufo/runtime/ext/hooks.py`

`domain_logic` · `turn lifecycle`

Extensions can react to important moments in a turn, such as before a tool is used, after a tool returns, or when the user submits a prompt. This file is the “switchboard” for those reactions. It keeps hooks grouped by event, then fires the right group when that event happens.

A hook is like a checkpoint on an assembly line. Each one sees the current package, may leave it alone, reject it, change part of it, or add a note. The file makes sure those checkpoints run in the pinned order, so the result is predictable. If one hook changes a tool input or output, the next hook sees that changed version.

The file also draws a clear safety line. For gating events, such as before tool use or user prompt submission, a serious hook failure can “fail closed”: the action is denied rather than allowed silently. For non-gating events, failures are logged and the turn continues, because those hooks are treated as observation or cleanup rather than permission checks. Hook calls are also time-limited so a slow extension cannot stall the turn forever. Finally, the file checks that a hook only returns outcomes allowed for that event, so an extension cannot, for example, deny an action at a stage where denial is not meaningful.

#### Function details

##### `HookChain.__post_init__`  (lines 90–93)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that all hooks in a HookChain are meant for the same audience as the chain itself. It prevents a chain from mixing hooks that are intended for different visibility or sharing scopes.

**Data flow**: After a HookChain is created, it reads every bound hook stored in the chain. It compares each hook extension’s audience with the chain’s audience. If they all match, nothing changes; if any one differs, it raises an error so the bad chain cannot be used.

**Call relations**: This runs automatically as part of creating a HookChain. It acts as an early guard before HookChain.fire ever runs, so the firing logic can assume all hooks belong to the same audience.


##### `HookChain.fire`  (lines 95–173)

```
async def fire(self, event: HookEvent, payload: HookPayload, turn: Turn | None, agent: Agent | None, speaker_member_id: UUID | None) -> HookResolution
```

**Purpose**: This runs all hooks for one event and folds their responses into a single HookResolution. It is used when the runtime reaches a hookable moment and needs to know whether to continue, deny, modify data, or add context.

**Data flow**: It takes an event, that event’s payload, optional turn and agent records, and the speaker identity. It looks up hooks for the event, skips tool-specific hooks that do not apply, builds a HookContext for each hook, and calls the hook with a five-second timeout. As hooks return, it combines their effects: a denial stops immediately, input or output changes are passed to later hooks, and injected text is collected in order. At the end it returns a HookResolution containing the final denial, changed input or output, and collected injected text.

**Call relations**: This is the main firing path for the hook chain during a turn. For each hook it creates a HookContext, uses dataclasses.replace to pass along updated payload data, and wraps the call in asyncio.timeout so an extension cannot wait forever. If a hook returns an outcome that this event is not allowed to produce, it creates a HookOutcomeNotAllowed error and then applies the normal failure policy. It returns a HookResolution for the caller to apply at the point where the event happened.

*Call graph*: 5 external calls (__init__, __init__, __init__, timeout, replace).


### `core/src/ufo/runtime/turns/activity.py`

`domain_logic` · `during a turn, when a tool call needs a user-facing activity label`

When the system uses a tool, the raw details can be noisy or unsafe to show directly. A tool call may include internal names, file paths, commands, URLs, or other details that should not appear in a member-facing activity feed. This file solves that by asking a language model to rewrite one tool call as a brief plain-language step.

The main piece is ActivitySummarizer. It receives a tool call and, optionally, the user’s goal. It builds a small JSON payload containing only a shortened version of the goal and a shortened rendering of the tool arguments. Then it sends that payload to a model with strict instructions: write a 3 to 8 word label, describe the current action, and do not reveal tool names or sensitive details.

There are two safety rails here. First, long goals and tool arguments are cut down before being sent, like putting only a small sample on a note card. Second, the model request has a short timeout, so activity labeling cannot stall the main work for long. If anything goes wrong, the code records a metric and log entry, then quietly returns no label. Finally, the model’s reply is cleaned into a single tidy line before it is shown.

#### Function details

##### `ActivityModel.model`  (lines 31–31)

```
def model(self) -> str
```

**Purpose**: This is part of the small contract that any activity-labeling model must follow. It provides the model name to use when asking for a summary.

**Data flow**: A concrete model object supplies its configured model name → callers read that name → the name is placed into the model request so the right language model is used.

**Call relations**: ActivitySummarizer.summarize depends on this property when it builds the request for a label. The protocol keeps the summarizer independent from any one specific model implementation.


##### `ActivityModel.complete`  (lines 33–33)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This is the other part of the contract for an activity-labeling model. It takes a model request and returns the model’s text answer.

**Data flow**: A prepared ModelRequest goes in → the concrete model implementation sends it to whatever model backend it uses → a text completion comes back for later cleanup.

**Call relations**: ActivitySummarizer.summarize calls this after preparing the prompt and payload. The summarizer does not care how the model is reached; it only needs this method to return text.


##### `ActivitySummarizer.summarize`  (lines 42–69)

```
async def summarize(self, call: ToolUseBlock, goal: str='') -> str | None
```

**Purpose**: This function creates one short, member-facing description of a tool call. It is used when the system wants to show progress in human terms without exposing internal tool details.

**Data flow**: A ToolUseBlock and optional user goal go in → the goal is shortened, the tool arguments are safely rendered and shortened, and both are packed into JSON → a language model is asked for a tiny plain-language label → the returned text is cleaned into one line → the cleaned label comes out, or None comes out if the model call fails or returns nothing useful. It also records a metric and log message if the attempt fails.

**Call relations**: This is the central flow in the file. It asks _bounded_arguments to make the tool arguments safe in size, builds the model request, waits only a limited time for the model response, and then hands the response to activity_line so it is fit to display. If the model layer raises an error or times out, this function reports the failure and stops without interrupting the larger tool workflow.

*Call graph*: calls 2 internal fn (_bounded_arguments, activity_line); 6 external calls (__init__, __init__, timeout, dumps, emit_metric, log).


##### `_bounded_arguments`  (lines 72–76)

```
def _bounded_arguments(arguments: dict[str, object]) -> str
```

**Purpose**: This helper turns a tool’s argument dictionary into compact JSON and cuts it off if it is too long. It keeps the activity-summary prompt small and avoids sending oversized details to the model.

**Data flow**: A dictionary of tool arguments goes in → it is converted to a compact JSON string → if it is within the allowed size, it is returned as-is; if it is too long, it is trimmed and marked with an ellipsis.

**Call relations**: ActivitySummarizer.summarize uses this before calling the model. It acts like a size limiter at the doorway, making sure the summarizer sends only a bounded amount of argument text.

*Call graph*: called by 1 (summarize); 1 external calls (dumps).


##### `activity_line`  (lines 79–82)

```
def activity_line(text: str) -> str | None
```

**Purpose**: This function cleans the model’s answer into a single neat label. It removes extra spacing, common bullet characters, wrapping quotes or backticks, and ending punctuation.

**Data flow**: Raw model text goes in → repeated whitespace is collapsed, surrounding decoration is stripped, and final punctuation is removed → a clean label string comes out, or None if nothing meaningful remains.

**Call relations**: ActivitySummarizer.summarize calls this after the model replies. It is the final polish step before the label can be shown to a member.

*Call graph*: called by 1 (summarize); 1 external calls (sub).

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-feature-flags` — The shared on/off switches and rollout choices that let operators change behavior without redeploying.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-model-catalog` — The shared list of available AI models, their abilities, providers, prices, and credential needs.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-turn-queue` — The durable queue of conversation turns waiting, running, parked, resumed, or blocked as duplicates.
- `reg-live-workflow-state` — The shared run-state for active turns, including locks, progress, retry guards, cancellation, and completion markers.
- `reg-turn-runtime-config` — The per-turn saved runtime settings that must survive retries and keep a turn using the same execution choices.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-browser-sessions` — The active or reusable Chrome browser sessions, tabs, downloads, and remote-control connections used by agents.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-conversation-slots-ui` — The shared side-panel and workspace UI state for artifacts, sources, tasks, sites, automations, and app home screens.
- `reg-subagent-state` — The parent-child turn links, delegation contracts, spawn identities, and pending result deliveries for helper agents.
- `reg-surface-outbox` — The durable outbound delivery buffer and writeback markers for replies or mid-turn messages that must be sent to external surfaces exactly once.
- `reg-tool-task-journals` — Persistent journals and handles for long-running tool or command executions so they can be resumed, polled, deduplicated, or cleaned up later.
- `reg-runtime-message-bus` — Shared Redis/pub-sub or message-hub connection state used to coordinate live updates, workers, and cross-process runtime events.
- `reg-model-client-pools` — Shared outbound model/provider client sessions, connection pools, retry state, and provider-side rate-limit/backoff buckets used across turns.
- `reg-repl-scratchpad-state` — Persistent Python/JavaScript REPL interpreter sessions, variables, scratch files, and execution handles kept across tool calls or turns.
- `reg-action-proposal-state` — Durable proposal records for actions or changes that require later review, approval, rejection, or replay.
