# Workspace onboarding and agent provisioning  `stage-5`

This stage is the project’s front door. It runs during first setup, and also supports later workspace setup screens. Its job is to create a usable workspace, add the first people, choose how they can sign in, and make sure helpful agents are available.

The main coordinator is onboarding.py. It creates the first workspace, the first admin member, and the main assistant agent. It also checks required keys and lets extensions do setup work safely. onboard_control.py is the trusted private API used by the Rust control plane. It creates workspaces, seats members, lists sign-in choices, counts workspaces, and reads invitation pages, so these rules are not scattered around the login layer. provisioning.py installs agents that come from extensions, but only once, and it avoids overwriting later user edits. agent_setup.py describes what those agents still need, such as connected accounts or credentials, and reports setup status to the portal. seed.py adds a rich demo conversation for testing and cleans up old copies. __init__.py simply makes the onboarding folder importable.

## Files in this stage

### Onboarding entry points
Core first-run onboarding initializes the package and orchestrates creation of the initial workspace, admin, and assistant while invoking extension setup safely.

### `core/src/ufo/onboard/onboarding.py`

`orchestration` · `startup / first-run initialization`

This file is the “move-in checklist” for a brand-new UFO installation. On the first run, the system needs a durable home: a workspace, an initial admin member, and a main agent the user can talk to. Without this file, a fresh install would not know how to create those basic records safely, and extensions would not get a reliable chance to prepare themselves.

The flow is deliberately careful. Before touching the database, it checks that the selected model has the needed environment variable for its API key, if UFO knows how to check that provider. It also checks whether extension onboarding steps need a credential store. This avoids leaving behind a half-created workspace after a preventable setup error.

The core creation happens once, inside a database transaction, which means the related database changes succeed or fail together. If the database already contains a member, setup stops with `AlreadyInitialized` instead of creating duplicates.

After the core workspace exists, extension setup runs. Each extension gets its own scoped context, like giving each add-on its own labeled toolbox. If one extension step fails, the error is logged and recorded, but the workspace remains usable and other extensions can still run.

#### Function details

##### `run_onboarding_steps`  (lines 57–97)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the setup steps supplied by installed extensions for a newly created workspace. It keeps extension failures from breaking the whole first-run setup, while still recording whether each step succeeded or failed.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters the workspace context, looks through each extension, builds a scoped context for that extension’s declared credential slots, then runs each onboarding step. For each step, it records a completed or failed result; if credentials are missing, it skips extensions that have steps and writes a log message.

**Call relations**: After the core workspace exists, `Onboarding.run_steps` calls this function to give extensions their turn. During that work it asks `context_for` for the extension-specific context, uses `ws` so the work is tied to the correct workspace, logs problems through the observability logger, and sends step results to `record_onboarding_step` so the outcome is visible as onboarding data rather than only as log text.

*Call graph*: called by 1 (run_steps); 4 external calls (log, record_onboarding_step, context_for, ws).


##### `Onboarding.run`  (lines 113–116)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full first-run process from start to finish. It creates the core workspace first, then runs provisioning and extension onboarding around that newly created workspace.

**Data flow**: It starts with the `Onboarding` object’s stored configuration, email, model choice, credentials, manifests, and reasoning setting. It calls `create` to produce an `Onboarded` result containing the workspace ID and member ID, then passes that result to `run_steps`. It returns the same `Onboarded` result to the caller.

**Call relations**: This is the high-level entry method for this file’s workflow. It coordinates `create` and `run_steps` in order, making sure extensions only run after the core workspace and admin user are safely in place.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 118–124)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates the durable core of a fresh installation: the workspace, initial admin member, and main agent. It performs important checks first so setup fails early before writing database records.

**Data flow**: It uses the stored model name, configuration, credential store, extension manifests, admin email, and reasoning setting. First it checks whether the model key is available, then checks whether extension steps need credentials. If those checks pass, it creates the workspace data and returns an `Onboarded` object with the new workspace and member IDs.

**Call relations**: `Onboarding.run` calls this before any extension setup. Inside, it delegates the early safety checks to `_require_model_key` and `_require_credentials_for_steps`, then delegates the actual database creation to `_create_workspace`.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 126–128)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the after-creation setup for a workspace that has already been created. This includes applying agent provisioning from extensions and then running extension onboarding steps.

**Data flow**: It receives an `Onboarded` object, mainly using its workspace ID. It applies agent provisioning based on the installed manifests, then passes the manifests, workspace ID, and credential store into `run_onboarding_steps`. It does not return a value; its result is the side effects of provisioning and recorded onboarding step outcomes.

