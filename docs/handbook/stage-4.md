# First-run onboarding and workspace provisioning  `stage-4`

This stage is the front door of a new installation. It runs during first setup, and later supports people joining or choosing workspaces. A workspace is the shared place where members, assistants, and settings live. The onboarding code creates the first workspace, adds the first administrator, creates the main assistant agent, checks required secrets, and lets installed extensions add their own pieces.

The control API is the gatekeeper used by the Rust control plane. It creates or joins workspaces, checks whether someone belongs, lists available workspaces, counts them, and pages through invitations, so these rules stay consistent. The seats code manages who may use the assistant inside a workspace, and protects against removing the last seated admin. Provisioning applies agents shipped with extensions, adopting existing ones where possible and avoiding overwriting user edits. Agent setup code reports what each shipped agent still needs, such as credentials, account connections, or schedules. Seed code adds a fresh demo conversation, after removing any older copy, so newcomers can see the portal in action.

## Files in this stage

### Initial workspace bootstrap
Creates the first workspace, admin, assistant agent, and applies shipped extension agents without overwriting local edits.

### `core/src/ufo/onboard/onboarding.py`

`orchestration` · `startup / first-run init`

This file is the “move-in checklist” for a brand-new UFO workspace. Without it, a fresh install would have no workspace to belong to, no administrator, no main agent to talk to, and no safe place for extensions to prepare themselves.

The flow is careful about order. First it checks that the chosen AI model has the needed environment secret, such as an API key. It also checks that extension onboarding can safely store any needed secrets. These checks happen before database changes, so a bad setup does not leave behind a half-created workspace.

Then it opens a database transaction, which is a safe all-or-nothing database boundary. It first looks for an existing member. If one exists, the workspace is treated as already initialized and the file refuses to create duplicates. If not, it creates a workspace, an admin member, and the default main agent.

After the core workspace exists, extension setup runs. Each extension receives its own scoped context, meaning it only sees the credential slots it declared. If an extension step fails, the failure is logged and recorded, but the whole onboarding process does not collapse. This is like letting optional appliances fail to install without undoing the house itself.

#### Function details

##### `run_onboarding_steps`  (lines 57–97)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the first-time setup steps supplied by installed extensions after the core workspace has already been created. It records whether each extension step completed or failed, while keeping one failing extension from blocking the rest.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters the workspace context, walks through each manifest, builds a scoped extension context for extensions that have onboarding steps, and calls each step’s handler. For each step, it writes an onboarding result: completed if the handler finishes, failed if it raises an error. It changes recorded onboarding status and logs problems, but it does not return a value.

**Call relations**: This is called by Onboarding.run_steps after agent provisioning has been applied. During each step, it asks context_for to create the extension-specific context, uses ws to mark which workspace is active, logs failures through the observability logger, and hands step outcomes to record_onboarding_step so the product can show or analyze what happened.

*Call graph*: called by 1 (run_steps); 4 external calls (log, record_onboarding_step, context_for, ws).


##### `Onboarding.run`  (lines 113–116)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-run onboarding sequence from start to finish. It creates the core workspace first, then runs the follow-up setup steps that depend on that workspace existing.

**Data flow**: It starts with the Onboarding object’s stored configuration, email, model choice, credentials, manifests, and reasoning setting. It calls create to produce an Onboarded result containing the workspace and member IDs. Then it passes that result to run_steps and finally returns the same Onboarded result to the caller.

**Call relations**: This is the top-level method for this file’s workflow. It delegates the durable core setup to Onboarding.create, then delegates extension and agent follow-up work to Onboarding.run_steps.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 118–124)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates only the essential, durable core of a new installation: required secret checks, workspace, first admin, and main agent. It intentionally does not depend on extension setup succeeding.

**Data flow**: It reads the onboarding object’s model, configuration, credentials, manifests, email, and reasoning setting. First it verifies the model key is available, then verifies credentials are present if extension onboarding needs them. If those checks pass, it creates the workspace data in the database and returns an Onboarded object with the new workspace ID and member ID.

**Call relations**: This is called by Onboarding.run before any extension steps run. It calls _require_model_key and _require_credentials_for_steps to fail early if setup is impossible, then calls _create_workspace to write the core records.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 126–128)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the setup work that comes after the workspace exists. This includes provisioning agents from extension manifests and then running extension-provided onboarding steps.

**Data flow**: It receives an Onboarded object with the new workspace ID. It uses the stored manifests to apply agent provisioning to that workspace, then passes the manifests, workspace ID, and credentials into run_onboarding_steps. It does not return anything; its effect is to create extension-related setup and record extension onboarding outcomes.

**Call relations**: This is called by Onboarding.run after Onboarding.create succeeds. It first hands control to AgentProvisioning for manifest-driven agent setup, then calls run_onboarding_steps so extensions can do their own first-run work.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 130–141)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops onboarding early if installed extensions have onboarding steps but the system has no credential store available. This prevents creating a workspace that immediately cannot finish required extension setup.

**Data flow**: It reads the onboarding object’s credential store, extension manifests, and configured credential key environment name. If credentials are present, it allows setup to continue. If credentials are missing and at least one manifest has onboarding steps, it raises an error explaining which environment setting must be provided. It returns nothing when the check passes.

**Call relations**: This is called by Onboarding.create before any database records are created. It is one of the preflight checks that protects _create_workspace from running when the later onboarding steps cannot safely use credentials.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 143–151)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks whether the selected AI model needs an environment-provided key, and fails early if that key is missing. This helps ensure the first real agent turn will not immediately fail because the model cannot be reached.

**Data flow**: It asks _model_key_env which environment variable name, if any, is needed for the selected model. If no key is required or the provider cannot be checked eagerly, it does nothing. If a key name is required, it asks deploy_env whether that value is set. If the value is missing, it raises a clear error message. It returns nothing when the check passes.

**Call relations**: This is called by Onboarding.create before workspace creation. It relies on _model_key_env to identify the needed secret, then uses deploy_env to check the deployment environment for that secret.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create); 1 external calls (deploy_env).


##### `Onboarding._model_key_env`  (lines 153–156)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that should hold the API key or secret for the selected model, when the core model registry knows it. If the model provider comes from an extension and resolves its key later, this may return nothing.

