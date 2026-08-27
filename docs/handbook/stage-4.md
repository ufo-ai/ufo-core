# Workspace Onboarding and Initial Seeding  `stage-4`

This stage is the front door for a new workspace. It runs during first setup, or when the system needs to find an existing workspace and finish its basic setup. The main coordinator is onboarding.py. It creates the workspace, adds the first administrator, makes the main assistant agent, checks that required secrets are present, and then gives installed extensions a chance to set themselves up.

The control-plane API in onboard_control.py is the trusted bridge used by the Rust sign-in and control layer. It creates or finds workspaces, seats members, and answers onboarding questions without spreading sensitive rules across the system. Provisioning.py installs agent definitions that come from extensions, while carefully keeping any changes members already made. Agent_setup.py works out what an agent still needs, such as credentials, connected accounts, or a schedule, and turns that into setup screens or a temporary helper skill.

Seed.py can add a safe demo “kitchen sink” conversation, like a showroom model, so new users can see many product features at once. __init__.py simply makes this folder importable by other Python code.

## Files in this stage

### First-Run Workspace Bootstrap
The main onboarding orchestrator creates the workspace, initial administrator, assistant agent, secrets checks, and extension setup pass.

### `core/src/ufo/onboard/onboarding.py`

`orchestration` · `first-run initialization`

This file is the “new office opening” checklist for UFO. On the very first run, the system needs a permanent workspace, a first admin user, and a default assistant agent before anyone can use it. It also needs to know that required secret values, such as model API keys or credential-encryption keys, are available before it creates anything lasting.

The main class, Onboarding, runs this process in two phases. First it checks prerequisites and creates the core records in the database inside one transaction, meaning the database changes are treated as one all-or-nothing unit. If the workspace already has a member, it raises AlreadyInitialized instead of creating duplicates. That protects a real installation from accidentally being initialized twice.

After the core workspace exists, the file runs extra onboarding steps supplied by installed extensions. Each extension receives its own scoped ExtensionContext, like giving each add-on its own labeled toolbox rather than letting it rummage through everything. If an extension step fails, the failure is logged and the process moves on. This is important: a broken add-on should not undo the usable core workspace or stop other add-ons from preparing themselves.

#### Function details

##### `run_onboarding_steps`  (lines 51–79)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs setup steps supplied by installed extensions after the main workspace already exists. It gives each extension the context it is allowed to use and keeps one failing extension from stopping the rest.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters the workspace context, skips extensions with no steps, skips all extension setup that needs credentials when no credential store is available, builds a scoped context for each extension, and calls each step. It produces no returned value, but it may let extensions write their own setup data and it logs skipped or failed steps.

**Call relations**: Onboarding.run_steps calls this after core provisioning has happened. Inside, it asks context_for to build the extension-specific context, uses ws to mark which workspace the steps are running for, and uses log when a step cannot run or fails.

*Call graph*: called by 1 (run_steps); 3 external calls (context_for, log, ws).


##### `Onboarding.run`  (lines 95–98)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-run onboarding flow from start to finish. A caller uses this when it wants both the core workspace creation and the extension setup to happen.

**Data flow**: It starts with the configuration and choices stored on the Onboarding object, such as email, model, credentials, manifests, and reasoning effort. It first creates the core workspace and receives the new workspace and member IDs, then runs follow-up setup steps for that workspace. It returns the Onboarded result so the caller knows which workspace and admin member were created.

**Call relations**: This is the high-level path: it calls Onboarding.create first, then Onboarding.run_steps. It is the method a command such as an init command would use when it wants the whole process, not just one phase.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 100–106)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the durable core of a new installation: required checks, workspace, first admin, and main agent. It deliberately does this before extension setup so the base system can exist even if add-ons later fail.

**Data flow**: It uses the onboarding settings already stored on the object. It checks whether the selected model needs a secret key, checks whether extension setup requires a credential key, and then writes the workspace data to the database. It returns an Onboarded object containing the new workspace ID and first member ID.

