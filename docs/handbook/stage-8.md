# Per-Turn Runtime Environment Assembly  `stage-8`

This stage is part of the main work loop. It happens before each agent turn, like setting a workbench before someone starts a task. The system decides what the agent can read, what tools it may use, which model will answer, what skills are loaded, and what files or browser access are available.

Prompt, Skill, and Tool Catalog Construction builds the agent’s instruction pack. It renders the system prompt, adds shared reply rules, declares safe tools, loads reusable skills, lists available models, and prepares the catalog for spawning helper agents. It also keeps these pieces traceable so the same turn can be understood later.

Sandbox and Browser Session Preparation builds the safe place where actions happen. It creates or resumes the conversation workspace, chooses where commands run, and sets up browser access without exposing real secrets or crossing sandbox boundaries.

The agent setup file checks whether an installed agent is still missing things, such as connected accounts, credentials, or scheduled-task setup. If something is missing, it prepares clear guidance so the assistant can ask the user for what is needed.

## Sub-stages

- [Prompt, Skill, and Tool Catalog Construction](stage-8.1.md) `stage-8.1` — 15 files
- [Sandbox and Browser Session Preparation](stage-8.2.md) `stage-8.2` — 15 files

## Files in this stage

### Per-Turn Runtime Environment Assembly
### `core/src/ufo/runtime/kinds/agent_setup.py`

`domain_logic` · `setup checks during portal reads and member turns`

An installed agent may arrive with its code and prompt, but without the practical things it needs to operate. For example, a GitHub-related agent may need access to a GitHub account, an API key, or a schedule that wakes it up every morning. This file is the shared place that describes those setup needs and turns them into readable setup status.

The data models in this file act like a checklist. AgentSetup is the checklist the shipped app declares. It can list account providers, workspace credentials, standing orders such as scheduled tasks, and instructions for the member. SetupState is the live version of that checklist: it says which items are already done and which still need action.

The file also protects against confusing declarations. For example, if an agent says it needs a scheduled task, it must also say what schedule options it offers. Likewise, an hourly schedule cannot name weekdays, because an hourly task has no single local clock time to convert across time zones.

At runtime, setup_state reads the database and answers, “For this member, is this agent ready?” pending_setup finds installed agents that still need account grants. setup_skill turns that into a temporary skill: instructions the language model can use when a member asks why an agent is not working or wants to finish setup.

#### Function details

##### `SetupCadence._hourly_spans_every_day`  (lines 46–54)

```
def _hourly_spans_every_day(self) -> 'SetupCadence'
```

**Purpose**: This validation step makes sure a schedule option is understandable and safe to convert later. It prevents an hourly schedule from also naming weekdays, and it checks that weekday numbers are valid.

**Data flow**: It receives a SetupCadence object after its fields have been filled. It inspects the hour and weekdays values. If the cadence is impossible or ambiguous, it raises an error; otherwise it returns the same cadence unchanged.

**Call relations**: This runs automatically when a SetupCadence is created or checked by Pydantic, the data validation library. It keeps bad schedule declarations from reaching later code that would have to turn local-time choices into cron schedules.


##### `AgentSetup._a_clock_need_carries_its_offer`  (lines 145–162)

```
def _a_clock_need_carries_its_offer(self) -> 'AgentSetup'
```

**Purpose**: This validation step keeps scheduled-agent setup honest. If an agent says it needs a clock to wake it, it must also provide the schedule offer that lets the member arm that clock; if it offers schedule cadences, it must declare the scheduled-task need they belong to.

**Data flow**: It receives an AgentSetup object after its fields have been filled. It compares the standing needs with the optional schedule offer. If one side is missing, it raises an error; otherwise it returns the same setup declaration.

**Call relations**: This runs automatically when AgentSetup is created or loaded from stored data. It protects the setup screen and turn logic from showing a need the member cannot act on, or showing a schedule offer with nothing to attach it to.


##### `_armed`  (lines 249–261)

```
async def _armed(kind: str, wanted: AgentSetup, armed: Armed) -> ArmedOrder
```

**Purpose**: This helper asks whether the agent already has a particular standing order, such as a scheduled task. For scheduled tasks, it asks about the app’s specific named task so one feature’s schedule is not mistaken for another’s.

**Data flow**: It takes a standing-order kind, the agent’s declared setup, and a caller-provided async checker named armed. If the kind is the scheduled-task kind and the setup includes a schedule, it asks for that specific schedule name. Otherwise it asks whether any order of that kind is present. It returns an ArmedOrder saying whether the order exists and, if relevant, what cron schedule it uses.