**Data flow**: It reads the onboarding object’s configuration, manifests, and selected model name. It builds or queries the model registry, asks which key environment variable belongs to that model, and returns either that variable name or None.

**Call relations**: This helper is called by Onboarding._require_model_key. It hands back the information that _require_model_key needs in order to decide whether to check the deployment environment.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 158–185)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the new workspace, first administrator, and main agent into the database in one safe transaction. It also prevents running first-time setup twice against the same already-initialized database.

**Data flow**: It opens a workspace database transaction and first looks for any existing member email. If a member already exists, it raises AlreadyInitialized instead of creating duplicates. If the database is empty, it generates IDs for the workspace and agent, inserts the workspace row, creates the admin member using the supplied email, inserts the main agent with the chosen model and reasoning setting, and returns an Onboarded object containing the new workspace and member IDs.

**Call relations**: This is called by Onboarding.create after all preflight checks pass. It uses workspace_tx for the database boundary, SQLAlchemy insert and select helpers for database statements, create_member for the first admin account, uuid4 for new IDs, and returns the Onboarded value used by Onboarding.run and Onboarding.run_steps.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### `core/src/ufo/runtime/kinds/provisioning.py`

`domain_logic` · `workspace provisioning during extension application`

Extensions can declare ready-made agents, like a starter kit included with an add-on. This file is the code that installs those declared agents into a workspace. Once an agent is placed in the workspace, it becomes an ordinary agent that the workspace owns. That matters because members may rename it, edit it, connect tools to it, or otherwise make it their own.

The main job here is to be helpful without being destructive. If the extension’s agent is already present, the code does not rewrite the whole row. It only refreshes two pieces that still belong to the extension’s declaration: the setup information and, if the workspace has no purpose written yet, the declared purpose. Everything else is left alone so user edits survive extension upgrades.

If an extension declares an agent whose name is already taken, this file does not replace the existing agent. It finds a safe alternate name, using the extension name as a suffix. If the provision is marked as the main agent, it adopts the workspace’s existing main agent rather than replacing its configuration. Database writes happen inside a workspace transaction, and creation uses “insert if nothing is already there” behavior so two processes racing to install the same shipped agent do not crash a user’s turn.

#### Function details

##### `AgentProvisioning.apply`  (lines 54–62)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies all agent declarations from all active extension manifests to one workspace. It is the public entry point for turning extension-declared agents into workspace agents.

**Data flow**: It receives a workspace ID and reads the manifests stored on the AgentProvisioning object. It enters that workspace’s context, walks through every manifest and every agent provision inside each manifest, and asks _one to apply each provision. It returns a tuple of ProvisionOutcome records saying, for each declared agent, whether it was created, adopted, or already present.

**Call relations**: This method starts the provisioning flow for a workspace. It uses the workspace context helper so the rest of the work happens with the correct workspace active, then delegates each individual agent decision to AgentProvisioning._one.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 64–147)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Applies one declared agent from one extension to one workspace. It decides whether the agent is already there, should adopt an existing workspace agent, or needs to be created under a free name.

**Data flow**: It receives the workspace ID, the extension manifest, and one agent provision. Inside a workspace database transaction, it first looks for an existing agent that was already provisioned by this extension under this declared name. If found, it refreshes only the extension-owned fields through _fill and reports that the agent is present. If not found, it looks for an unarchived, unprovisioned workspace agent that can be adopted: the current main agent for a main provision, or an identical same-named agent for a normal provision. If adoption is safe, it marks that row as provisioned. Otherwise it asks _free_name for an unused name, creates the row with _create, and reports it as created.

**Call relations**: AgentProvisioning.apply calls this once for every declared agent. This method is the traffic controller: it opens the transaction, reads and writes the agent table, uses _identical to avoid claiming the wrong existing agent, calls _fill for already-shipped rows, calls _free_name when a name might collide, and calls _create when a new row is needed.