**Call relations**: Onboarding.run calls this as the first phase. This method delegates the safety checks to Onboarding._require_model_key and Onboarding._require_credentials_for_steps, then hands the actual database creation to Onboarding._create_workspace.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 108–110)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the after-creation setup that depends on an existing workspace. This includes agent provisioning for extensions and then each extension’s own onboarding steps.

**Data flow**: It receives an Onboarded object containing the workspace that was just created. It applies extension-related agent provisioning to that workspace, then passes the manifests, workspace ID, and credentials to the extension onboarding runner. It returns nothing, but extensions may create or configure their own data.

**Call relations**: Onboarding.run calls this after Onboarding.create succeeds. It first creates an AgentProvisioning helper for the installed manifests, then hands off to run_onboarding_steps so extension-defined setup code can run in the right workspace.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 112–123)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops initialization early if installed extensions have onboarding steps that need a credential store, but no credential key is configured. This avoids creating a half-ready workspace that cannot safely store extension secrets.

**Data flow**: It reads the Onboarding object’s credential store, extension manifests, and configured credential-key environment name. If credentials are available, it does nothing. If any installed extension has onboarding steps but there is no credential store, it raises an error explaining which environment setting is needed.

**Call relations**: Onboarding.create calls this before touching the database. It is one of the guard checks that must pass before Onboarding._create_workspace is allowed to create lasting records.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 125–133)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected model has the required deployment secret available before the first assistant turn could happen. This catches a missing model API key during setup instead of letting the user discover it later.

**Data flow**: It asks Onboarding._model_key_env which environment variable name, if any, is required for the chosen model. If no eager check is possible or needed, it returns quietly. If a key name is required, it checks the deployment environment for that value and raises an error if it is missing.

**Call relations**: Onboarding.create calls this before creating the workspace. It relies on Onboarding._model_key_env to identify the needed secret and deploy_env to look for it in the environment.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create); 1 external calls (deploy_env).


##### `Onboarding._model_key_env`  (lines 135–138)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that should contain the secret key for the selected model, when the core system knows how to check it. Some extension-provided models resolve their keys later, so this can return no name.

**Data flow**: It reads the configuration, installed manifests, and selected model name from the Onboarding object. It builds or queries the model registry, then asks what key environment variable belongs to that model. It returns the variable name as text, or None if there is nothing this setup flow can check early.

**Call relations**: Onboarding._require_model_key calls this as its lookup step. This method hands the model-key question to model_registry, which knows about core and extension-contributed model providers.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 140–167)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the core first-run records to the database: the workspace, the first administrator member, and the default main agent. It also prevents duplicate initialization by refusing to run if any member already exists.

**Data flow**: It opens a workspace database transaction, checks whether a member is already present, and raises AlreadyInitialized if so. Otherwise it creates fresh IDs, inserts a workspace row, creates the admin member for the given email, inserts the main agent with the chosen model and reasoning setting, and returns the new workspace and member IDs. The database is changed only if this transaction succeeds.

**Call relations**: Onboarding.create calls this after all preflight checks pass. It uses workspace_tx for the database boundary, SQLAlchemy insert and select operations for database writes and reads, create_member for the first admin account, uuid4 for new IDs, and returns an Onboarded result for later steps.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Extension Agent Readiness
Extension-provided agents are described, checked for missing setup needs, and provisioned into the workspace without overwriting member edits.

### `core/src/ufo/kinds/agent_setup.py`

`domain_logic` · `setup screen and member turn handling`

An installed agent may arrive with its code and prompt, but still be unable to do useful work. It may need permission to use a member’s account, a workspace-wide secret key, or a standing order such as a scheduled task. This file is the shared place that records those needs and checks whether they have been satisfied.

The file uses Pydantic models, which are structured data objects that validate their own fields, to describe setup requirements in a stable form. For example, AgentSetup says which account providers are needed, which credentials are needed, whether a schedule is offered, and what instructions should be shown to the member. SetupState is the read-back version: it says which declared needs are already filled and which are still missing.

The database is the source of truth for what has actually happened. The setup_state function reads the agent’s declared setup, checks connection grants that the current member may use, checks stored credentials, asks another part of the system whether standing orders exist, and returns a complete setup status. pending_setup looks across installed agents and finds those still missing account grants for a particular member.