**Call relations**: `Onboarding.run` calls this immediately after `create`. It first hands extension manifests to `AgentProvisioning` so extension-provided agent setup can be applied, then hands control to `run_onboarding_steps` for each extension’s own first-run tasks.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 130–141)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops first-run setup early if installed extensions have onboarding steps but there is no credential store available. This prevents a partially created workspace when extension setup would need a secret-storage key later.

**Data flow**: It reads the `credentials` field and the installed extension manifests from the `Onboarding` object. If a credential store exists, it allows setup to continue. If there is no credential store and at least one extension has onboarding steps, it raises an error explaining which environment setting is needed.

**Call relations**: `Onboarding.create` calls this before database creation. It acts as a gatekeeper so `_create_workspace` is only reached when extension onboarding will have the credential support it may need.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 143–151)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks that the selected model has its required API key available before the workspace is created. This helps ensure the first conversation can actually run after initialization.

**Data flow**: It asks `_model_key_env` which environment variable name is required for the selected model. If no eager check is possible or needed, it allows setup to continue. If a variable name is returned, it reads the deployment environment through `deploy_env`; when the value is missing, it raises an error telling the user what to set.

**Call relations**: `Onboarding.create` calls this as the first model-safety check. It relies on `_model_key_env` to identify the needed key name, then uses the credential environment reader to see whether that key is present.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create); 1 external calls (deploy_env).


##### `Onboarding._model_key_env`  (lines 153–156)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that should hold the API key for the selected model, when UFO can know that upfront. Some extension-provided model providers resolve their own keys later, so this can also return nothing.

**Data flow**: It reads the current configuration, installed manifests, and selected model from the `Onboarding` object. It builds or consults the model registry, asks it for the key environment name, and returns either that name or `None`.

**Call relations**: `Onboarding._require_model_key` calls this to learn what key to check. This function hands the provider lookup to `model_registry`, which knows about both built-in and extension-contributed model providers.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 158–185)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the first workspace, first admin member, and main agent into the database. It also protects against running initialization twice by refusing to proceed if any member already exists.

**Data flow**: It opens a workspace database transaction, then checks the member table for an existing member. If one is found, it raises `AlreadyInitialized`. Otherwise it generates new IDs, inserts a workspace row, creates the admin member using the supplied email, inserts the main agent with the chosen model and default prompt details, and returns an `Onboarded` object containing the new workspace and member IDs.

**Call relations**: `Onboarding.create` calls this only after the model and credential checks pass. It uses the database transaction helper so the core records are created as one unit, calls `create_member` for the admin user, and returns the identity information that `Onboarding.run` later passes into `run_steps`.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### `core/src/ufo/onboard/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the system know the drawer exists and can be opened by name.

Because this file is empty, it does not run setup code, expose shortcut imports, or define shared constants. Its value is structural: without it, depending on the Python version and packaging setup, code that tries to import from `ufo.onboard` might fail or behave differently. Keeping it present makes the package boundary explicit and helps tools, tests, and readers understand that `onboard` is meant to be part of the project’s Python module tree.


### Workspace control and demos
Trusted onboarding control APIs manage workspace membership and sign-in state, then optional seed data populates a demonstration conversation.

### `core/src/ufo/onboard/onboard_control.py`

`domain_logic` · `request handling`

This file is the “back office desk” for hosted onboarding. The public sign-in gateway does not directly change core workspace tables. Instead, it calls these internal routes with a special bearer token, and this file decides what is allowed.

Its most important job is seating a verified email address into the right workspace. If the workspace is new, it creates the workspace, makes the first member an admin, creates the default main agent, and gives the workspace an initial billing credit and reserve. If the workspace already exists, it checks that the verified signup identity still belongs there before adding or confirming the member.

The file also supports sign-in decisions. It can list which workspaces an email may enter, check whether a chosen member is still an admin, count the total fleet of workspaces, and return pages of invited teammates for an email-sending sweep.

A key safety detail is how intake-form text is treated. The form is public and untrusted, so its answers are wrapped behind a “wall” before being added to an agent prompt. The code also neutralizes prompt-variable braces, so a malicious form answer cannot break future prompt rendering.

#### Function details

##### `MemberModelKey._served`  (lines 120–123)

```
def _served(cls, provider: str) -> str
```

**Purpose**: Checks that a supplied model-provider name is one this system knows how to route for a member. This prevents storing a credential under an unsupported or misspelled provider.

**Data flow**: It receives a provider string from the request model. It compares that string with the allowed provider names listed in MEMBER_ROUTED_SLOTS. If the name is allowed, it returns it unchanged; if not, it raises a validation error before the request can continue.

**Call relations**: This runs automatically as part of validating a MemberModelKey. It relies on MEMBER_ROUTED_SLOTS as the project’s map of credential slots to provider names, so later code can safely ask the key which slot it belongs in.