*Call graph*: calls 4 internal fn (_create, _fill, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._fill`  (lines 149–189)

```
async def _fill(self, connection: AsyncConnection, shipped: sa.Row, manifest: Manifest, provision: AgentProvision) -> None
```

**Purpose**: Refreshes the small part of an already-provisioned agent that still belongs to the extension. It updates setup every time it changes, and fills in the purpose only if the workspace has not written one.

**Data flow**: It receives an open database connection, the existing shipped agent row, the manifest, and the provision. It compares the stored setup with the setup declared by the current manifest. If setup changed, it prepares a setup update. If the row has no purpose, it adds the provision’s purpose. If neither change is needed, it does nothing. If something changed, it updates the row and records the manifest version that supplied those values.

**Call relations**: AgentProvisioning._one calls this after it finds that the declared agent is already present in the workspace. This function deliberately avoids touching user-owned fields, so extension upgrades can add setup information without erasing member edits.

*Call graph*: called by 1 (_one); 2 external calls (execute, update).


##### `AgentProvisioning._free_name`  (lines 191–216)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Finds a usable agent name when the declared name might already be taken. It protects existing workspace agents by choosing a variant instead of overwriting them.

**Data flow**: It receives an open database connection, the workspace ID, the extension name, and the declared agent name. It reads all active agent names in the workspace. It then tries the declared name first, then a name with the extension added as a suffix, then numbered versions of that suffix. It returns the first name that is not already in use, or raises an error if it cannot find one within the limit.

**Call relations**: AgentProvisioning._one calls this only after it has decided that no existing row should be adopted. The returned name is handed to _create so the new shipped agent can be inserted without taking another agent’s name.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 218–275)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Creates the normal workspace agent row for an extension-shipped agent. It records where the agent came from, but otherwise gives it the same kind of database row as any other agent.

**Data flow**: It receives an open database connection, the workspace ID, the manifest, the provision, and the chosen agent name. It takes the agent settings from the provision, chooses an icon automatically if the provision did not specify one, generates a new ID, and inserts a row into the agent table with the provision identity and version recorded. The insert is written so that if another process already inserted the same provision at the same time, the operation simply does nothing instead of failing.

**Call relations**: AgentProvisioning._one calls this after _free_name has chosen a safe name. This is the final step in the “new agent needed” path, and it uses helper functions for icon selection and ID generation before writing to the database.

*Call graph*: called by 1 (_one); 4 external calls (execute, select, auto_agent_icon, uuid4).


##### `AgentProvisioning._identical`  (lines 277–290)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing workspace agent already matches what a non-main provision would create. This lets the system adopt a matching row instead of making a duplicate.

**Data flow**: It receives a database row for an existing agent and the provision being applied. It compares the stored prompt, model, reasoning setting, internet access flag, sandbox size, visibility, and tools with the provision’s specification. It returns true only when those fields match.

**Call relations**: AgentProvisioning._one uses this when it finds an unprovisioned agent with the declared name. If _identical says the row matches, _one safely adopts it; if not, _one leaves it alone and creates the shipped agent under another free name.

*Call graph*: called by 1 (_one).


### Workspace enrollment and seating
Centralizes workspace creation, joining, invitations, membership checks, and seat access rules for members and admins.

### `core/src/ufo/onboard/onboard_control.py`

`domain_logic` · `signup and internal onboarding request handling`

This file is the “front desk” for hosted onboarding, but it is not public. Every route sits under `/internal/onboard/` and must present a special bearer token, which is a secret string proving the caller is the trusted control plane. Without this file, signup would have to duplicate important rules elsewhere: how a workspace is claimed by an email or domain, who becomes the first admin, how a default agent is created, how signup credit is granted, and how public intake text is kept from becoming hidden instructions.

The main path is seating a member. The code first proves that the supplied email, domain, and signup subject agree. Then it either creates the named workspace or joins the member to an existing one, creates the default agent, optionally credits the new workspace, and stores a member model key if one was supplied.

Some reads deliberately cross workspace boundaries. For example, a person signing in may need to see every workspace their verified address can enter, and the invitation sweeper needs to find invited teammates across the whole fleet. Those reads use a special owner database transaction instead of the usual workspace-scoped one. The code logs warnings for the visible cross-workspace reads so operators can notice them.

#### Function details

##### `MemberModelKey._served`  (lines 120–123)

```
def _served(cls, provider: str) -> str
```

**Purpose**: This validator checks that a requested model provider is one the system knows how to route for a member. It stops a bad or misspelled provider name from being accepted into the onboarding request.

**Data flow**: It receives a provider name from the request body. It compares that name with the approved provider names in `MEMBER_ROUTED_SLOTS`. If the name is valid, it returns it unchanged; if not, it raises a validation error before the request reaches the seating workflow.

**Call relations**: Pydantic calls this automatically while building a `MemberModelKey` from incoming request data. Later, if seating succeeds, `OnboardControl._seat` relies on this earlier check before storing the key in the member’s credential slot.

*Call graph*: 1 external calls (values).


##### `MemberModelKey.slot`  (lines 125–126)

```
def slot(self) -> str
```

**Purpose**: This turns a provider name into the internal credential slot name where that provider’s key should be stored. Someone uses it when a member arrives with a pre-provisioned model key.

**Data flow**: It reads the validated provider on the `MemberModelKey`. It searches the configured slot-to-provider mapping and returns the slot whose provider matches. Nothing is written here; it is just a lookup from friendly provider name to internal storage location.

**Call relations**: After `OnboardControl._seat` has created or found the member, it calls this method to decide which credential slot to write. The returned slot is then passed to `member_slot`, which builds the exact member-specific credential address.

*Call graph*: 1 external calls (items).


##### `_inert`  (lines 193–207)

```
def _inert(answer: str) -> str
```

**Purpose**: This makes public intake-form text safe to place near a system prompt by defusing the prompt variable syntax `{{...}}`. It prevents a stranger from accidentally or deliberately breaking every future prompt render for the workspace.

**Data flow**: It receives one text answer from the intake form. It repeatedly turns doubled braces like `{{` and `}}` into single braces until no doubled brace remains. It returns text that still looks close to what the person typed, but no longer contains live prompt-variable markers.

**Call relations**: `agent_prompt` calls this for the business and goals fields before those answers are wrapped as untrusted text. It is a small safety step inside the larger process of building the first agent prompt for a new workspace.

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 210–229)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

**Purpose**: This builds the default prompt for the main agent in a newly created workspace. If signup supplied intake-form answers, it includes them carefully as untrusted background information, not as instructions.

**Data flow**: It receives either no profile or a profile containing business and goals text. With no profile, it returns the normal default agent prompt. With a profile, it first runs the answers through `_inert`, then passes the combined intake text through `wall`, which labels it as untrusted data, and finally returns a full prompt containing the default prompt plus that protected context.

**Call relations**: `OnboardControl._seat` calls this when it inserts the workspace’s default main agent. This function hands off to `_inert` and `wall` so public form content cannot silently take control of the system prompt.

*Call graph*: calls 1 internal fn (_inert); called by 1 (_seat); 1 external calls (wall).


##### `_labelled`  (lines 232–268)

```
def _labelled(rows: Sequence[sa.RowMapping], subject: str) -> list[WorkspaceChoice]
```

**Purpose**: This turns raw database rows about possible workspaces into the simple choice list shown during sign-in. It also refuses to guess if one signup subject appears to map to more than one workspace.

**Data flow**: It receives rows from the workspace-choice query and the verified signup subject. For each workspace, it derives the human label from the first member’s email and workspace id, filters subject matches so only the correct subject counts, detects ambiguous subject matches, and marks whether the email is already a member. It returns a list of `WorkspaceChoice` objects, adding a short UUID prefix to labels that would otherwise look identical.

**Call relations**: `OnboardControl._choices` runs the cross-workspace query and then calls `_labelled` to convert database-shaped results into user-facing choices. `_labelled` depends on `workspace_subject` for the naming rule and raises an HTTP conflict when the data cannot produce a safe single answer.

*Call graph*: called by 1 (_choices); 3 external calls (__init__, HTTPException, workspace_subject).


##### `_verified_signup`  (lines 271–290)

```
def _verified_signup(email: str, domain: str | None, signup_subject: str | None) -> tuple[str, str, str]
```

**Purpose**: This normalizes and checks the identity facts supplied by the sign-in gateway: email, domain, and signup subject. It makes sure the claimed domain or subject really comes from the verified email address.

**Data flow**: It receives an email plus optional domain and signup subject. It trims and lowercases them, derives the email’s domain, rejects mismatches, fills in the subject when it is omitted, and returns three trusted strings: normalized member email, verified domain, and signup subject.

**Call relations**: Both `OnboardControl._seat` and `OnboardControl._choices` call this before doing any workspace lookup or write. If the identity facts do not agree, it stops the request immediately with an HTTP error rather than letting bad data influence workspace selection.

*Call graph*: called by 2 (_choices, _seat); 2 external calls (HTTPException, email_domain).


##### `OnboardControl.router`  (lines 300–307)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the private FastAPI router for onboarding. It declares the five internal routes and attaches the token guard to all of them.

**Data flow**: It starts with the `OnboardControl` instance, including its configured control token. It creates a router with the `/internal/onboard` prefix, adds the guard dependency, registers the seat, membership, choices, fleet, and invitations handlers, and returns the finished router for the application to mount.

**Call relations**: Application setup calls this when wiring HTTP routes. Once mounted, incoming internal requests are first checked by `OnboardControl._guard` and then dispatched to the matching route method.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `OnboardControl._guard`  (lines 309–311)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This is the security gate for every route in this file. It refuses requests that do not carry the exact onboarding control token.

**Data flow**: It receives the HTTP `Authorization` header, or an empty string if the header is missing. It compares that value with `Bearer <control_token>`. If they match, it returns nothing and the request continues; otherwise, it raises a 401 error.

**Call relations**: `OnboardControl.router` installs this as a dependency on the whole router. That means `_seat`, `_membership`, `_choices`, `_fleet`, and `_invitations` are only reached after this guard has accepted the request.

*Call graph*: 1 external calls (HTTPException).


##### `OnboardControl._seat`  (lines 313–406)

```
async def _seat(self, request: SeatRequest) -> EnsuredWorkspace
```

**Purpose**: This creates or joins the workspace named by a verified signup, seats the member in it, creates the default agent, grants signup credit only for a newly founded workspace, and optionally stores the member’s model key. It is the central onboarding write path.

**Data flow**: It receives a `SeatRequest` containing workspace id, email identity facts, optional intake profile, and optional model key. It verifies the identity with `_verified_signup`, opens a workspace-scoped database transaction, tries to insert the workspace, locks that workspace row, checks that the workspace still belongs to the same signup subject, creates or finds the member, inserts the default main agent using `agent_prompt`, credits the workspace if this call founded it, reads whether the member is an admin, and commits. After the transaction, if a model key was supplied, it writes that key to the member’s credential slot. It returns an `EnsuredWorkspace` saying which workspace was used, whether the member is an admin, and whether this was the founding sign-in.

**Call relations**: The `/seat` route calls this after `_guard` accepts the request. It coordinates many lower-level pieces: signup verification, database inserts and locks, member creation, agent prompt construction, billing credit, reserve setup, and credential storage. It hands back the final facts the caller needs to finish the sign-in flow.

*Call graph*: calls 2 internal fn (_verified_signup, agent_prompt); 14 external calls (__init__, HTTPException, insert, select, workspace_tx, member_slot, credit, set_reserve, create_member, signup_workspace_id (+4 more)).


##### `OnboardControl._membership`  (lines 408–428)

```
async def _membership(self, workspace_id: UUID, email: str) -> Membership
```

**Purpose**: This checks whether an email is still a member of one specific workspace and whether that member is an admin. It is used after a user chooses an existing workspace, so a removed member is not silently recreated.

**Data flow**: It receives a workspace id and email. It normalizes the email, opens a transaction scoped to that workspace, and looks for a matching member row. If found, it returns a `Membership` object with the admin flag. If not found, it raises a 404 error saying the person is no longer a member.

**Call relations**: The `/membership` route calls this after token checking. It uses the normal workspace-scoped path, unlike the cross-workspace listing routes, because by this point the caller has already selected one workspace.

*Call graph*: 5 external calls (__init__, HTTPException, select, workspace_tx, ws).


##### `OnboardControl._choices`  (lines 430–455)

```
async def _choices(self, email: str, domain: str | None=None, signup_subject: str | None=None) -> WorkspaceChoices
```

**Purpose**: This lists every workspace a verified address may enter: workspaces where the email is already a member, plus the deterministic workspace for its verified signup subject. It helps the sign-in flow show the user the right doors without guessing.

**Data flow**: It receives an email and optional domain or signup subject. It verifies and normalizes them with `_verified_signup`, logs that a cross-workspace read is happening, runs a special owner-level SQL query to find matching memberships and subject matches, then passes those rows to `_labelled`. It returns a `WorkspaceChoices` object containing clean labels and membership flags.

**Call relations**: The `/choices` route calls this when a verified person is signing in and needs to know where they can go. It hands raw database results to `_labelled`, which applies the final naming and ambiguity rules before the answer leaves this service.

*Call graph*: calls 2 internal fn (_labelled, _verified_signup); 5 external calls (__init__, text, owner_tx, warn, signup_workspace_id).


##### `OnboardControl._fleet`  (lines 457–464)

```
async def _fleet(self) -> Fleet
```

**Purpose**: This returns the total number of workspaces in the database. It is a small health or deployment-gate read that proves this internal path can reach the core database.

**Data flow**: It receives no user-specific input. It logs that a cross-workspace read is happening, opens an owner-level transaction, counts rows in the workspace table, and returns that count inside a `Fleet` object.

**Call relations**: The `/fleet` route calls this after the token guard. It uses `owner_tx` because counting all workspaces necessarily looks outside any single workspace.

*Call graph*: 4 external calls (__init__, select, owner_tx, warn).


##### `OnboardControl._invitations`  (lines 466–539)

```
async def _invitations(self, after_invited_at: datetime | None=None, after_workspace_id: UUID | None=None, after_email: str | None=None) -> Invitations
```

**Purpose**: This returns one page of teammate invitations across all workspaces, ordered from oldest to newest. It lets a repeated background sweep send or inspect invitations without loading the whole table at once.

**Data flow**: It receives an optional cursor made of three parts: invitation time, workspace id, and email. It rejects partial cursors, builds a database query for invited members joined to their inviter and the workspace’s first member, applies the cursor if present, limits the page size, and runs the query through an owner-level transaction. It converts each row into an `Invitation` with workspace id, invited email, inviter email, workspace label, and invitation time, then returns them inside `Invitations`.

**Call relations**: The `/invitations` route calls this for the invitation sweeper. It does not log every call because the sweep repeats forever, but it still uses `owner_tx` because invitations are gathered across the whole fleet rather than inside one selected workspace.

*Call graph*: 9 external calls (__init__, __init__, HTTPException, DateTime, literal, select, tuple_, owner_tx, workspace_subject).


### `core/src/ufo/runtime/seats.py`

`domain_logic` · `cross-cutting: onboarding, member admission, each agent turn, admin seat changes, and background workspace scans`

A “seat” here means permission for a workspace member to use the agent. The member record itself is treated like an identity record: it stays even if access is removed. That matters because the system may still need to remember who the person is, while refusing to answer them until an admin restores their seat.

The file is the central rulebook for that access. It can answer simple questions such as “is this authority still live?”, “are all these members still seated?”, and “who is currently seated?” It also provides the only safe ways to grant a seat, revoke a seat, create a member, and look up members by email.

The important safety rule is that the last seated admin cannot be unseated. Since seat management happens through chat, removing the final admin would be like throwing away the only key to the building. To make that rule hold even when two changes happen at the same time, revocation locks the workspace row before counting admins.

The file also standardizes email handling. It checks that an email really looks like one local name plus one domain, lowercases it, and uses that same shape rule for member creation and workspace-domain lookup. This avoids accidentally creating duplicate or unreachable member records.

#### Function details

##### `SeatSnapshot.seated`  (lines 58–59)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently have access. It gives callers a quick summary without making another database query.

**Data flow**: It reads the snapshot’s list of member entries, checks each entry’s seated flag, and returns the number marked as seated. It does not change the snapshot or the database.

**Call relations**: This is used after a snapshot has already been built, usually when a caller wants to display or reason about the current seat count. It depends only on the data inside SeatSnapshot.


##### `Seats.admits`  (lines 70–87)

```
async def admits(self, connection: AsyncConnection, authority: ExecutionAuthority) -> bool
```

**Purpose**: Answers the core yes-or-no question: may this authority act in this workspace right now? Workspace-wide authority is always accepted, while member authority must still belong to a seated member in this workspace.

**Data flow**: It receives a database connection and an execution authority. If the authority is for the whole workspace, it returns true. If it names a member, it looks up that member in this workspace and checks whether their seated_at value is present. It returns true only when the row exists and the seat has not been revoked.

**Call relations**: This is a boundary check used wherever the system must confirm that an action is still allowed. It asks the database through SQLAlchemy, a library that builds and runs SQL queries, and turns the result into a simple boolean answer.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 89–109)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether a whole group of member IDs are still seated. This is useful for turns or jobs that involve more than one speaker and must stop if any involved member loses access.

**Data flow**: It receives a connection and a collection of member IDs. If the collection is empty, it returns true. Otherwise it counts, in one database query, how many of those IDs are seated members of this workspace, then compares that count with the number requested.

**Call relations**: This function supports repeated liveness checks during agent work, parked turns, and dispatch sweeps. Rather than checking one member at a time, it asks the database one grouped question so the caller gets one clear yes-or-no answer.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 111–134)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a read-only picture of every member in the workspace and whether each one is seated and an admin. This is the file’s “show me the roster” operation.

**Data flow**: It receives a database connection, reads member IDs, emails, seat timestamps, and admin flags for this workspace, ordered by creation time. It turns each database row into a SeatEntry and wraps them all in a SeatSnapshot.

**Call relations**: Callers use this when they need to display or inspect the current seat state. It hands off raw database rows into the simple data objects SeatEntry and SeatSnapshot so the rest of the program does not need to interpret database columns directly.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 136–146)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores access for an existing workspace member identified by email. If the member is already seated, it quietly does nothing.

**Data flow**: It receives a connection and an email address. It first uses Seats._member_by_email to find the matching member in this workspace. If that member already has a seated_at timestamp, nothing changes. Otherwise it updates the member row with the current time, marking the seat as active again.

**Call relations**: This is called when an admin wants to give access back. It relies on Seats._member_by_email for the scoped lookup, then writes the seat change through the database.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 148–171)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes access from an existing workspace member identified by email. It refuses to remove the final seated admin, because that would leave nobody able to restore seats through chat.

**Data flow**: It receives a connection and an email address. First it locks the workspace row so concurrent revocations line up instead of racing. Then it finds the member by email. If the member is already unseated, it does nothing. If the member is an admin and is the only seated admin left, it raises LastAdminSeatRevocation. Otherwise it clears seated_at on the member row.

**Call relations**: This is the admin-facing removal path. It calls Seats._member_by_email to identify the member and Seats._seated_admin_count to protect the last admin. It updates only the member’s seat status; running turns are stopped later by admission and per-round checks.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 173–190)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds one member in this workspace by email and returns the details needed for seat changes. It raises a clear error if the email is not a member of this workspace.

**Data flow**: It receives a connection and an email. It trims and lowercases the email for comparison, queries only this workspace’s member table, and returns the member ID, current seat timestamp, and admin flag. If there is no matching row, it raises UnknownMember.

**Call relations**: This helper is used by Seats.grant and Seats.revoke before they change a seat. Keeping the lookup here ensures both paths use the same workspace-scoped email rule.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 192–201)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in this workspace currently still have seats. It exists to protect the rule that at least one seated admin must remain.

**Data flow**: It receives a database connection, counts member rows in this workspace where the member is both seated and marked as an admin, and returns that count as an integer.

**Call relations**: Seats.revoke calls this after locking the workspace row. The count tells revoke whether removing the requested admin would strand the workspace with no seated administrator.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 204–217)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts the domain part of a well-formed email address, such as example.com from name@example.com. If the value is malformed, it returns an empty string so bad addresses cannot accidentally match a workspace or create a member.

**Data flow**: It receives a string, trims it, lowercases it, and checks that it contains exactly one at-sign with text on both sides and no whitespace. If the address passes, it returns the domain. Otherwise it returns an empty string.

**Call relations**: This is the shared email shape gate for create_member, workspace_subject, workspace_domain, and workspace_by_domain. Because all these paths call the same helper, they agree on what counts as a usable email domain.

*Call graph*: called by 4 (create_member, workspace_by_domain, workspace_domain, workspace_subject).


##### `signup_workspace_id`  (lines 220–222)

```
def signup_workspace_id(subject: str) -> UUID
```

**Purpose**: Creates the stable workspace ID that belongs to a hosted signup subject. The same subject always produces the same UUID, which is a standard unique identifier.

**Data flow**: It receives a subject string, lowercases it, and feeds it into uuid5, a deterministic UUID generator. The output is a UUID that can be recomputed later from the same subject.

**Call relations**: Workspace-subject helpers call this to tell whether a workspace is keyed by a founder’s exact email address or by a domain. It is the shared conversion rule, so different callers do not invent different IDs.

*Call graph*: called by 3 (workspace_by_domain, workspace_domain, workspace_subject); 1 external calls (uuid5).


##### `workspace_subject`  (lines 225–234)

```
def workspace_subject(first_email: str, workspace_id: UUID) -> str
```

**Purpose**: Returns the signup subject that represents a workspace. For a personal-email workspace it returns the founder’s exact email; otherwise it returns the founder’s email domain.

**Data flow**: It receives the first member’s email and the workspace ID. It checks whether that exact email would generate the workspace ID. If so, it returns the email. If not, it extracts and returns the email domain.

**Call relations**: This function combines signup_workspace_id and email_domain so labels, invitations, and seat checks describe the same workspace subject. It prevents one part of the system from calling a workspace by a domain while another calls it by a personal address.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id).


##### `workspace_domain`  (lines 237–255)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the email domain that represents a workspace, but only when the workspace is actually domain-based. Personal-email workspaces return no domain so public email providers do not become auto-join keys.

**Data flow**: It receives a connection and workspace ID. It reads the first member’s email, derives its domain, and checks whether the workspace was instead created from the exact email address. If the workspace is domain-based, it returns the domain; otherwise it returns None.

**Call relations**: This function uses the same email_domain and signup_workspace_id rules as the rest of the file. It is used when code needs to know whether a workspace has a real organization-style domain identity.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `workspace_by_domain`  (lines 258–291)

```
async def workspace_by_domain(connection: AsyncConnection, domain: str) -> UUID | None
```

**Purpose**: Looks up which workspace, if any, is addressed by an email domain. It deliberately skips personal-email workspaces so a shared provider domain like gmail.com does not point to one user’s workspace.

**Data flow**: It receives a connection and a domain string. It normalizes the domain by turning it into a fake email and passing it through email_domain. If valid, it searches for workspaces whose first member email uses that domain, ordered consistently, then returns the first workspace that is not keyed by the founder’s exact email. If none qualifies, it returns None.

**Call relations**: This function is part of domain-based workspace discovery. It relies on email_domain for safe normalization, signup_workspace_id to filter out personal workspaces, and database queries to inspect each workspace’s earliest member.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `member_by_email`  (lines 294–310)

```
async def member_by_email(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID | None
```

**Purpose**: Finds the member ID for an email inside one specific workspace. It is a lookup only; it never creates a member or grants a seat.

**Data flow**: It receives a connection, workspace ID, and email. It trims and lowercases the email for comparison, queries only that workspace’s members, and returns the matching member ID if found. If there is no match, it returns None.

**Call relations**: This is used by routes or admission code that already know the workspace and need to connect a verified email address to a member record. The query is scoped to the workspace from the start, so it never briefly treats another workspace’s member as relevant.

*Call graph*: 2 external calls (execute, select).


##### `member_is_admin`  (lines 313–324)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a seated member is an admin in a given workspace. Unseated admins do not count for this answer.

**Data flow**: It receives a connection, workspace ID, and member ID. It looks for a member row matching all three conditions: same workspace, same member, currently seated. If the row says the member is an admin, it returns true; otherwise it returns false.

**Call relations**: Callers use this when an action requires admin power. It keeps the admin check tied to live seat access, so a revoked admin record does not continue to authorize changes.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 327–398)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False, invited_by: UUID | None=None) -> UUID
```

**Purpose**: Creates a member row in a workspace, which also gives that member a seat by default. This is the single shared creation path so onboarding, invitations, and verified joins all follow the same rules.

**Data flow**: It receives a connection, workspace ID, email, and optional admin and invitation information. It first validates the email shape with email_domain, lowercases the email, and locks the workspace row so simultaneous creations wait their turn. Then it inserts a new member with a fresh UUID. If another process already created the same workspace-and-email row, the insert does nothing and the function returns the existing member ID instead.

**Call relations**: This is the main write path for adding people. It calls email_domain to enforce the shared email rule and uses uuid4 to create a random new member ID. Its conflict behavior makes races safe: two callers adding the same person end up with one member row.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 401–409)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate set of all workspaces that have at least one member. This is meant for jobs that need to report on or process member-related workspace state.

**Data flow**: It creates a small query function that selects distinct workspace IDs from the member table, then passes that query to owner_candidates. The result is a WorkspaceCandidates object the job system can use.

**Call relations**: Extensions or background jobs can declare this as their workspace source without reaching directly into the core member table. It hands the actual SQL-building work to member_workspaces.with_a_member and wraps it with owner_candidates.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 406–407)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query for workspaces that contain at least one member. It is the inner query used by member_workspaces.