Finally, setup_skill turns missing setup into a RuntimeSkill, meaning temporary instructions shown to the language model only when a member can actually act on them. Without this file, agents could look installed but silently fail because no one had granted the accounts, credentials, or schedule they need.

#### Function details

##### `SetupCadence._hourly_spans_every_day`  (lines 46–54)

```
def _hourly_spans_every_day(self) -> 'SetupCadence'
```

**Purpose**: This validator keeps schedule choices meaningful. It prevents an “every hour” cadence from also naming weekdays, because an hourly schedule has no single local hour that can be converted cleanly across time zones.

**Data flow**: It receives a SetupCadence after its fields have been filled. It checks whether the hour is missing, which means every hour, and rejects any weekday list in that case. It also checks that any weekday numbers are in the allowed range of 0 through 6. If everything is sensible, the same cadence object comes out unchanged.

**Call relations**: This runs automatically when a SetupCadence is created or loaded through Pydantic validation. It protects later code and user interfaces from receiving a schedule option that cannot be honestly converted or displayed.


##### `AgentSetup._a_clock_need_carries_its_offer`  (lines 145–162)

```
def _a_clock_need_carries_its_offer(self) -> 'AgentSetup'
```

**Purpose**: This validator makes sure schedule-related setup is complete and not contradictory. If an agent says it needs a scheduled task, it must also offer the schedule details that let the member arm it; if it offers schedule details, it must declare that scheduled-task need.

**Data flow**: It receives an AgentSetup after its fields are filled. It looks at the declared standing-order kinds and the optional schedule offer. If the scheduled-task kind and schedule offer do not match each other, it raises an error. If they do match, it returns the same setup object.

**Call relations**: This runs whenever an AgentSetup is validated from stored or newly declared setup data. It ensures setup_state, setup screens, and setup_skill all work from a declaration that makes sense: the screen never shows a clock-related need without an action the member can take.


##### `_armed`  (lines 249–261)

```
async def _armed(kind: str, wanted: AgentSetup, armed: Armed) -> ArmedOrder
```

**Purpose**: This helper asks whether the agent already has a standing order of a particular kind, such as a scheduled task. For scheduled tasks, it asks about the app’s specific named task instead of any task of that kind.

**Data flow**: It receives a standing-order kind, the agent’s declared setup, and an async callback named armed. If the kind is a scheduled task and the setup includes a named schedule, it passes that name to the callback. Otherwise it asks only whether any order of that kind exists. The callback returns an ArmedOrder saying whether the order is held and, when relevant, what schedule it uses.

**Call relations**: setup_state calls this while building the setup status for each declared standing-order kind. _armed delegates the actual lookup to the caller-supplied armed function because standing orders live in extension-specific storage outside this core file.

*Call graph*: called by 1 (setup_state).


##### `setup_state`  (lines 264–361)

```
async def setup_state(agent_id: UUID, member_id: UUID, *, armed: Armed) -> SetupState
```

**Purpose**: This function builds the full setup status for one agent as seen by one member. It answers questions like: has this member granted the needed account, is the required workspace credential available, and is the schedule already armed?

**Data flow**: It takes an agent ID, a member ID, and an armed callback for checking standing orders. It reads the agent’s saved setup declaration from the workspace database. If there is no setup, it returns an empty SetupState. Otherwise it checks which declared account providers have usable grants for this member, which credential slots are stored or supplied by deployment environment variables, and which standing orders are armed. It returns a SetupState containing all declared rows, each marked as filled or not, plus the schedule offer and instructions.

**Call relations**: This is the main read path for setup status, especially for surfaces such as a portal or setup display. It calls _armed for standing-order checks, constructs SetupConnector, SetupCredentialState, SetupStanding, and SetupState rows, and uses database and environment lookups to compare the agent’s declaration with the workspace’s current reality.

*Call graph*: calls 1 internal fn (_armed); 9 external calls (__init__, __init__, __init__, __init__, or_, select, deploy_env, workspace_tx, ws_current).