*Call graph*: 1 external calls (values).


##### `MemberModelKey.slot`  (lines 125–126)

```
def slot(self) -> str
```

**Purpose**: Finds the internal credential slot that matches this member’s chosen model provider. Someone uses it when they need to store the member’s API key in the same place the normal connect flow would use.

**Data flow**: It reads the provider already validated on the MemberModelKey. It scans the MEMBER_ROUTED_SLOTS mapping until it finds the slot whose served provider matches. It returns that slot name as a string.

**Call relations**: OnboardControl._seat calls this indirectly when a request includes a model key. After the member has been seated, _seat uses the returned slot with member_slot so the credential is written to the correct member-specific place.

*Call graph*: 1 external calls (items).


##### `_inert`  (lines 193–207)

```
def _inert(answer: str) -> str
```

**Purpose**: Makes public form text safe to place inside a prompt template by weakening doubled braces such as {{ and }}. This matters because those braces are also used as prompt-variable syntax, and an outside user should not be able to break every future agent turn.

**Data flow**: It takes one form answer as text. It repeatedly replaces doubled opening and closing braces with single braces until no doubled braces remain. It returns the cleaned text, still readable but no longer containing live prompt-variable markers.

**Call relations**: agent_prompt calls _inert on both intake-form fields before those fields are wrapped as untrusted text. It is a small safety step inside the larger process of building a new workspace’s default agent prompt.

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 210–229)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

**Purpose**: Builds the main agent’s starting prompt for a newly created workspace. If there was no intake form, it uses the normal default prompt; if there was one, it adds the form answers as clearly marked, untrusted background information.

**Data flow**: It receives either a SignupProfile or nothing. With no profile, it returns DEFAULT_AGENT_PROMPT unchanged. With a profile, it cleans each answer with _inert, formats the business and goals text, wraps that text with wall so it is treated as outside information, and returns the combined prompt.

**Call relations**: OnboardControl._seat calls this when creating the default main agent for a workspace. agent_prompt hands off to _inert for brace cleanup and to wall for the project’s standard untrusted-text boundary.

*Call graph*: calls 1 internal fn (_inert); called by 1 (_seat); 1 external calls (wall).


##### `_labelled`  (lines 232–268)

```
def _labelled(rows: Sequence[sa.RowMapping], subject: str) -> list[WorkspaceChoice]
```

**Purpose**: Turns raw database rows about possible workspaces into a human-friendly list of choices for sign-in. It also refuses an ambiguous case where one verified signup subject points to more than one workspace.

**Data flow**: It receives rows from the workspace-choice query and the verified signup subject. It computes each workspace’s label from its first member, keeps exact memberships, adds the subject-matched workspace when valid, and adds a short UUID prefix when two labels would look the same. It returns a list of WorkspaceChoice objects, or raises an HTTP error if the subject match is ambiguous.

**Call relations**: OnboardControl._choices calls _labelled after doing the cross-workspace database read. _labelled uses workspace_subject to compute the same names users see elsewhere, and creates WorkspaceChoice records for the API response.

*Call graph*: called by 1 (_choices); 3 external calls (__init__, HTTPException, workspace_subject).


##### `_verified_signup`  (lines 271–290)

```
def _verified_signup(email: str, domain: str | None, signup_subject: str | None) -> tuple[str, str, str]
```

**Purpose**: Normalizes and checks the identity claims that come with a signup request. It makes sure the stated domain or signup subject really matches the verified email address, so a caller cannot claim someone else’s company or workspace subject.

**Data flow**: It receives an email address, an optional domain, and an optional signup subject. It lowercases and trims them, derives the email’s real domain, checks the optional fields against the email and domain rules, and chooses the final subject when one was not supplied. It returns the normalized member email, verified domain, and signup subject, or raises an HTTP error for invalid claims.

**Call relations**: OnboardControl._seat uses this before creating or joining a workspace, and OnboardControl._choices uses it before listing possible workspaces. It depends on email_domain to derive the domain from the verified email address.

*Call graph*: called by 2 (_choices, _seat); 2 external calls (HTTPException, email_domain).


##### `OnboardControl.router`  (lines 300–307)

```
def router(self) -> APIRouter
```

**Purpose**: Builds the FastAPI router for the internal onboarding endpoints. This is how the rest of the web service gets a ready-made group of routes under /internal/onboard.

**Data flow**: It reads the OnboardControl instance’s guard method and route methods. It creates an APIRouter with a common path prefix and a dependency that checks authorization, then attaches the five routes. It returns the configured router.

**Call relations**: The application setup code is expected to call router when mounting this internal API. The router connects incoming HTTP requests to _seat, _membership, _choices, _fleet, and _invitations, with _guard running first through FastAPI’s dependency system.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `OnboardControl._guard`  (lines 309–311)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: Protects every internal onboarding route with the expected bearer token. Without this check, anyone who could reach the route could create or inspect onboarding state.