**Data flow**: It takes no outside input directly. It constructs a SQL select that reads distinct workspace IDs from the member table and returns that query object for someone else to run.

**Call relations**: member_workspaces gives this query builder to owner_candidates. The query is intentionally broad and simple: any workspace with any member becomes a candidate.

*Call graph*: 1 external calls (select).


### Demo content and agent readiness
Seeds the showcase demo conversation and reports what credentials, connections, or schedules each shipped agent still needs.

### `core/src/ufo/onboard/seed.py`

`orchestration` · `onboarding or demo seed action`

This file is a seeder: it fabricates a complete conversation so designers, developers, or operators can open the product and see many conversation features at once. The demo includes normal chat messages, tool calls, tool results, subagent runs, attached files, cost and token information, and a still-open question asking the user for choices. Without this file, someone testing the portal would have to manually create a complicated conversation every time they wanted to check how the interface looks.

The main class, `KitchenSink`, works like a careful stage crew. First it looks for older demo runs that it created itself. It only deletes runs marked with its own private seed prefix, and it refuses to delete anything if a real user has spoken in it or if there is a transcript access record. That protects real workspace history from being erased by a demo reset.

After cleanup, it writes database rows for a web conversation and its finished turns. It then creates nested subagent conversations, stores transcript files in the workspace blob store, and records shared artifacts such as a Markdown audit and a CSV metrics file. Finally, it stores the web chat row that lets the portal find and show the conversation. The result is one durable, realistic conversation named “Kitchen sink” that can be regenerated repeatedly.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: This small helper makes a user message that includes a hidden reference to the turn it belongs to. It lets the transcript connect what the user said with the finished turn record stored elsewhere.