##### `pending_setup`  (lines 364–420)

```
async def pending_setup(member_id: UUID) -> tuple[tuple[UUID, str, AgentSetup], ...]
```

**Purpose**: This function finds installed, unarchived agents that are still missing account grants for a specific member. It is focused on connector setup, because an account grant is something the member can provide through conversation.

**Data flow**: It takes a member ID. It reads all shipped agents in the current workspace that have setup declarations and are not archived. It then reads the account providers already granted to each agent, counting only grants this member may use: shared grants or the member’s own private grants. For each agent, it compares declared connector needs with granted providers. It returns a sorted tuple of agent ID, agent name, and a smaller AgentSetup containing only the missing connectors and instructions.

**Call relations**: setup_skill calls this before deciding whether to load setup instructions into a turn. pending_setup does the broad workspace scan, while setup_skill decides whether the current agent should ask about itself or, if it is the main agent, about other agents.

*Call graph*: called by 1 (setup_skill); 5 external calls (__init__, or_, select, workspace_tx, ws_current).


##### `_wants`  (lines 423–424)

```
def _wants(missing: AgentSetup) -> str
```

**Purpose**: This small formatter turns missing account providers into a readable phrase for the member. For example, it can turn provider names into text like “a GitHub account, a Slack account.”

**Data flow**: It receives an AgentSetup that represents only missing connector needs. It reads the connector provider names and joins them into a comma-separated sentence fragment. The output is plain text used in setup instructions.

**Call relations**: setup_skill calls this when writing the message that tells the language model what to ask the member for. It keeps the wording of missing account needs in one place.

*Call graph*: called by 1 (setup_skill).


##### `setup_skill`  (lines 444–488)

```
async def setup_skill(agent_id: UUID, is_main: bool, speaker_member_id: UUID | None) -> RuntimeSkill | None
```

**Purpose**: This function decides whether to give the language model a temporary setup skill for the current turn. It only does so when a real member is speaking and there is something that member can fix, such as granting an account.

**Data flow**: It takes the current agent ID, a flag saying whether this is the main agent, and the speaking member’s ID if there is one. If no member is speaking, it returns None because setup actions require a member. Otherwise it asks pending_setup what account grants are missing for that member. If the current agent itself is missing grants, it returns a RuntimeSkill instructing it to ask for those accounts. If the current agent is the main agent and other agents are missing grants, it returns a roster-style RuntimeSkill listing them. If there is nothing actionable, it returns None.

**Call relations**: This is used during turn preparation, when the system decides what skills or instructions the model should see. It calls pending_setup to get current missing setup, uses _wants to phrase those needs, and creates RuntimeSkill objects only for turns where the member can actually grant the needed accounts.

*Call graph*: calls 2 internal fn (_wants, pending_setup); 1 external calls (__init__).


### `core/src/ufo/kinds/provisioning.py`

`domain_logic` · `extension provisioning during workspace use`

Extensions can come with ready-made agents. This file is the bridge between those extension declarations and the workspace’s real agent list in the database. Its job is careful because a shipped agent starts as something an extension suggests, but once it exists in a workspace, the workspace owns it. That means later extension updates must not casually erase what a member changed.

The main flow looks at every active manifest, which is an extension description, and every agent that manifest declares. For each one, it first checks whether this exact extension and declared agent name already created a row. If so, it leaves the member-controlled settings alone, but may refresh the extension-owned setup data and fill in a missing purpose. If the provision is marked as the main agent, it can adopt the workspace’s existing main agent rather than replacing it.

If no existing row can be reused, the code chooses a safe name. If the preferred name is already taken, it tries a version with the extension name added, then numbered versions. This is like labeling identical boxes from different senders so none is mistaken for another. Finally it inserts a normal agent row, recording where it came from so future passes can recognize it.

#### Function details

##### `AgentProvisioning.apply`  (lines 54–62)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies all agent provisions from all active extension manifests to one workspace. Someone uses this when the system wants the workspace to have the agents promised by its enabled extensions.