**Data flow**: It receives the Authorization header from the HTTP request. It compares it with Bearer plus the configured control token. If it matches, nothing is returned and the request may continue; if not, it raises a 401 HTTP error.

**Call relations**: router installs _guard as a dependency for the whole /internal/onboard router. FastAPI calls it before the route method itself, so _seat, _membership, _choices, _fleet, and _invitations only run after the token check passes.

*Call graph*: 1 external calls (HTTPException).


##### `OnboardControl._seat`  (lines 313–406)

```
async def _seat(self, request: SeatRequest) -> EnsuredWorkspace
```

**Purpose**: Creates or confirms the workspace for a verified signup identity and seats the member in it. For a brand-new workspace, it also creates the default agent and grants the initial signup balance.

**Data flow**: It receives a SeatRequest containing a workspace ID, email identity fields, optional intake profile, and optional model key. It verifies the signup identity, opens the workspace-scoped database context, creates the workspace if needed, locks it, checks that the signup subject matches the workspace, creates the member, inserts the default main agent prompt, optionally credits the workspace, and reads whether the member is an admin. After the database work commits, it optionally stores the member’s model credential. It returns an EnsuredWorkspace response with the workspace ID, admin flag, and whether this was the founding member.

**Call relations**: This is the POST /seat route registered by router. It calls _verified_signup for identity safety, agent_prompt for the default agent’s prompt, workspace_tx for workspace-scoped database writes, create_member for seat creation, credit and set_reserve for the signup grant, and member_slot plus the workspace credential store when a model key is supplied.

*Call graph*: calls 2 internal fn (_verified_signup, agent_prompt); 14 external calls (__init__, HTTPException, insert, select, workspace_tx, member_slot, credit, set_reserve, create_member, signup_workspace_id (+4 more)).


##### `OnboardControl._membership`  (lines 408–428)

```
async def _membership(self, workspace_id: UUID, email: str) -> Membership
```

**Purpose**: Checks whether a given email is still a member of a chosen workspace, and whether that member is an admin. It protects against a race where someone is removed after seeing a workspace choice but before completing sign-in.

**Data flow**: It receives a workspace ID and email address. It normalizes the email, enters that workspace context, and queries the member table for the admin flag on that exact workspace and email. If no row is found, it raises a 404 error; otherwise it returns a Membership response with the admin value.

**Call relations**: This is the GET /membership route registered by router. It uses ws and workspace_tx to read under the normal workspace-scoped database rules, and returns the Membership model used by the caller to finish sign-in decisions.

*Call graph*: 5 external calls (__init__, HTTPException, select, workspace_tx, ws).


##### `OnboardControl._choices`  (lines 430–455)

```
async def _choices(self, email: str, domain: str | None=None, signup_subject: str | None=None) -> WorkspaceChoices
```

**Purpose**: Lists the workspaces a verified email address may enter. It includes workspaces where the address is already a member and, when valid, the deterministic workspace tied to the verified signup subject.

**Data flow**: It receives an email plus optional domain and signup subject. It validates and normalizes them with _verified_signup, logs a warning that this is a cross-workspace read, runs a database query through the owner-level transaction, and feeds the rows into _labelled. It returns a WorkspaceChoices response containing readable choices.

**Call relations**: This is the GET /choices route registered by router. Unlike workspace-local routes, it uses owner_tx because it must look across workspaces before the user has chosen one. It hands raw query results to _labelled so the response is safe and understandable for the sign-in flow.

*Call graph*: calls 2 internal fn (_labelled, _verified_signup); 5 external calls (__init__, text, owner_tx, warn, signup_workspace_id).


##### `OnboardControl._fleet`  (lines 457–464)

```
async def _fleet(self) -> Fleet
```

**Purpose**: Returns the total number of workspaces. This is used as a simple cross-workspace health or deploy-gate check that proves the service can reach the database.

**Data flow**: It takes no request-specific business input. It logs that a cross-workspace read is happening, opens an owner-level transaction, counts rows in the workspace table, and returns that count in a Fleet response.

**Call relations**: This is the GET /fleet route registered by router. It uses owner_tx because counting all workspaces is intentionally outside any one workspace’s normal boundary, and warn records that this broader read happened.

*Call graph*: 4 external calls (__init__, select, owner_tx, warn).


##### `OnboardControl._invitations`  (lines 466–539)

```
async def _invitations(self, after_invited_at: datetime | None=None, after_workspace_id: UUID | None=None, after_email: str | None=None) -> Invitations
```