**Data flow**: It receives a turn ID and the user’s visible words. It wraps the turn ID in a small context block, appends the user’s text, and returns a `Message` object marked as coming from the user.

**Call relations**: It is used by `KitchenSink._said` while building the main transcript. That transcript needs these framed user messages so each user request lines up with the turn IDs created earlier.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: This is the main entry point for creating the demo conversation. It clears any safe-to-remove older seed data, writes a new conversation, adds subagent runs and files, stores the transcript, and returns the new conversation ID.

**Data flow**: It starts with the workspace, agent, member, email, and blob store already saved on the `KitchenSink` object. It creates fresh IDs, calls helper steps to delete old demo data and write new database and blob records, then returns the ID of the new root conversation.

**Call relations**: This function coordinates the whole seeding flow. It calls `_clear` first for cleanup, then `_open` for the main conversation rows, `_runs` for subagent examples, `_files` for shared artifacts, and `_said` to build the transcript that is encoded and stored in the blob store.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: This removes older kitchen-sink demo runs that are safe to delete. It keeps the demo from piling up duplicate conversations every time the seed is run.

**Data flow**: It asks `_prior` for earlier runs that were created by this seed and have not been touched like real workspace history. For each one, it deletes database rows through `_drop`, removes the web chat row from the extension store, and deletes transcript and artifact blobs from storage.