**Data flow**: It receives a workspace ID and reads the manifests stored on this AgentProvisioning object. It enters that workspace’s context, walks through each manifest and each declared agent inside it, asks _one to apply each provision, and returns a tuple of outcome records saying what happened.

**Call relations**: This is the public doorway for the file’s behavior. It sets the workspace context with ws, then calls _one for each declared agent so the detailed create, adopt, or refresh decision happens one provision at a time.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 64–147)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Applies one extension-declared agent to one workspace. It decides whether the agent is already present, should adopt an existing row, or needs to be newly created.

**Data flow**: It receives the workspace ID, the extension manifest, and one agent provision. Inside a database transaction, it first searches for a row already tied to the same extension and declared provision name. If found, it may update only extension-owned fields through _fill and reports that the agent is present. If the row conflicts with a new main-agent provision, it detaches and archives the old shipped row so the main agent can be adopted instead. If no shipped row exists, it looks for an unprovisioned workspace row that can be adopted, either the current main agent or an identical agent. If adoption is not possible, it asks _free_name for an unused name, calls _create, and reports that a new row was created.

**Call relations**: AgentProvisioning.apply calls this for every provision. This method is the traffic controller: it opens the workspace transaction, uses database queries and updates, calls _identical to decide whether adoption is safe, calls _fill for existing shipped rows, calls _free_name before new creation, calls _create to insert, and returns a ProvisionOutcome describing the result.

*Call graph*: calls 4 internal fn (_create, _fill, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._fill`  (lines 149–189)

```
async def _fill(self, connection: AsyncConnection, shipped: sa.Row, manifest: Manifest, provision: AgentProvision) -> None
```

**Purpose**: Refreshes the small parts of an already shipped agent that still belong to the extension. It avoids overwriting member-owned settings.

**Data flow**: It receives an open database connection, the existing shipped row, the manifest, and the provision. It compares the stored setup with the setup declared by the current extension version. If setup changed, it prepares to rewrite setup. If the row has no purpose, it prepares to fill in the provision’s purpose. If neither change is needed, it does nothing. Otherwise it updates those fields, records the manifest version that supplied them, and updates the timestamp.

**Call relations**: _one calls this after it finds a shipped row that already belongs to the same extension and provision name. It is the narrow update path that lets new extension-owned setup reach existing workspaces while preserving the wider rule that shipped agents become workspace-owned after creation.

*Call graph*: called by 1 (_one); 2 external calls (execute, update).


##### `AgentProvisioning._free_name`  (lines 191–216)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Finds an agent name that is not already used in the workspace. This prevents an extension-shipped agent from overwriting or blocking a member’s agent or another extension’s agent.

**Data flow**: It receives a database connection, workspace ID, extension name, and the declared preferred name. It reads all active agent names in the workspace. It then tries the declared name, then the declared name plus a cleaned-up extension suffix, then numbered variants. It returns the first unused candidate. If it cannot find one within the limit, it raises an error.

**Call relations**: _one calls this only when it has decided a new agent row must be created. The chosen name is then handed to _create, so the inserted row has a safe, human-readable name that does not collide with existing agents.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 218–275)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Inserts a new workspace agent row from an extension provision. The new row is a normal agent, but it records which extension and declared provision created it so future passes can recognize it.

**Data flow**: It receives an open database connection, workspace ID, manifest, provision, and final name. It gathers the agent specification from the provision. If the provision did not choose an icon, it reads existing workspace icons and asks auto_agent_icon to pick one. It then inserts a row with a new ID, the agent’s prompt, purpose, model, tool settings, setup data, timestamps, and provisioning identity. The insert is written so that if another process already inserted the same provision at the same time, this attempt quietly does nothing instead of failing the user’s turn.

**Call relations**: _one calls this after _free_name picks a usable name. This is the final creation step in the provisioning story, using database insertion, uuid4 for the row ID, and auto_agent_icon when an icon needs to be chosen automatically.

*Call graph*: called by 1 (_one); 4 external calls (execute, select, auto_agent_icon, uuid4).


##### `AgentProvisioning._identical`  (lines 277–290)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing unprovisioned agent already matches what an extension provision would create. If it matches, the system can adopt that row instead of creating a duplicate.

**Data flow**: It receives a database row and an agent provision. It compares the row’s stored prompt, model, reasoning setting, internet access setting, sandbox size, visibility, and tools with the provision’s specification. It returns true only when those member-visible configuration fields match.

**Call relations**: _one uses this while deciding whether an existing named workspace agent can become the provisioned agent. It is the safety check that allows adoption when the row really appears to be the same agent, and avoids adoption when the existing row is merely using the same name.

*Call graph*: called by 1 (_one).


### Onboarding Package and Control API
The onboarding package is exposed for imports and provides trusted private APIs for the Rust control plane to create or find workspaces and seat members.

### `core/src/ufo/onboard/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the system find the drawer and reach the files inside it. Here, the drawer is `ufo.onboard`. Because the file is empty, it does not run setup code, expose shortcut names, or change how imports behave. Its value is structural: it helps keep the project organized and makes room for onboard-related code to live under a clear namespace. Without it, depending on the Python version and packaging setup, imports from this folder might be less reliable or not work as expected.


### `core/src/ufo/onboard/onboard_control.py`

`domain_logic` · `request handling`

This file is the “back office counter” for hosted onboarding. The public sign-in flow does not directly write core workspace tables. Instead, it calls these internal FastAPI routes with a special bearer token, and this file performs the trusted database work.

Its main job is to make onboarding consistent and safe. When a verified person signs in, it can create the workspace tied to their email domain, add them as a member, make the first member an admin, create the default agent, and give a new workspace its starting balance. It also checks that an existing workspace still belongs to the same domain, so a stale or wrong sign-in cannot quietly take over a workspace.

The file also supports reads that must look across all workspaces, such as “which workspaces can this email enter?”, “how many workspaces exist?”, and “which invited teammates need email notices?” Those use a more privileged database path, and some are logged as warnings because cross-workspace reads are sensitive.

A special care point is intake-form text. Because anyone could type it, the file wraps it as untrusted data before putting it into an agent prompt. Like putting a note behind glass, the agent can read it, but the text is not allowed to act as instructions.

#### Function details

##### `_inert`  (lines 159–173)

```
def _inert(answer: str) -> str
```

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 176–195)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