**Purpose**: Returns one page of pending teammate invitations, ordered from oldest to newest. A background caller can walk through the pages and send invitation emails without loading the whole table at once.

**Data flow**: It receives an optional cursor made of invited time, workspace ID, and email. If only part of the cursor is present, it raises a 422 error. It builds a query for invited members, joins to the inviting member and first workspace member, applies the cursor when present, limits the page size, and reads through an owner-level transaction. It returns Invitations containing invitation records with workspace labels, inviter emails, invitee emails, and timestamps.

**Call relations**: This is the GET /invitations route registered by router. It uses owner_tx because invitations are enumerated across all workspaces. It creates Invitation objects for each row and wraps them in Invitations for the polling sweep that repeatedly asks for the next page.

*Call graph*: 9 external calls (__init__, __init__, HTTPException, DateTime, literal, select, tuple_, owner_tx, workspace_subject).


### `core/src/ufo/onboard/seed.py`

`domain_logic` · `onboarding/demo seed run`

This file is a controlled seed script for demo and onboarding data. Its job is to write a complete finished conversation directly into the same durable places the real engine uses: database tables for conversations and turns, and blob storage for transcripts and shared files. That matters because the public extension layer is not allowed to invent finished history; otherwise an extension could fake audit records for work it never did.

The main class, `KitchenSink`, builds one conversation that acts like a showroom. It opens a web chat conversation, adds three completed turns, attaches terminal summaries with model cost and token details, creates nested subagent conversations, stores transcript messages, and uploads two shared artifacts. One turn ends with a still-open user question, so the portal can show that state too.

Before writing the new demo, it looks for earlier seed runs and deletes only the rows it can prove were written by this seed. It uses private marks such as a seed queue prefix and idempotency keys. If a human has spoken in the conversation, or if transcript access has been recorded, the file treats that as real workspace history and leaves it alone. In plain terms: it cleans up its own display stand, but it will not throw away anything a person may have used.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: Builds a user message that includes a hidden reference to the turn it belongs to. This lets the transcript text point back to the database turn that produced or received it.

**Data flow**: It receives a turn ID and the words the user supposedly said. It wraps the turn ID in a small context block, appends the visible user text, and returns a `Message` object marked as coming from the user.

**Call relations**: The transcript builder `KitchenSink._said` calls this whenever it needs a user message tied to one of the seeded turns. `_framed` hands back a ready-to-store message object for the final transcript.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: Creates one complete kitchen-sink conversation and returns its new conversation ID. This is the top-level action someone uses when they want the demo data written into a workspace.

**Data flow**: It starts by making fresh IDs for the conversation and its three turns. It clears older safe-to-delete seed runs, opens the new conversation rows, creates subagent runs, uploads shared files, writes the transcript blob, and finally returns the conversation ID.

**Call relations**: This is the main driver for the class. It calls the cleanup, database-writing, subagent, file, and transcript-building helpers in order, so the rest of the file works like stations on an assembly line.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: Removes older kitchen-sink seed runs before a new one is created. It keeps the demo from piling up duplicate conversations while protecting anything that may have become real history.

**Data flow**: It asks `_prior` for old seed runs that are safe to remove. For each one, it deletes database rows through `_drop`, removes the web chat row from the extension store, and deletes related transcript and artifact blobs from blob storage.

**Call relations**: `KitchenSink.write` calls this first. `_clear` depends on `_prior` to decide what is safe and `_drop` to remove database records, then it finishes the cleanup in the extension store and blob store.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: Finds earlier kitchen-sink runs that this seed is allowed to delete. It is deliberately cautious so it does not erase a conversation a person interacted with or an admin disclosed.

**Data flow**: It reads the workspace database for web conversations whose queue key has the seed prefix. For each root conversation, it follows child subagent conversations through their parent turn links, gathers their turns, then checks whether any turn lacks the seed mark or whether transcript access exists. Only fully seed-owned, undisclosed runs come out as `_PriorRun` records.

**Call relations**: `KitchenSink._clear` calls this before deleting anything. It uses database queries inside a workspace transaction and returns a compact list of old runs for `_clear` and `_drop` to act on.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: Deletes the database records for one old seed run and reports which blob artifacts also need deletion. It separates database cleanup from blob cleanup because the blob keys must be known before the rows disappear.

**Data flow**: It receives a `_PriorRun` containing conversation IDs and turn IDs. It looks up shared artifact blob keys, deletes shared artifact rows, conversation change rows, turn rows, and conversation rows, then returns the artifact blob keys to the caller.

**Call relations**: `KitchenSink._clear` calls this for each safe old run found by `_prior`. `_drop` clears the database side, then `_clear` uses the returned blob keys to remove the stored files.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: Creates the main web conversation and its three finished turns. This is where the seed establishes the conversation that the portal will show as the kitchen-sink demo.

