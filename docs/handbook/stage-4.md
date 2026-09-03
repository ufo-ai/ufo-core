# First-run onboarding, workspace creation, and shipped-agent provisioning  `stage-4`

This stage happens near the end of startup, when the system is ready to turn an empty installation into something people can use. It is the “move-in day” for a new workspace. The main onboarding flow creates the first workspace, adds the first administrator, checks that required secrets are present, creates the main assistant agent, and lets installed extensions run their own setup steps.

A small package marker makes this onboarding code importable by the rest of the project. The private onboarding control API is the trusted doorway for workspace and sign-in setup. It can create or find workspaces, seat members, list login choices, count workspaces, and read invitations, so these rules are not scattered across edge services.

Shipped-agent provisioning then turns agent declarations from installed extensions into real workspace agents, without overwriting agents that users changed or made themselves. Agent setup describes what those agents still need, such as connected accounts, credentials, or schedules, and reports that status to setup screens. A demo seeder can also create a rich sample conversation for testing and demos.

## Files in this stage

### Onboarding entrypoints
Package-level onboarding entrypoints coordinate first-run setup and expose the importable onboard module.

### `core/src/ufo/onboard/onboarding.py`

`orchestration` · `first-run startup initialization`

This file is the “move-in checklist” for a brand-new UFO installation. Without it, a fresh database would have no workspace, no admin user, no main agent to talk to, and extensions would not get a clean chance to prepare themselves.

The central class is `Onboarding`, which is used by the first-run command. It first checks that the chosen model can actually be used. If the model needs an environment variable for its API key, this file makes sure that key is present before creating anything permanent. It also checks whether installed extensions have onboarding steps that need a credential store, so setup does not leave behind a half-created workspace.

Once the checks pass, it opens a database transaction and creates the core records: one workspace, one admin member, and one main agent with the default name, prompt, icon, model, and reasoning setting. If the database already has a member, it raises `AlreadyInitialized` instead of creating duplicates.

After the core setup exists, it provisions agents from installed extensions and runs each extension’s onboarding steps inside the new workspace. Extension failures are logged and skipped, so one broken add-on does not destroy the basic workspace or stop other extensions from trying.

#### Function details

##### `run_onboarding_steps`  (lines 51–79)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the setup steps supplied by installed extensions for a newly created workspace. Each extension gets a scoped context, meaning it only sees the credential slots it declared, like giving each add-on its own labeled toolbox.

**Data flow**: It receives the installed extension manifests, the workspace ID, and an optional credential store. It enters the workspace context, looks through each manifest, builds the right extension context for extensions that have steps, and awaits each step’s handler. It does not return a value; its visible effects are whatever successful extension steps do, plus log messages for skipped or failed steps.

**Call relations**: This is called after the core workspace has already been created by `Onboarding.run_steps`. During that later setup phase, it uses `ws` to mark which workspace is active, `context_for` to prepare the extension’s view of credentials, and `log` to record skipped or failed extension setup without stopping the whole onboarding flow.

*Call graph*: called by 1 (run_steps); 3 external calls (log, context_for, ws).


##### `Onboarding.run`  (lines 95–98)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-time setup from start to finish. It creates the core workspace first, then runs the extra setup supplied by extensions.

**Data flow**: It starts with the `Onboarding` object’s stored settings, such as config, email, model, credentials, and manifests. It calls `create` to make the durable core records, then passes the resulting workspace and member information to `run_steps`. It returns an `Onboarded` value containing the new workspace ID and member ID.

**Call relations**: This is the top-level method for this file’s flow. It simply sequences the two major phases: `create` for the core system and `run_steps` for extension and agent provisioning work.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 100–106)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the permanent core of a new installation, but only after checking that setup has the secrets it needs. This separation lets the system fail early before writing database records.

**Data flow**: It reads the onboarding object’s chosen model, config, credentials, extension manifests, and admin email. It first checks for the model key, then checks whether extension steps require a credential store, then creates the workspace records. It returns an `Onboarded` object with the IDs of the created workspace and admin member.

**Call relations**: This method is called by `Onboarding.run` before any extension onboarding happens. It delegates the safety checks to `_require_model_key` and `_require_credentials_for_steps`, then hands the actual database creation to `_create_workspace`.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 108–110)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the after-core setup for a newly created workspace. This includes provisioning agents from extensions and then running extension onboarding steps.

**Data flow**: It receives an `Onboarded` result, mainly using its workspace ID. It applies `AgentProvisioning` to that workspace, then calls `run_onboarding_steps` with the installed manifests, workspace ID, and credential store. It returns nothing; its effects are the created or configured extension-related resources.