*Call graph*: calls 1 internal fn (_inert); called by 1 (_seat); 1 external calls (wall).


##### `deterministic_workspace_id`  (lines 198–201)

```
def deterministic_workspace_id(domain: str) -> UUID
```

*Call graph*: called by 1 (_choices); 1 external calls (uuid5).


##### `_labelled`  (lines 204–232)

```
def _labelled(rows: Sequence[sa.RowMapping], domain: str) -> list[WorkspaceChoice]
```

*Call graph*: called by 1 (_choices); 2 external calls (__init__, HTTPException).


##### `OnboardControl.router`  (lines 242–249)

```
def router(self) -> APIRouter
```

*Call graph*: 2 external calls (APIRouter, Depends).


##### `OnboardControl._guard`  (lines 251–253)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

*Call graph*: 1 external calls (HTTPException).


##### `OnboardControl._seat`  (lines 255–334)

```
async def _seat(self, request: SeatRequest) -> EnsuredWorkspace
```

*Call graph*: calls 1 internal fn (agent_prompt); 11 external calls (__init__, HTTPException, insert, select, credit, set_reserve, workspace_tx, create_member, email_domain, ws (+1 more)).


##### `OnboardControl._membership`  (lines 336–356)

```
async def _membership(self, workspace_id: UUID, email: str) -> Membership
```

*Call graph*: 5 external calls (__init__, HTTPException, select, workspace_tx, ws).


##### `OnboardControl._choices`  (lines 358–375)

```
async def _choices(self, email: str, domain: str) -> WorkspaceChoices
```

*Call graph*: calls 2 internal fn (_labelled, deterministic_workspace_id); 4 external calls (__init__, text, owner_tx, warn).


##### `OnboardControl._fleet`  (lines 377–384)

```
async def _fleet(self) -> Fleet
```