**Call relations**: `KitchenSink.write` calls this before creating the new demo. It relies on `_prior` to decide what can be deleted and `_drop` to remove the database part, then it finishes cleanup in the extension store and blob store.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: This finds previous kitchen-sink demo runs that belong to this workspace and are safe for the seeder to remove. Its main job is caution: it separates disposable demo data from anything that may have become real history.

**Data flow**: It reads conversation, turn, and transcript-access tables inside a workspace database transaction. It starts from web conversations marked with the seed prefix, follows any subagent conversations connected through queue keys, collects their turns, and then rejects any run where a non-seed turn or transcript disclosure exists. It returns a tuple of `_PriorRun` records describing only the removable runs.

**Call relations**: `_clear` calls this when it needs a cleanup list. The returned `_PriorRun` objects are passed to `_drop`, and their conversation IDs are also used to delete matching transcript blobs.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: This deletes the database rows for one old seed run. It removes the conversation tree, its turns, change records, and shared artifact records, while reporting which blob files still need to be deleted from storage.

**Data flow**: It receives a `_PriorRun` containing conversation IDs and turn IDs. Inside a database transaction, it first reads blob keys for shared artifacts, then deletes artifact rows, conversation change rows, turn rows, and conversation rows. It returns the artifact blob keys so the caller can delete the actual stored files afterward.