**Call relations**: This is called by `Onboarding.run` after `create` succeeds. It bridges the core setup to extension setup: first extension agent provisioning is applied, then `run_onboarding_steps` gives each extension a chance to perform its own startup work.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 112–123)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops onboarding early if installed extensions have setup steps but no credential store is available. This prevents the system from creating a workspace and only then discovering that extension setup cannot safely store or read needed secrets.

**Data flow**: It reads the onboarding object’s credential store, extension manifests, and configured credential key environment name. If credentials are present, it does nothing. If credentials are missing and any extension has onboarding steps, it raises a runtime error explaining which environment setting is needed.

**Call relations**: This check is used inside `Onboarding.create`, before database records are written. It does not call other project functions; it acts as a gatekeeper so `_create_workspace` only runs when the setup environment is acceptable.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 125–133)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks whether the selected model needs an environment-provided key and refuses to continue if that key is missing. This keeps a first-run setup from producing a workspace whose main assistant cannot take its first turn.

**Data flow**: It asks `_model_key_env` which environment variable name, if any, is needed for the selected model. If no key is required, it exits quietly. If a key is required, it uses `deploy_env` to look for the value, and raises a runtime error if the value is absent.

**Call relations**: This is called by `Onboarding.create` before database creation. It relies on `_model_key_env` to identify the needed setting and on `deploy_env` to read the deployment environment in the accepted way.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create); 1 external calls (deploy_env).


##### `Onboarding._model_key_env`  (lines 135–138)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that should contain the API key for the chosen model, if the core model registry knows one. If the model provider comes from an extension and resolves its key later, this may return nothing.

**Data flow**: It reads the onboarding config, installed manifests, and selected model name. It builds or consults the model registry, asks it for the key environment name for that model, and returns either that string or `None`.

**Call relations**: This helper is called by `_require_model_key`. It hands that caller the exact environment variable name to check, using `model_registry` as the source of truth for model-provider configuration.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 140–167)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the core first-run records into the database: the workspace, the initial admin member, and the main agent. It also protects against running initialization twice.

**Data flow**: It opens a workspace database transaction, checks whether any member already exists, and raises `AlreadyInitialized` if so. If the database is still fresh, it generates new IDs, inserts a workspace row, creates the admin member, inserts the main agent row with default settings and the selected model, and returns an `Onboarded` object containing the new workspace and member IDs.

**Call relations**: This is called by `Onboarding.create` after all preflight checks pass. It uses `workspace_tx` for the database boundary, SQLAlchemy insert/select helpers for database statements, `create_member` for the member record, `uuid4` for new IDs, and returns the `Onboarded` result that later flows into `Onboarding.run_steps`.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### `core/src/ufo/onboard/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the program refer to the drawer by name.

Because this file is empty, it does not create objects, run setup code, or change program behavior directly. Its value is structural. Without it, depending on the Python version and packaging setup, imports that expect `ufo.onboard` to be a normal package could fail or behave differently. Keeping the file also makes the project layout clearer to readers and tools: anything inside this directory belongs to the onboarding part of the `ufo` codebase.


### Workspace onboarding services
Trusted onboarding helpers create or find workspaces and members, expose sign-in state, and seed realistic demo project data.

### `core/src/ufo/onboard/onboard_control.py`

`domain_logic` · `request handling`

This file is the locked staff entrance for onboarding. The public sign-in gateway does not directly edit the main workspace tables. Instead, it calls these internal routes with a special bearer token, which is a secret string in the HTTP Authorization header. Without this file, the edge service would need to know how to create members, fund new workspaces, choose the default agent prompt, and read across workspaces, making those rules easier to drift or misuse.

The main class, OnboardControl, builds five routes under /internal/onboard. One route seats a verified email address in a workspace. If the workspace is new, it creates the first member as an admin, creates the default agent, and gives the workspace a starting balance. If an intake form supplied company goals, that text is placed behind a safety “wall” so it is treated as outside information, not as an instruction the agent must obey. Another route checks whether an existing member is still an admin. The choices route lists workspaces a verified address may enter. The fleet route counts all workspaces. The invitations route pages through teammate invitations.

Some reads must look across all workspaces, so they use a special owner database path rather than the normal workspace-limited path. That is powerful, so the visible cross-workspace reads are logged as warnings.

#### Function details

##### `MemberModelKey._served`  (lines 120–123)

```
def _served(cls, provider: str) -> str
```

**Purpose**: This validator checks that a requested model provider is one the system knows how to route for individual members. It stops callers from storing a credential under an unknown provider name.

**Data flow**: It receives the provider string from a request body, compares it with the allowed provider names from the member-routed slot map, and either returns the same provider string or raises a validation error before the request can proceed.

**Call relations**: It runs automatically when FastAPI and Pydantic, the request-shape checking library, build a MemberModelKey from incoming JSON. Later, if validation passed, OnboardControl._seat can safely use the model key without guessing what provider it belongs to.