*Call graph*: 4 external calls (__init__, select, owner_tx, warn).


##### `OnboardControl._invitations`  (lines 386–461)

```
async def _invitations(self, after_invited_at: datetime | None=None, after_workspace_id: UUID | None=None, after_email: str | None=None) -> Invitations
```

*Call graph*: 8 external calls (__init__, __init__, HTTPException, DateTime, literal, select, tuple_, owner_tx).


### Demo Conversation Seeding
A safe kitchen-sink demo conversation can be created or cleaned up to give new workspaces representative onboarding data.

### `core/src/ufo/onboard/seed.py`

`orchestration` · `onboarding or admin-triggered seeding`

This file is like a stage crew setting up a full demo scene before anyone walks into the theater. It fabricates a completed conversation directly in the system’s durable stores: the database, the blob store for larger saved content, and the web extension’s own small store for the chat row. That matters because normal extensions are not allowed to fake completed turns or audit history; only engine-side code should write those records.

The main class, KitchenSink, builds one new conversation each time it runs. Before doing that, it looks for older conversations that were clearly created by this same seed tool. It is careful not to erase anything that a real member has spoken into or anything that has been disclosed through transcript access. This protects real workspace history.

After cleanup, it opens a new web conversation, writes three finished turns, adds terminal summaries with token and cost information, creates nested subagent conversations, attaches two sample files, and writes the transcript blob that the chat UI can read. The end result is a stable demo conversation titled “Kitchen sink” that designers and operators can use to check whether the interface draws every important conversation shape correctly.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: This helper creates a user message that includes a hidden-looking context block pointing back to the turn it belongs to. It is used so the seeded transcript can connect a spoken user request with the stored turn record.

**Data flow**: It receives a turn identifier and the words the user supposedly said. It wraps the identifier into a small context section, appends the visible message text, and returns a Message object with the user role.

**Call relations**: KitchenSink._said calls this helper while assembling the sample transcript. The helper hands back ready-made user messages that are mixed with assistant messages and tool results.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: This is the top-level routine that creates the entire kitchen sink demo conversation. Someone would use it when they want a workspace to contain one fresh, complete example conversation for testing or onboarding.

**Data flow**: It starts by creating new random identifiers for the conversation and its three turns. It clears earlier safe-to-remove seed data, opens the new conversation, adds subagent runs, adds shared files, writes the transcript into blob storage, and finally returns the new conversation identifier.

**Call relations**: This method is the main story driver for the file. It calls the cleanup, database setup, subagent, file, and transcript-building helpers in order, so the rest of the methods act like specialized crew members preparing parts of the seeded conversation.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: This removes older kitchen sink demo runs before a new one is written. It keeps the workspace from filling up with stale demo conversations while avoiding real user history.

**Data flow**: It asks _prior which old seed runs are safe to delete. For each one, it removes database rows through _drop, deletes the related web chat row from the extension store, and deletes transcript and artifact blobs from blob storage.

**Call relations**: KitchenSink.write calls this first so the new demo starts from a clean slate. It depends on _prior to decide what is safe and on _drop to remove the database side before it cleans up extension-store and blob-store leftovers.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: This searches for earlier kitchen sink runs that this seed tool created and that are still safe to erase. Its safety checks are important because a seeded conversation can become real workspace history if a member speaks in it or if transcript access has been recorded.

**Data flow**: It reads the workspace database for web conversations marked with the seed queue prefix. For each possible root conversation, it follows child subagent conversations by matching their queue keys to parent turn identifiers, collects all related turns, and then checks for real user turns or transcript access records. It returns only runs that have no such signs of real use.

**Call relations**: KitchenSink._clear calls this before deleting anything. It hands _clear a set of safe prior runs, each containing the root conversation, related conversations, and turns that belong to that seed run.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: This deletes the database records for one safe prior seed run. It also finds the blob keys for shared artifacts so the caller can remove those files from blob storage afterward.

**Data flow**: It receives a _PriorRun containing conversation and turn identifiers. Inside a database transaction, it reads artifact blob keys, deletes shared artifact rows, conversation change rows, turn rows, and conversation rows, then returns the artifact blob keys it found.