**Call relations**: `_clear` calls this for each old run selected by `_prior`. `_drop` handles only database cleanup; `_clear` uses its returned artifact keys to finish cleanup in the blob store.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: This creates the main web conversation and its three completed turns. It also gives the conversation its display title and writes the chat lookup row used by the web portal.

**Data flow**: It receives a new conversation ID and three turn IDs. It inserts one conversation row and three finished turn rows into the database, using `_terminals` to fill in the final status, model, token, cost, and question data for each turn. After the transaction, it retitles the conversation to “Kitchen sink” and stores a web chat row containing the agent ID and member email.

**Call relations**: `KitchenSink.write` calls this after cleanup. It calls `_terminals` to get the finished-turn summaries, then hands off to the surface title helper and the scoped extension store so the web UI can discover and label the conversation.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: This builds the final result summaries for the demo’s three main turns. These summaries are what the system records when a turn is finished.

**Data flow**: It takes no outside input beyond the `KitchenSink` object. It returns three `TerminalFrame` objects: two plain completed turns with model, token, and cost data, and a third completed turn that includes a pending user-input question with options, multi-select choices, and a free-text prompt.

**Call relations**: `_open` calls this while inserting turn rows. The returned frames become the stored terminal data that the portal later draws as cost lines, completion state, and the open question UI.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–371)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: This adds subagent examples to the demo conversation. It creates a child subagent run, a deeper nested subagent run, and a transcript showing tool activity inside the child run.

**Data flow**: It receives the root conversation ID and the parent turn ID that should appear to spawn the subagent. It creates fresh IDs for a child and grandchild run, calls `_run` to write their conversation and turn rows, then stores a transcript blob for the child run containing user, assistant, tool-use, and tool-result messages.

**Call relations**: `KitchenSink.write` calls this after the main conversation exists. It delegates each database insertion to `_run`, then writes transcript content using the transcript encoding helpers so the subagent surface has something realistic to display.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 373–408)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: This writes one completed subagent conversation and its single completed turn. It is used to make the demo show subagents, including a subagent spawned by another subagent.

**Data flow**: It receives a turn ID, the parent turn ID, a subagent profile name, and the answer text the subagent should report. It creates a new conversation ID, inserts a subagent conversation whose queue key points back to the parent, inserts one completed turn with a JSON-like result in its terminal frame, and returns the new subagent conversation ID.

**Call relations**: `_runs` calls this once for the child subagent and once for the grandchild subagent. The returned child conversation ID is then used by `_runs` to store a transcript for that subagent run.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 410–431)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: This attaches example files to one of the demo turns. The files let the portal show shared artifacts such as a Markdown report and a CSV data file.

**Data flow**: It receives the turn ID that should own the files. For each built-in file body, it converts the text to bytes, creates a unique blob key, writes the bytes to the blob store, and inserts a shared-artifact database row with the filename, media type, size, and timestamps.

**Call relations**: `KitchenSink.write` calls this after creating the main conversation and subagent runs. The shared artifact rows it writes point to blobs that the UI can later list and open as turn attachments.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 433–499)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: This builds the visible transcript for the main demo conversation. It is the scripted conversation that users will read in the portal.