*Call graph*: 1 external calls (values).


##### `MemberModelKey.slot`  (lines 125–126)

```
def slot(self) -> str
```

**Purpose**: This converts a public provider name into the internal credential slot where that provider's key should be stored. It is used when onboarding needs to save a member's own model-provider key.

**Data flow**: It reads the validated provider name on the MemberModelKey object, searches the configured slot-to-provider map, and returns the matching slot name.

**Call relations**: OnboardControl._seat uses this after a member has been created. The returned slot is passed into member_slot so the credential is stored in the same place the normal browser-based connect flow would use.

*Call graph*: 1 external calls (items).


##### `_inert`  (lines 193–207)

```
def _inert(answer: str) -> str
```

**Purpose**: This makes intake-form text safe to place inside an agent prompt by defusing doubled braces like {{name}}. Those braces have special meaning to the prompt renderer, so leaving them untouched could break every turn in that workspace.

**Data flow**: It receives one free-text answer from the public form, repeatedly shrinks any doubled opening or closing braces until none remain, and returns text that still resembles what the person typed but no longer contains live prompt-variable syntax.

**Call relations**: agent_prompt calls this for the business and goals fields before wrapping them in the untrusted-text wall. It is a small safety step inside the larger process of turning public intake data into background context for the default agent.

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 210–229)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

**Purpose**: This builds the starting prompt for the default agent in a newly onboarded workspace. It either returns the standard prompt unchanged or adds intake-form context in a clearly marked, untrusted section.

**Data flow**: It receives an optional SignupProfile. If there is no profile, it returns DEFAULT_AGENT_PROMPT. If there is one, it cleans the business and goals answers with _inert, wraps them with wall so they are treated as quoted outside information, and inserts that section below the default prompt.

**Call relations**: OnboardControl._seat calls this while creating the default agent row. It hands off to _inert for brace safety and to wall for the stronger boundary that tells the agent this text came from a public form, not from a trusted workspace member.

*Call graph*: calls 1 internal fn (_inert); called by 1 (_seat); 1 external calls (wall).


##### `_labelled`  (lines 232–268)

```
def _labelled(rows: Sequence[sa.RowMapping], subject: str) -> list[WorkspaceChoice]
```

**Purpose**: This turns raw database rows into the list of workspace choices a signing-in person can understand. It also refuses an ambiguous case where one verified subject appears to point to more than one workspace.

**Data flow**: It receives rows from the choices query and the verified signup subject. It computes each workspace's human label from its first member, separates direct memberships from subject-based matches, rejects multiple subject matches, adds short UUID prefixes when labels collide, and returns WorkspaceChoice objects.

**Call relations**: OnboardControl._choices calls this after doing the cross-workspace lookup. It relies on workspace_subject to make labels match the rest of onboarding, and it raises an HTTP error rather than letting the caller guess if the subject mapping is unsafe.

*Call graph*: called by 1 (_choices); 3 external calls (__init__, HTTPException, workspace_subject).


##### `_verified_signup`  (lines 271–290)

```
def _verified_signup(email: str, domain: str | None, signup_subject: str | None) -> tuple[str, str, str]
```

**Purpose**: This checks that the email, domain, and signup subject claimed by the gateway all agree with the verified email address. It protects onboarding from seating someone into a workspace for a domain or address they did not prove.

**Data flow**: It receives an email plus optional domain and signup_subject values. It lowercases and trims them, derives the domain from the email, rejects mismatches with clear HTTP 422 errors, chooses a default subject when needed, and returns the normalized member email, verified domain, and signup subject.

**Call relations**: OnboardControl._seat uses this before creating or joining a workspace, and OnboardControl._choices uses it before listing possible workspaces. It is the shared front door for the rollout-compatible email/domain/subject rules.

*Call graph*: called by 2 (_choices, _seat); 2 external calls (HTTPException, email_domain).


##### `OnboardControl.router`  (lines 300–307)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the FastAPI router for the private onboarding API. A router is the object that tells the web server which URL paths exist and which Python methods should answer them.

**Data flow**: It starts with the control token stored on the OnboardControl object, creates a router with the /internal/onboard prefix, attaches the token guard as a dependency, registers the five route methods, and returns the ready-to-mount router.

**Call relations**: Application startup code can call this to add onboarding routes to the service. Every route it registers passes through OnboardControl._guard first, then reaches the matching method such as _seat, _choices, or _invitations.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `OnboardControl._guard`  (lines 309–311)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This is the lock on every internal onboarding route. It rejects any request that does not carry the exact configured bearer token.

**Data flow**: It receives the Authorization header from the HTTP request, compares it with Bearer plus the control token, and either returns quietly or raises a 401 HTTP error.