**Call relations**: KitchenSink._clear calls this for each safe old run. _drop removes the relational database records, then _clear uses the returned blob keys to clean up the corresponding stored files.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: This creates the main web conversation and its three finished turns. It also makes the conversation visible to the web surface by adding a chat row and giving it the “Kitchen sink” title.

**Data flow**: It receives the new conversation identifier and the three turn identifiers. It inserts a conversation row, inserts one completed turn for each terminal frame returned by _terminals, retitles the conversation, and writes a small chat entry containing the agent and member email into the web extension store.

**Call relations**: KitchenSink.write calls this after cleanup. It uses _terminals to get the finished-turn summaries, writes the core database records, then hands the conversation to the web surface through retitling and the extension store.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: This builds the final status summaries for the three seeded turns. These summaries include model name, token and cost numbers, and, for the last turn, a still-open question for the user.

**Data flow**: It takes no outside input beyond the KitchenSink instance. It constructs three TerminalFrame objects: two plain completed summaries and one completed summary that contains a structured user-input request with choices and a free-text field. It returns them as a tuple.

**Call relations**: KitchenSink._open calls this while inserting the three turns. The terminal frames become the stored end-state of each turn, which is what the interface later uses to display costs, status, and the pending question.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–371)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: This creates sample subagent activity beneath the main conversation. It shows that a turn can spawn a subagent, and that subagent can spawn another subagent of its own.

**Data flow**: It receives the root conversation identifier and the parent turn identifier. It creates two new subagent turn identifiers, calls _run to insert the child and grandchild subagent conversations, then writes a transcript blob for the child subagent showing a tool call and tool result.

**Call relations**: KitchenSink.write calls this after opening the main conversation. It delegates the database insertion for each subagent to _run, then writes the child transcript so the subagent view has messages to display.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 373–408)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: This inserts one completed subagent conversation and its single completed turn. It is the small building block used to make the nested subagent example.

**Data flow**: It receives a turn identifier, the parent turn identifier, a subagent profile name, and the answer text to store. It creates a new conversation identifier, inserts a subagent conversation linked to the parent, inserts one completed turn with the result encoded in its terminal frame, and returns the new subagent conversation identifier.

**Call relations**: KitchenSink._runs calls this twice: once for the child subagent and once for the grandchild. The returned conversation identifier lets _runs attach a transcript to the child subagent conversation.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 410–431)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: This attaches two sample files to one of the seeded turns: a Markdown audit and a CSV metrics file. These files let the interface demonstrate how shared artifacts appear in a conversation.

**Data flow**: It receives the turn identifier that should own the files. For each built-in sample file, it encodes the text as bytes, creates a unique blob key, writes the bytes to blob storage, and inserts a shared artifact database row with filename, media type, size, and ownership details.

**Call relations**: KitchenSink.write calls this after creating the main and subagent records. The artifact rows and blob contents it writes become attachments associated with the selected turn in the seeded conversation.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 433–499)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: This builds the visible transcript for the main kitchen sink conversation. It includes user requests, assistant replies, tool calls, tool results, a table, a quote, a code block, and a final pending-question setup.

**Data flow**: It receives the three turn identifiers. It creates a sequence of Message objects, using _framed for user messages that need to refer to a turn, and directly creating assistant, tool-use, and tool-result messages for the rest. It returns the complete ordered transcript as a tuple.

**Call relations**: KitchenSink.write calls this when it is ready to save the transcript blob. The returned messages are wrapped in a Conversation object, encoded, and stored under the transcript key for the new conversation.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-installation-lock` — The saved list of installed extensions and exact versions that should be loaded again consistently.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-surface-routing` — The mapping from outside places like web, Slack, terminal, and iMessage to the right workspace, conversation, member, and agent.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-skill-store` — The shared library of built-in and user-created skills that can be selected, checked, and loaded into a turn.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-workspace-membership-roster` — Durable workspace member records, roles/admin flags, invitations, inviter stamps, seating history, and member-local profile fields such as timezone or email lookup data.