**Data flow**: It receives a conversation ID and three turn IDs. It inserts the conversation row, inserts one completed turn for each terminal frame returned by `_terminals`, retitles the conversation to “Kitchen sink,” and writes the chat metadata row used by the web surface.

**Call relations**: `KitchenSink.write` calls this after cleanup. `_open` gets its terminal summaries from `_terminals`, writes the core database rows, then hands the conversation to the surface retitling and web extension store so it appears properly in the portal.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: Builds the final-status summaries for the three seeded turns. These summaries include what the model used, token and cost numbers, and, for the final turn, a pending question for the user.

**Data flow**: It takes no outside input beyond the `KitchenSink` instance. It creates three `TerminalFrame` objects: two plain completed frames and one completed frame that carries a multi-part user-input request with options and free text.

**Call relations**: `KitchenSink._open` calls this while inserting the turn rows. The returned terminal frames become the stored end state that the portal later renders for each seeded turn.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–371)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: Creates the nested subagent part of the demo conversation. This lets the portal show not only a main chat, but also an agent run that spawned another agent run.

**Data flow**: It receives the main conversation ID and the parent turn ID. It creates a child subagent run, creates a grandchild subagent run under that child, and writes a small transcript blob for the child conversation showing a tool call and result.

**Call relations**: `KitchenSink.write` calls this after opening the main conversation. `_runs` calls `_run` twice to create the database rows for the subagent chain, then writes the transcript blob needed to display one of those subagent conversations.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 373–408)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: Creates one completed subagent conversation with one completed turn. It is the small reusable piece used to build the child and grandchild runs in the demo.

**Data flow**: It receives the turn ID to use, the parent turn ID, the subagent profile name, and the answer text. It creates a new conversation ID, inserts a subagent conversation whose queue key points to the parent, inserts a completed turn with the answer encoded in its terminal frame, and returns the new conversation ID.

**Call relations**: `KitchenSink._runs` calls this for each level of the subagent tree. `_run` writes the concrete database records, and `_runs` uses the returned conversation ID when it needs to attach transcript data.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 410–431)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: Adds shared files to the seeded conversation so the portal can show attachments/artifacts. The files are a markdown audit and a CSV metrics sheet.

**Data flow**: It receives the turn ID that should own the files. For each built-in file body, it encodes the content, stores it in blob storage under a fresh artifact key, and inserts a `shared_artifact` database row with filename, media type, size, and ownership details.

**Call relations**: `KitchenSink.write` calls this after creating the subagent runs. It writes both sides of an attachment: the bytes in blob storage and the database row that lets the conversation UI find and label those bytes.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 433–499)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: Builds the visible transcript messages for the main kitchen-sink conversation. These messages are the scripted chat story that users will read in the portal.

**Data flow**: It receives the three turn IDs. It creates a sequence of user and assistant messages, including tool-use blocks, tool-result blocks, markdown text, a table, and a code snippet, with user messages framed by their matching turn IDs.

**Call relations**: `KitchenSink.write` calls this when it is ready to store the main transcript blob. `_said` uses `_framed` for turn-linked user messages and returns the message sequence that `write` wraps in a `Conversation` and encodes for blob storage.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).


### Agent setup and provisioning
Runtime agent setup describes remaining configuration needs and provisioning applies shipped extension agents without overwriting member edits.

### `core/src/ufo/runtime/kinds/agent_setup.py`

`domain_logic` · `request handling`

A shipped agent arrives with its core behavior, but often not with the outside access or wake-up rules it needs. For example, it may need a Google connection, an API key stored by the workspace, or a schedule that runs it every morning. This file gives the project a shared language for describing those needs and checking whether they have been satisfied.

Most of the file is made of Pydantic models, which are structured data objects that also check their own rules. AgentSetup is the declaration: what the app says it needs. SetupState is the readout: what the current workspace and current member have actually provided. The difference matters because setup is not a one-time flag. A member can revoke a connection, an admin can add a credential later, or a schedule can be deleted. So the state is rebuilt from live data each time.

The file is careful about scheduled tasks. If an app says it needs a clock to wake it, it must also offer the schedule choices that make sense for that app. This avoids showing a member a need they cannot settle from the setup screen. It is like putting a “needs batteries” label on a device: the label should also say what kind of batteries fit.

#### Function details

##### `SetupCadence._hourly_spans_every_day`  (lines 45–53)

```
def _hourly_spans_every_day(self) -> 'SetupCadence'
```

**Purpose**: This validator checks that a proposed schedule cadence makes sense. In particular, an hourly cadence cannot also name weekdays, because there is no single local hour that can be converted cleanly into calendar days.