**Call relations**: The router installs this as a dependency, meaning FastAPI runs it before any route method. If it fails, none of the database-reading or database-writing onboarding code is reached.

*Call graph*: 1 external calls (HTTPException).


##### `OnboardControl._seat`  (lines 313–406)

```
async def _seat(self, request: SeatRequest) -> EnsuredWorkspace
```

**Purpose**: This creates or joins the workspace named by a verified signup subject and seats the signing-in member there. For a new workspace, it also creates the default agent and grants the initial balance needed to start using it.

**Data flow**: It receives a SeatRequest containing workspace_id, email identity information, optional intake profile, and optional model key. It verifies the email claim, opens the workspace context and database transaction, creates the workspace if absent, locks the workspace row, checks that the workspace belongs to the subject, creates or finds the member, creates the default agent, credits a signup grant only for a newly founded workspace, reads whether the member is admin, optionally stores the member's model key after the transaction, and returns workspace id, admin status, and whether this was the founding sign-in.

**Call relations**: FastAPI calls this for POST /internal/onboard/seat after _guard succeeds. It calls _verified_signup for identity safety, agent_prompt for the default agent's prompt, create_member for seat rules, credit and set_reserve for the signup balance, and credential helpers if a model key was supplied.

*Call graph*: calls 2 internal fn (_verified_signup, agent_prompt); 14 external calls (__init__, HTTPException, insert, select, workspace_tx, member_slot, credit, set_reserve, create_member, signup_workspace_id (+4 more)).


##### `OnboardControl._membership`  (lines 408–428)

```
async def _membership(self, workspace_id: UUID, email: str) -> Membership
```

**Purpose**: This checks whether an email address is still a member of a chosen workspace and whether that member is an admin. It deliberately does not recreate a missing member, so a removal during sign-in still takes effect.

**Data flow**: It receives a workspace_id and email, normalizes the email, opens that workspace's database context, looks up the member's admin flag, and returns it in a Membership object. If no matching member exists, it returns a 404 HTTP error.

**Call relations**: FastAPI calls this for GET /internal/onboard/membership after the token guard passes. It is used after a workspace choice has been made, as a final check that the member was not revoked between listing choices and selecting one.

*Call graph*: 5 external calls (__init__, HTTPException, select, workspace_tx, ws).


##### `OnboardControl._choices`  (lines 430–455)

```
async def _choices(self, email: str, domain: str | None=None, signup_subject: str | None=None) -> WorkspaceChoices
```

**Purpose**: This lists the workspaces a verified address may enter: workspaces where the address is already a member, plus the deterministic workspace for its verified signup subject. It gives the sign-in gateway a safe menu instead of letting it inspect all tenant data itself.

**Data flow**: It receives email plus optional domain and signup_subject, verifies and normalizes them, logs a warning because this is a cross-workspace read, opens the owner database path, runs the choices SQL query, converts the rows into friendly labels with _labelled, and returns a WorkspaceChoices response.

**Call relations**: FastAPI calls this for GET /internal/onboard/choices after _guard. It uses _verified_signup to confirm the caller's identity facts, signup_workspace_id to look up the subject's deterministic workspace, owner_tx because the lookup crosses workspace boundaries, and _labelled to make the result safe and readable.

*Call graph*: calls 2 internal fn (_labelled, _verified_signup); 5 external calls (__init__, text, owner_tx, warn, signup_workspace_id).


##### `OnboardControl._fleet`  (lines 457–464)

```
async def _fleet(self) -> Fleet
```

**Purpose**: This counts how many workspaces exist. It is mainly a simple health or deployment check that proves the internal route can reach the database.

**Data flow**: It logs a warning for the cross-workspace read, opens the owner database path, counts rows in the workspace table, and returns that count as Fleet.craft.

**Call relations**: FastAPI calls this for GET /internal/onboard/fleet after _guard. Unlike workspace-scoped routes, it uses owner_tx because counting every workspace is intentionally outside any one workspace's view.

*Call graph*: 4 external calls (__init__, select, owner_tx, warn).


##### `OnboardControl._invitations`  (lines 466–539)

```
async def _invitations(self, after_invited_at: datetime | None=None, after_workspace_id: UUID | None=None, after_email: str | None=None) -> Invitations
```

**Purpose**: This returns one page of pending teammate invitations, oldest first. It lets a repeated background caller send or inspect invitations without loading the entire table at once.

**Data flow**: It receives an optional page cursor made of invited time, workspace id, and email. If only part of the cursor is present, it rejects the request. It builds a query for invited members, joins the inviter and the first workspace member for email and label details, applies the cursor if present, limits the page size, reads through the owner database path, and returns Invitation objects with workspace label, invited address, inviter address, and timestamp.