**Data flow**: It receives the three turn IDs created for the main conversation. It returns a sequence of `Message` objects: framed user requests, assistant tool calls, user tool results with activity text, assistant explanations, a Markdown table and code block, and a final assistant message asking for more input.

**Call relations**: `KitchenSink.write` calls this when it is ready to store the main transcript. It uses `_framed` for the user messages that need turn references, then `write` encodes the returned conversation and saves it under the transcript key for the root conversation.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/runtime/kinds/agent_setup.py`

`domain_logic` · `request handling`

A shipped agent arrives with a job to do, but it may still need permission to use an account, access to a shared secret, or a standing order such as a scheduled task. This file is the shared language for those needs. It defines small data shapes, using Pydantic models (Python classes that validate their fields), for the setup declaration and for the live setup state shown to a user.

The file separates what the app asks for from what the workspace has already provided. For example, an agent may declare that it can use a Gmail connection. The live state then says whether the current member actually has a usable Gmail grant. Likewise, a credential can be filled either by a workspace-stored secret or by a deployment-provided environment value, so the setup screen does not wrongly say a working app is unready.

Schedules get special care. If an agent says it needs a clock to wake it, it must also offer the schedule options the member can pick. That keeps the setup screen actionable: the user is not told “you need a schedule” without being offered one.

The main public action is `setup_state`, which reads the agent’s declaration, checks database records and standing orders, and returns one complete setup picture.

#### Function details

##### `SetupCadence._hourly_spans_every_day`  (lines 45–53)

```
def _hourly_spans_every_day(self) -> 'SetupCadence'
```

**Purpose**: This validation step makes sure a schedule cadence is understandable before it is accepted. In particular, it prevents an hourly cadence from also naming weekdays, because an hourly schedule has no single local hour that can anchor a weekday conversion.

**Data flow**: It starts with a `SetupCadence` object containing an hour, minute, and optional weekdays. It checks whether the hour is missing, meaning “every hour,” and rejects any weekdays in that case. It also checks that all weekday numbers are in the allowed range, where 0 means Sunday and 6 means Saturday. If everything is valid, the same cadence object continues unchanged; if not, validation stops with an error.

**Call relations**: This runs automatically when a `SetupCadence` is created or validated. Other setup models can then trust that each cadence they carry is coherent before it is displayed or converted into an actual schedule elsewhere.


##### `AgentSetup._a_clock_need_carries_its_offer`  (lines 135–152)

```
def _a_clock_need_carries_its_offer(self) -> 'AgentSetup'
```

**Purpose**: This validation step keeps an agent’s setup declaration honest about scheduled tasks. If an agent says it needs a clock-based standing order, it must also include the schedule offer that lets the member arm it; and if it offers a schedule, it must declare that scheduled-task need.

**Data flow**: It receives an `AgentSetup` object after its fields have been filled. It looks at the declared standing-order kinds and the optional schedule offer. If `scheduled_task` is listed without a schedule, or a schedule is present without `scheduled_task`, it raises an error. Otherwise, it returns the same setup object as valid.

**Call relations**: This runs automatically during `AgentSetup` validation, including when `setup_state` reads a saved setup declaration from the database and turns it back into an `AgentSetup`. It protects later setup-screen logic from impossible combinations, such as a schedule picker with nothing to arm.


##### `_armed`  (lines 236–248)

```
async def _armed(kind: str, wanted: AgentSetup, armed: Armed) -> ArmedOrder
```

**Purpose**: This helper asks whether the agent already has the standing order it needs, such as a scheduled task. For scheduled tasks, it asks about the app’s specific named task rather than any schedule of the same kind, so one feature is not mistaken for another.

**Data flow**: It receives a standing-order kind, the agent’s declared setup, and an `armed` callback supplied by the caller. If the kind is `scheduled_task` and the setup includes a schedule, it passes the schedule’s fixed name to the callback. Otherwise, it asks only whether any order of that kind exists for this agent. It returns an `ArmedOrder`, which says whether the order is held and may include the cron schedule string.

**Call relations**: `setup_state` calls this while building the live setup state. `_armed` does not know how extension-owned standing orders are stored; instead, it delegates that question to the caller-provided `armed` function and standardizes which name should be asked about.

*Call graph*: called by 1 (setup_state).


##### `setup_state`  (lines 251–347)

```
async def setup_state(agent_id: UUID, member_id: UUID, *, armed: Armed) -> SetupState
```

**Purpose**: This is the main reader for an agent’s setup screen. Given an agent and a member, it returns the full current picture: which requested connectors are granted, which credentials are filled, which standing orders are armed, and what instructions or schedule options should be shown.

**Data flow**: It takes an agent ID, a member ID, and an `armed` callback that can answer questions about standing orders. It opens a workspace database transaction, reads the agent’s saved setup declaration, and returns an empty setup state if there is no declaration. If setup exists, it validates it, reads connection grants that are usable by this member, reads any stored credentials for the requested slots, and then leaves the database transaction. It also treats deployment environment values as filled credentials when available. Finally, it builds connector rows, credential rows, and standing-order rows, asking `_armed` about each standing order, and returns a `SetupState` object for the surface to display.

**Call relations**: This function is the file’s main orchestration point for setup status. It calls the database transaction helper and SQL-building functions to read saved facts, uses `AgentSetup` validation to understand the declaration, calls `_armed` for extension-owned standing orders, and creates the small state objects that the setup UI or portal can show to the member.

*Call graph*: calls 1 internal fn (_armed); 9 external calls (__init__, __init__, __init__, __init__, or_, select, workspace_tx, deploy_env, ws_current).

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-extension-store` — The per-workspace saved data that extensions use to remember their own settings and state.
- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-workspace-member-seat-state` — The shared record of workspaces, members, admins, invitations, seats, and workspace-level limits.
- `reg-auth-tokens-sessions` — The login, surface, sandbox, and signing tokens that prove who a request belongs to and what it may access.
- `reg-surface-routing-state` — The saved routing information that maps web, Slack, iMessage, terminal, hosted app, and public-link traffic to the right workspace and conversation.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-product-census-telemetry` — Derived product analytics/census state summarizing workspace activity, onboarding progress, tool connections, and payment funnel status for dashboards.