**Data flow**: It reads the cadence fields already placed on the SetupCadence object: hour, minute, and weekdays. If the hour is missing, meaning “run every hour,” it rejects any weekday list. It also rejects weekday numbers outside 0 through 6. If everything is valid, the same cadence object comes out unchanged.

**Call relations**: This runs automatically when a SetupCadence is created or validated by Pydantic. Other setup objects rely on this check so that schedule choices shown to members are possible to convert into real scheduled jobs later.


##### `AgentSetup._a_clock_need_carries_its_offer`  (lines 135–152)

```
def _a_clock_need_carries_its_offer(self) -> 'AgentSetup'
```

**Purpose**: This validator keeps the agent’s setup declaration honest about scheduled tasks. If the agent says it needs a scheduled task, it must also include the schedule offer; if it includes a schedule offer, it must also declare that scheduled task need.

**Data flow**: It reads the AgentSetup object after its fields have been filled. It compares the standing needs with the optional schedule definition. If those two parts disagree, it raises an error. If they match, it returns the same setup object.

**Call relations**: This runs automatically when an AgentSetup is validated, including inside setup_state when the stored setup declaration is read from the database. It protects the rest of the setup flow from impossible screens, such as a schedule picker with no declared scheduled-task need.


##### `_armed`  (lines 236–248)

```
async def _armed(kind: str, wanted: AgentSetup, armed: Armed) -> ArmedOrder
```

**Purpose**: This helper asks whether the agent already has the standing order it needs, such as a scheduled task. For scheduled tasks, it asks about the app’s specific named task so one feature’s schedule is not mistaken for another’s.

**Data flow**: It receives a standing-order kind, the agent’s declared setup, and an async callback called armed that knows how to look up real standing orders. If the kind is the scheduled-task kind and the setup names a schedule, it asks about that exact schedule name. Otherwise, it asks whether any order of that kind exists. It returns an ArmedOrder saying whether one is held, and possibly what schedule it uses.

**Call relations**: setup_state calls this while building the standing-order part of the setup screen. _armed does not know where standing orders are stored; it hands that question to the provided armed callback because those objects belong to extension-specific parts of the system.

*Call graph*: called by 1 (setup_state).


##### `setup_state`  (lines 251–347)

```
async def setup_state(agent_id: UUID, member_id: UUID, *, armed: Armed) -> SetupState
```

**Purpose**: This is the main reader for an agent’s setup screen. It compares what the agent declared it needs with what the workspace and the current member actually have, then returns a SetupState ready for display.

**Data flow**: It takes an agent ID, a member ID, and an armed callback for checking standing orders. It opens a workspace database transaction, reads the agent’s stored setup declaration, and returns an empty state if there is none. If setup exists, it validates it as AgentSetup, reads account providers granted to this agent and usable by this member, reads workspace credentials for the declared credential slots, and also counts credentials supplied by deployment environment variables. After the database read, it builds connector rows, credential rows, and standing-order rows, then returns a SetupState containing both the settled and unsettled setup items plus the app’s instructions.

**Call relations**: This is called by code that needs to show or refresh an agent’s setup status. During its work it calls _armed for each declared standing-order kind, uses the database transaction helper to read live workspace data, and creates the SetupConnector, SetupCredentialState, SetupStanding, and SetupState objects that the caller can present to the member.

*Call graph*: calls 1 internal fn (_armed); 9 external calls (__init__, __init__, __init__, __init__, or_, select, workspace_tx, deploy_env, ws_current).


### `core/src/ufo/runtime/kinds/provisioning.py`

`domain_logic` · `workspace provisioning`

Extensions can ship ready-made agents. This file turns those extension declarations into normal agent records inside a workspace, much like unpacking a starter kit onto a user's desk. After that first unpacking, the workspace owns the copy: members can rename, edit, connect, or archive it without the extension constantly resetting it.

The central class, AgentProvisioning, walks through all active extension manifests and applies each declared agent. For each one, it first looks for an agent already known to have come from that exact extension and declared name. If it finds one, it usually leaves the member-controlled settings alone, but it may refresh the extension-owned setup data and fill in a missing purpose. If the provision is marked as the main agent, it can instead adopt the workspace's current main agent rather than replace it.

If no existing shipped agent is found, the file checks whether there is already a matching ordinary agent. If the existing row is the main agent, or is identical to what the extension would create, it is adopted by recording where it came from. Otherwise a new agent is inserted under a safe free name, so two extensions or a member and an extension do not fight over the same name.

The important promise is: extension-provided agents appear automatically, but member-owned changes are not silently overwritten.

#### Function details

##### `AgentProvisioning.apply`  (lines 54–62)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies every agent declaration from every active manifest to one workspace. This is the public entry for turning extension-declared agents into live workspace agents.