**Call relations**: FastAPI calls this for GET /internal/onboard/invitations after _guard. It uses owner_tx because invitations are read across the whole fleet, and it uses workspace_subject so invitation emails name workspaces the same way the choices screen does.

*Call graph*: 9 external calls (__init__, __init__, HTTPException, DateTime, literal, select, tuple_, owner_tx, workspace_subject).


### `core/src/ufo/onboard/seed.py`

`domain_logic` · `onboarding or demo-data seeding`

This file is like a showroom setup script for the conversation UI. Instead of waiting for a real user and real agent run to produce every possible display shape, it writes a carefully built finished conversation directly into the system’s durable stores: the database, the blob store, and the extension-owned chat store.

The main class, KitchenSink, creates a new conversation with three completed turns. Those turns include terminal result frames, token and cost information, a still-open user question, tool calls, tool results, and a transcript. It also creates nested subagent conversations, where one subagent spawns another, so the portal can show that hierarchy too. Finally, it uploads two shared files and records them as artifacts connected to one turn.

Before writing the new demo, it looks for previous kitchen-sink runs and deletes them. This cleanup is deliberately cautious. It only removes conversations marked with this seed’s private queue prefix, and it refuses to delete a run if there is evidence that a real user spoke in it or that transcript access was disclosed. That protects real workspace history from being mistaken for throwaway demo data.

Without this file, developers and operators would need to manually create complex conversations to check whether the portal still draws every conversation feature correctly.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: This helper builds a user message that includes a hidden-looking context header pointing back to a specific turn. It is used so the seeded transcript looks like a real conversation where each user request is tied to the turn it belongs to.

**Data flow**: It receives a turn ID and the words the user supposedly said. It wraps the turn ID into a small context block, appends the user text, and returns a Message object marked as coming from the user.

**Call relations**: KitchenSink._said calls this helper while assembling the transcript. It keeps the repeated message-wrapping pattern in one place so the transcript entries are consistent.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: This is the main entry for creating the kitchen-sink demo conversation. A caller uses it when they want one fresh, complete demo conversation written into the workspace.

**Data flow**: It starts with the workspace, agent, member, email, and blob store already stored on the KitchenSink object. It creates new IDs, clears older safe-to-delete demo runs, opens the main conversation, adds subagent runs, uploads sample files, writes the transcript blob, and returns the new conversation ID.

**Call relations**: This method drives the whole flow. It calls _clear first for cleanup, then _open for the main database rows, _runs for nested subagent conversations, _files for shared artifacts, and _said to build the transcript that is encoded and stored in the blob store.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: This removes earlier kitchen-sink demo runs before a new one is written. It exists so the workspace shows one current demo instead of accumulating stale copies.

**Data flow**: It asks _prior for previous runs that are safe to remove. For each one, it deletes database rows through _drop, removes the chat row from the web extension store, and deletes transcript and artifact blobs from blob storage.

**Call relations**: KitchenSink.write calls this at the start of each seed run. It relies on _prior to decide what is safe and on _drop to remove database records before it cleans up extension-store and blob-store leftovers.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: This finds earlier kitchen-sink runs that belong to this seeder and are safe to delete. Its main job is to avoid damaging real user history.

**Data flow**: It reads the database for web conversations in the same workspace whose queue key starts with the kitchen-sink prefix. For each root conversation, it follows linked subagent conversations through their queue keys, gathers their turns, then checks whether any turn looks user-created or whether any transcript access record exists. Only runs with no such signs are returned as _PriorRun records.

**Call relations**: KitchenSink._clear calls this before deleting anything. It hands back only the runs that _clear may pass to _drop, acting as the safety gate for the cleanup process.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: This deletes the database records for one previous seed run. It removes the rows that the seeder itself created, while returning blob keys so the caller can clean up stored files too.

**Data flow**: It receives a _PriorRun containing conversation IDs and turn IDs. Inside a workspace database transaction, it finds shared artifact blob keys, deletes artifact rows, deletes conversation change rows, deletes turn rows, and finally deletes the conversation rows. It returns the artifact blob keys it found.

**Call relations**: KitchenSink._clear calls this after _prior has approved a run for deletion. _drop removes database state, then _clear uses the returned blob keys to delete matching files from blob storage.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: This creates the main web conversation and its three completed turns. It also makes the conversation visible to the web chat surface with the expected title and chat metadata.

**Data flow**: It receives the new conversation ID and three turn IDs. It inserts one conversation row, then inserts one done turn per terminal frame returned by _terminals. After the database rows are written, it retitles the conversation to “Kitchen sink” and writes a chat row containing the agent ID and user email into the web extension store.

**Call relations**: KitchenSink.write calls this after cleanup. It calls _terminals to get the finished-turn summaries, then hands the created conversation to the surface title helper and the extension store so the web portal can find and display it.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: This builds the final summary frames for the three seeded turns. These frames are what make the turns look completed and show model, token, cost, and question information.