**Call relations**: setup_state calls this while building the live setup status. The actual standing-order records live outside this file, so _armed delegates the lookup to the armed callback supplied by the caller.

*Call graph*: called by 1 (setup_state).


##### `setup_state`  (lines 264–361)

```
async def setup_state(agent_id: UUID, member_id: UUID, *, armed: Armed) -> SetupState
```

**Purpose**: This function builds the full setup status for one agent as seen by one member. It answers which declared account connections, credentials, and standing orders are already satisfied and which are still missing.

**Data flow**: It receives an agent ID, a member ID, and an armed callback for checking standing orders. It reads the agent’s stored setup declaration from the workspace database, then reads grants the member can use, stored credential slots in the workspace, and deploy-time environment credentials. It also asks _armed about each standing order. It returns a SetupState object containing the visible checklist, including done and not-done rows.

**Call relations**: This is used when a surface, such as a setup screen or agent projection, needs to show the current setup state. It pulls together database facts, deploy-time credential fallback, and standing-order checks, then packages them into SetupConnector, SetupCredentialState, SetupStanding, and SetupState records.

*Call graph*: calls 1 internal fn (_armed); 9 external calls (__init__, __init__, __init__, __init__, or_, select, workspace_tx, deploy_env, ws_current).


##### `pending_setup`  (lines 364–420)

```
async def pending_setup(member_id: UUID) -> tuple[tuple[UUID, str, AgentSetup], ...]
```

**Purpose**: This function finds installed, non-archived agents that still need account connections for a particular member. It is used so the system knows which agent should ask for which missing account grant.

**Data flow**: It receives a member ID. It reads all shipped agents with setup declarations in the current workspace, then reads account grants usable by that member, including shared connections and the member’s private connections. For each agent, it compares declared connector needs with granted providers. It returns a sorted tuple of agent ID, agent name, and a smaller AgentSetup containing only the missing connectors and instructions.

**Call relations**: setup_skill calls this at turn time. The result tells either the current agent what it personally still needs, or tells the main agent which other installed agents still need setup help.

*Call graph*: called by 1 (setup_skill); 5 external calls (__init__, or_, select, workspace_tx, ws_current).


##### `_wants`  (lines 423–424)

```
def _wants(missing: AgentSetup) -> str
```

**Purpose**: This small formatting helper turns missing connector names into a plain phrase the agent can say to a member. For example, it can turn providers into text like “a GitHub account, a Slack account.”

**Data flow**: It receives an AgentSetup containing missing connectors. It reads the connector provider names and joins them into a comma-separated human-readable string. It returns that string and changes nothing else.

**Call relations**: setup_skill uses this when writing the instructions that will be shown to the language model. It keeps the wording of missing account needs consistent in both the current-agent and roster cases.

*Call graph*: called by 1 (setup_skill).


##### `setup_skill`  (lines 444–488)

```
async def setup_skill(agent_id: UUID, is_main: bool, speaker_member_id: UUID | None) -> RuntimeSkill | None
```

**Purpose**: This function creates a temporary RuntimeSkill that teaches an agent how to finish setup when a member can actually take action. If nothing is missing, or there is no speaking member to grant accounts, it returns nothing.

**Data flow**: It receives the current agent ID, whether that agent is the main agent, and the speaking member ID if there is one. If no member is speaking, it returns None. Otherwise it asks pending_setup what is missing for that member. If the current agent is missing grants, it returns a RuntimeSkill telling it what to ask for. If the current agent is the main agent and other agents are missing grants, it returns a roster-style RuntimeSkill telling the main agent how to help set them up. If there is nothing actionable, it returns None.

**Call relations**: This is used during member turns, when skills are loaded for the language model. It calls pending_setup to find real missing grants, uses _wants to phrase them clearly, and hands back a RuntimeSkill only when the model has useful setup work it can ask the member to do.

*Call graph*: calls 2 internal fn (_wants, pending_setup); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-search-provider-catalog` — The common search and page-fetching service state used when the system needs outside web information.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-conversation-workspace-change-state` — The persisted record of file/workspace changes detected for a conversation sandbox, used for commit summaries, artifact presentation, recovery, and debugging.
- `reg-turn-runtime-snapshot` — The per-turn frozen runtime configuration and generated references used to run, recover, bill, and debug a turn consistently after settings change.
- `reg-sandbox-template-build-cache` — The local Docker image and E2B template build/version state that sandbox launch code relies on to create compatible runtimes.
- `reg-turn-context-token-budget` — The active per-turn context-window and token/image budget accounting used to choose prompt contents, trigger compaction, constrain model rounds, and reconcile usage.