**Data flow**: It receives a workspace ID and reads the manifests stored on the AgentProvisioning object. It enters that workspace's context, then sends each manifest and each agent provision to _one. It returns a tuple of ProvisionOutcome records saying, for each declared agent, whether it was created, adopted, or already present.

**Call relations**: This function starts the provisioning pass. It uses the workspace context helper so later work happens in the right workspace, then repeatedly calls AgentProvisioning._one to do the real decision-making for each declared agent.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 64–147)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Applies one extension-declared agent to one workspace. It decides whether to refresh an existing shipped agent, adopt an existing workspace agent, or create a new one.

**Data flow**: It takes a workspace ID, one extension manifest, and one agent provision. Inside a workspace database transaction, it first searches for a row already tied to that extension and declared agent name. If found, it may update extension-owned fields through _fill and returns a PRESENT outcome. If not, it searches for a suitable existing ordinary agent to adopt. If adoption is safe, it records the extension identity on that row and returns ADOPTED. If neither path works, it asks _free_name for an unused name, calls _create to insert the new row, and returns CREATED.

**Call relations**: AgentProvisioning.apply calls this once per declared agent. This function is the traffic controller: it opens the transaction, queries and updates the agent table, calls _fill for already-shipped rows, _identical to judge safe adoption, _free_name to avoid name clashes, and _create when a new row is needed.

*Call graph*: calls 4 internal fn (_create, _fill, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._fill`  (lines 149–189)

```
async def _fill(self, connection: AsyncConnection, shipped: sa.Row, manifest: Manifest, provision: AgentProvision) -> None
```

**Purpose**: Updates only the extension-owned parts of an already shipped agent. It keeps the setup declaration current and fills in the purpose only if the workspace copy does not already have one.

**Data flow**: It receives a database connection, the existing shipped agent row, the manifest, and the provision. It compares the stored setup with the setup declared by the current manifest. If setup changed, it prepares a setup update. If the row has no purpose, it prepares a purpose update from the provision. If there is nothing to change, it does nothing. Otherwise it writes the changed fields, updates the recorded provision version, and refreshes the updated time.

**Call relations**: AgentProvisioning._one calls this when it finds an agent already tied to the same extension and declared name. It deliberately does not rewrite normal agent settings, because those belong to the workspace member after the first creation.

*Call graph*: called by 1 (_one); 2 external calls (execute, update).


##### `AgentProvisioning._free_name`  (lines 191–216)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Finds a usable agent name when the extension's declared name may already be taken. This prevents an extension from overwriting a member's agent or another extension's agent.

**Data flow**: It receives a database connection, workspace ID, extension name, and declared agent name. It reads all active agent names in the workspace. It then tries the declared name, then a name with the extension added as a suffix, then numbered variants. It returns the first name not already in use, or raises an error if it cannot find one within the limit.

**Call relations**: AgentProvisioning._one calls this only when it has decided a new agent must be created. The returned safe name is passed directly to _create so the insert does not collide with existing workspace names.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 218–275)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Inserts a new ordinary agent row for an extension-provided agent. The new agent starts with the extension's declared prompt, model, tools, setup, and identity, but without extra member-granted connections or credentials.

**Data flow**: It receives a database connection, workspace ID, manifest, provision, and the chosen agent name. It reads the provision's spec and icon. If no icon was declared, it reads existing workspace icons and asks auto_agent_icon to choose one. It then inserts a new agent row with a fresh UUID, the provision's settings, the extension identity, setup data, and timestamps. If another concurrent process already inserted the same provision, the insert quietly does nothing instead of failing the user's work.

**Call relations**: AgentProvisioning._one calls this after _free_name has selected an available name. This is the final creation step, and it relies on database conflict handling so two simultaneous provisioning passes do not crash over the same extension-provided agent.

*Call graph*: called by 1 (_one); 4 external calls (execute, select, auto_agent_icon, uuid4).


##### `AgentProvisioning._identical`  (lines 277–290)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing workspace agent already matches what the extension would create. If it does, the system can adopt that row instead of making a duplicate.

**Data flow**: It receives a database row and an agent provision. It compares the row's prompt, model, reasoning setting, internet access setting, sandbox size, visibility, and tools with the provision's declared values. It returns true only when all compared fields match.

**Call relations**: AgentProvisioning._one uses this when it finds an ordinary, unarchived agent with the provision's declared name. A true result lets _one adopt the existing row; a false result tells _one to leave that row alone and create a separate shipped agent under a safe name.

*Call graph*: called by 1 (_one).

## 📊 State Registers Touched

- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-workspace-directory` — The shared record of workspaces, members, seats, admins, invitations, and onboarding status.
- `reg-credential-connections` — The encrypted outside-account credentials, reusable connections, and grants that let agents use them.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