**Data flow**: It takes no outside input beyond the KitchenSink object. It returns three TerminalFrame objects: two plain completed frames with model usage and cost, and one completed frame that also includes a pending multi-part question for the user.

**Call relations**: KitchenSink._open calls this while inserting the main turn rows. The returned frames become the terminal data stored on those turns, which the portal later reads to draw completion status, cost lines, and the question UI.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–371)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: This adds nested subagent activity to the demo conversation. It makes the seeded data show not only a main chat, but also child and grandchild agent runs.

**Data flow**: It receives the main conversation ID and the parent turn ID that should appear to have spawned a subagent. It creates two subagent runs through _run, then writes a transcript blob for the first spawned conversation containing a user message, an assistant tool call, and a tool result.

**Call relations**: KitchenSink.write calls this after opening the main conversation. It delegates database creation to _run, then writes a transcript so the spawned subagent conversation has visible message content.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 373–408)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: This creates one subagent conversation with one completed turn. It is the reusable piece used to build the nested subagent chain in the demo.

**Data flow**: It receives a turn ID, the parent turn ID, the subagent profile name, and the answer text to store. It creates a new conversation ID, inserts a subagent conversation whose queue key points back to the parent, inserts a completed turn with the profile and result JSON, and returns the new conversation ID.

**Call relations**: KitchenSink._runs calls this twice: once for a child subagent and once for a grandchild subagent. The returned conversation ID lets _runs attach a transcript to the spawned conversation.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 410–431)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: This uploads sample shared files and records them as artifacts for one turn. It lets the demo conversation show attached documents in the portal.

**Data flow**: It receives the turn ID that should own the files. For each built-in sample document, it encodes the text, creates a unique blob key, writes the bytes to blob storage, and inserts a shared_artifact database row with filename, media type, size, and workspace information.

**Call relations**: KitchenSink.write calls this after creating the conversation and subagent runs. The files it records are later displayed as artifacts attached to the chosen turn.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 433–499)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: This builds the main transcript messages for the seeded conversation. It gives the demo realistic chat content, including tool calls, tool results, Markdown, a table, and a code block.

**Data flow**: It receives the three turn IDs. It creates a sequence of Message objects: framed user requests tied to those turns, assistant tool-use messages, user tool-result messages, and assistant replies. The result is a tuple of messages ready to be encoded and stored as the transcript.

**Call relations**: KitchenSink.write calls this when writing the main transcript blob. It uses _framed for the user requests and constructs the other message shapes directly so the portal has many rendering cases to exercise.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).


### Shipped agent provisioning
Runtime agent setup definitions and provisioning logic install extension-declared agents without overwriting member-managed agents.

### `core/src/ufo/runtime/kinds/agent_setup.py`

`domain_logic` · `request handling`

An agent can be installed before it has everything it needs to run well. For example, it may need the member to connect a Google account, an admin to add an API key, or the member to turn on a daily schedule. This file is the shared language for describing those needs and checking which ones are already satisfied.

Most of the file is made of Pydantic models, which are structured data objects that also validate their contents. AgentSetup is the agent author's declaration: “this app can use these connectors, these credentials, and these standing orders.” SetupState is the answer shown later to a user: “this connector is granted, this credential is filled, this schedule is armed.” The schedule-related models are careful about local time. If a member chooses 9 AM, that is their 9 AM, not a server time; conversion to UTC happens elsewhere where the member’s time zone is known.

The key live function is setup_state. It reads the agent’s declared setup from the workspace database, checks which account grants the current member may actually use, checks whether needed credentials exist either in the workspace or deployment environment, and asks an outside callback whether standing orders exist. The result is a complete setup report, including both finished and unfinished rows, so the screen can explain what the agent runs on rather than only what is missing.

#### Function details

##### `SetupCadence._hourly_spans_every_day`  (lines 45–53)

```
def _hourly_spans_every_day(self) -> 'SetupCadence'
```

**Purpose**: This validator keeps schedule choices meaningful. It prevents an “every hour” cadence from also naming weekdays, because without a specific local hour there is no safe way to translate a local weekday into server time.

**Data flow**: It reads the cadence object after its fields have been filled. If the cadence has no hour but does list weekdays, it rejects it with a clear error. It also checks that every weekday number is between 0 and 6, where 0 means Sunday. If everything is consistent, it returns the same cadence unchanged.

**Call relations**: Pydantic calls this automatically whenever a SetupCadence is created or validated. Other setup objects can then trust that any cadence they contain follows the scheduling rules before it is shown to users or converted elsewhere.


##### `AgentSetup._a_clock_need_carries_its_offer`  (lines 135–152)

```
def _a_clock_need_carries_its_offer(self) -> 'AgentSetup'
```

**Purpose**: This validator makes sure schedule setup is not half-declared. If an agent says it needs a scheduled task, it must also say what schedule it offers; if it offers schedule choices, it must also declare that it needs a scheduled task.

**Data flow**: It reads the completed AgentSetup declaration. If the standing-order list includes the scheduled-task kind but no schedule offer is present, it raises an error. If a schedule offer is present but the scheduled-task need is missing, it also raises an error. Otherwise it returns the declaration unchanged.

**Call relations**: Pydantic runs this when an AgentSetup is validated, including inside setup_state after the declaration is read from the database. This protects the setup screen from showing a need the user cannot act on, or an offer that is not attached to any visible need.


##### `_armed`  (lines 236–248)

```
async def _armed(kind: str, wanted: AgentSetup, armed: Armed) -> ArmedOrder
```

**Purpose**: This helper asks whether the agent already has the standing order it needs, such as a scheduled task. For scheduled tasks, it asks about the specific task name from the agent’s schedule offer so that one feature’s schedule is not mistaken for another’s.

**Data flow**: It receives a standing-order kind, the agent’s declared setup, and an async callback that knows how to look up standing orders. If the kind is the scheduled-task kind and the agent declared a schedule, it passes the schedule’s name to the callback. Otherwise it asks only whether any order of that kind exists. It returns an ArmedOrder saying whether the order is held and, when relevant, what cron schedule it uses.

**Call relations**: setup_state calls this once for each declared standing-order kind while building the user-facing setup report. The lookup itself is deliberately handed off to the supplied armed callback, because different standing-order kinds can live in extension-specific storage outside this core file.

*Call graph*: called by 1 (setup_state).


##### `setup_state`  (lines 251–347)

```
async def setup_state(agent_id: UUID, member_id: UUID, *, armed: Armed) -> SetupState
```

**Purpose**: This builds the current setup status for one agent as seen by one member. It answers practical questions like “is the needed account connected for this member?”, “is the required credential available?”, and “has this scheduled task been armed?”

**Data flow**: It takes an agent ID, a member ID, and a callback for checking standing orders. It opens a workspace database transaction, reads the agent’s stored setup declaration, and returns an empty setup state if there is no declaration. If there is a declaration, it validates it, reads connector grants that are either shared with the workspace or owned by the member, and reads stored credentials for the declared credential slots. After leaving the database transaction, it also treats deployment-provided environment credentials as filled. It then creates connector, credential, and standing-order status rows and returns one SetupState containing the full setup picture plus the agent’s instructions and schedule offer.

**Call relations**: This is the main public flow in the file. A setup surface or API endpoint can call it when it needs to draw the setup screen for an agent. During that flow it uses _armed to delegate standing-order checks, because core knows the declaration but not every extension’s storage. It also constructs the small state models that the caller can return to the user interface.

*Call graph*: calls 1 internal fn (_armed); 9 external calls (__init__, __init__, __init__, __init__, or_, select, workspace_tx, deploy_env, ws_current).


### `core/src/ufo/runtime/kinds/provisioning.py`

`domain_logic` · `workspace activation / first turn provisioning`

Extensions can come with ready-made agents, like a new appliance arriving with suggested settings. This file is the installer for those agents inside one workspace. Its main job is to copy each extension-declared agent into the workspace database, while respecting that the workspace belongs to the member after that point.

The key class, AgentProvisioning, receives active extension manifests. A manifest is the extension’s description of what it ships. For each declared agent, it checks the workspace. If the agent was already provisioned by this same extension under the same declared name, the file mostly leaves it alone. It only refreshes the extension-owned setup information and fills in a missing purpose sentence. This avoids wiping out a member’s own edits.

If the provision is marked as the main agent, it can adopt the workspace’s existing main agent instead of replacing it. If a matching ordinary agent already exists and looks exactly like what the extension would create, it is adopted too. Otherwise, a new agent row is inserted.

Name conflicts are handled politely. If the requested name is already taken, the code tries a name with the extension added, then numbered variants. This means two extensions, or an extension and a member, can ask for the same name without one silently overwriting the other.

#### Function details

##### `AgentProvisioning.apply`  (lines 54–62)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies all agent provisions from all active extension manifests to one workspace. Someone uses this when a workspace needs to make sure every installed extension’s shipped agents exist there.

**Data flow**: It receives a workspace ID and reads the manifests stored on the AgentProvisioning object. It enters the workspace context, walks through every manifest and every agent declared by that manifest, and asks _one to apply each provision. It returns a tuple of ProvisionOutcome records saying, for each provision, whether the agent was created, adopted, or already present.

**Call relations**: This is the public starting point for the file’s work. It sets the workspace context with ws, then repeatedly hands individual provisions to _one, which performs the database checks and writes.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 64–147)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Applies one extension-declared agent to one workspace. It decides whether to reuse an existing row, adopt a matching member-owned row, update extension-owned fields, or create a new agent.

**Data flow**: It takes a workspace ID, one manifest, and one agent provision. Inside a workspace database transaction, it first looks for an agent already tied to that extension and declared provision name. If found, it may refresh the fields the extension still owns and returns a “present” result. If not found, it looks for an existing main agent or an identical unprovisioned agent that can be adopted. If adoption is not safe, it finds a free name, creates a new row, and returns a “created” result.

**Call relations**: apply calls this once per declared agent. During its decision tree, it calls _fill to refresh already-provisioned rows, _identical to decide whether an existing agent is safe to adopt, _free_name to avoid name collisions, and _create to insert a new row when needed. It wraps the whole operation in workspace_tx so the read-and-write steps happen as one database transaction.

*Call graph*: calls 4 internal fn (_create, _fill, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._fill`  (lines 149–189)

```
async def _fill(self, connection: AsyncConnection, shipped: sa.Row, manifest: Manifest, provision: AgentProvision) -> None
```

**Purpose**: Refreshes the small part of an already-shipped agent that still belongs to the extension. It updates setup every time it changes, and only fills purpose if the workspace row has no purpose yet.

**Data flow**: It receives a database connection, the existing agent row, the manifest, and the provision. It compares the stored setup with the setup currently declared by the extension. If setup changed, it prepares an update. If the stored purpose is missing, it adds the declared purpose. If there is nothing to change, it does nothing. Otherwise, it writes the changed fields, updates the recorded provision version, and touches the updated timestamp.

**Call relations**: _one calls this after it finds an agent that was already provisioned by the same extension declaration. It does not create agents or choose names; it only makes sure the already-linked row carries the current extension-owned setup and any missing purpose.

*Call graph*: called by 1 (_one); 2 external calls (execute, update).


##### `AgentProvisioning._free_name`  (lines 191–216)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Chooses a safe agent name when the extension’s requested name may already be taken. It protects existing member agents and agents from other extensions from being overwritten.

**Data flow**: It receives a database connection, workspace ID, extension name, and declared agent name. It reads all active agent names in that workspace. It first tries the declared name. If that is taken, it tries the declared name plus the extension name, with underscores changed to hyphens. If needed, it tries numbered versions up to a fixed limit. It returns the first unused name or raises an error if none can be found.

**Call relations**: _one calls this only after deciding that no existing agent should be adopted. The chosen name is then passed to _create so the new database row is inserted without clashing with existing live agents.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 218–275)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Creates the actual workspace agent row for an extension-declared agent. The new row starts with the extension’s declared settings, but it does not inherit special permissions or hidden authority.

**Data flow**: It receives a database connection, workspace ID, manifest, provision, and final agent name. It gathers the provision’s spec fields such as prompt, model, visibility, tools, and setup. If the provision did not supply an icon, it reads existing workspace icons and asks auto_agent_icon to choose one. It then inserts a new agent row with a new UUID, timestamps, provision identity, and version. If another concurrent run inserted the same provision first, the insert quietly does nothing instead of failing.

**Call relations**: _one calls this after it has ruled out an existing provision, ruled out adoption, and obtained a free name from _free_name. It is the final write step in the “create a new shipped agent” path.

*Call graph*: called by 1 (_one); 4 external calls (execute, select, auto_agent_icon, uuid4).


##### `AgentProvisioning._identical`  (lines 277–290)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing unprovisioned agent already has the same core settings as the agent an extension would create. This lets the system adopt that row instead of making a duplicate.

**Data flow**: It receives a database row and an agent provision. It compares the row’s stored prompt, model, reasoning setting, internet access flag, sandbox size, visibility, and tools against the provision’s desired values. It returns true only if all those compared settings match.

**Call relations**: _one calls this when it finds an ordinary, unarchived workspace agent with the declared name. If _identical says the row matches, _one marks that row as provisioned by the extension; if not, _one leaves it alone and creates a separate provisioned agent under a free name.

*Call graph*: called by 1 (_one).

## 📊 State Registers Touched

- `reg-pack-composition` — The selected bundle of built-in extensions, prompts, skills, jobs, and setup steps for this deployment.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-auth-sessions` — The sign-in state and signed tokens that prove who a web, surface, or API request belongs to.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-object-system` — The common address book and audit trail for durable workspace objects such as agents, members, artifacts, and connectors.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-agent-provisioning` — The saved provenance, setup needs, policies, and ownership for agents that are shipped by extensions or created in workspaces.
- `reg-credential-fulfillment` — Durable one-time markers that a requested credential/setup slot has been fulfilled for a workspace.
