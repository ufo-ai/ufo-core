# Workspace Onboarding and Seating  `stage-3.2`

This stage is part of startup and early workspace use. It prepares a new installation so people have a place to work, and it controls who is allowed to sit in that workspace and use the assistant. The package marker simply makes the onboarding folder importable by the rest of the program.

The main setup file runs first-time onboarding. It creates the first workspace, the first administrator, and the main assistant agent, then lets installed extensions add their own setup steps. The control file is the trusted doorway used by the Rust control plane, the outer service that coordinates the system. Through it, that layer can create or find workspaces, list sign-in choices, count workspaces, and read invitations without copying sensitive rules.

The seats file is the gatekeeper. It creates members, grants or removes seats, decides which members the agent may answer, and prevents the last seated administrator from being removed. The seed file adds a realistic demo conversation to permanent storage, like a showroom display, so the web portal can be tested with rich sample data.

## Files in this stage

### Workspace setup entry points
Package setup and private onboarding entry points create or find workspaces, initialize first-run state, expose sign-in and invitation reads, and let extensions hook into setup.

### `core/src/ufo/onboard/__init__.py`

`other` · `import/package discovery`

This is an empty package file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside this directory using names like `ufo.onboard.something`. Think of it like a label on a drawer: the drawer may contain useful tools, but this label simply tells Python, “this drawer is part of the organized system.” Without this file, depending on the Python version and packaging setup, imports from `ufo.onboard` might not work reliably or might be harder for tools to understand. Because it is empty, it does not run setup code, expose shortcuts, or change any settings when imported.


### `core/src/ufo/onboard/onboard_control.py`

`orchestration` · `request handling`

This file is the guarded front desk for hosted onboarding. A sign-in gateway can call these routes only if it presents a special bearer token, like showing a staff badge before being allowed behind the counter. Once inside, the file applies the project’s core rules for what a workspace is, who belongs to it, and how a new workspace starts.

The main route, called “seat,” takes a verified email and a workspace ID. It checks that the email, domain, and signup subject agree, creates the workspace if it is truly the one that subject owns, creates or finds the member, adds the default main agent, and gives a new workspace its starting balance. If an operator supplied a model-provider key, it stores that key in the new member’s own credential slot after the database work is safely committed.

Other routes answer questions needed during sign-in: whether a chosen membership still exists, which workspaces an address may enter, how many workspaces exist, and which invited teammates need email follow-up. Some of these reads intentionally look across all workspaces, so they use the owner-level database path and log warnings where the code comments say operators should notice that kind of access.

#### Function details

##### `MemberModelKey._served`  (lines 120–123)

```
def _served(cls, provider: str) -> str
```

**Purpose**: This validates that a supplied model-provider name is one the system knows how to route for a member. It prevents storing a credential under a provider label that no later code can use.

**Data flow**: It receives a provider string from the incoming request. It compares that string with the allowed provider names from the member routing table. If it is allowed, the same string comes back; if not, request validation fails with a clear error.

**Call relations**: This runs automatically as part of Pydantic request validation when a `MemberModelKey` is built. It consults `MEMBER_ROUTED_SLOTS.values` so the accepted names stay tied to the runtime credential routing table.

*Call graph*: 1 external calls (values).


##### `MemberModelKey.slot`  (lines 125–126)

```
def slot(self) -> str
```

**Purpose**: This turns a human-facing provider name into the internal credential slot name where that member’s key should be stored. Someone uses it when they have a provider like an AI model vendor and need the matching storage location.

**Data flow**: It reads the validated provider already stored on the object. It walks the member routing table until it finds the internal slot whose served provider matches. It returns that slot name as a string.

**Call relations**: The seating route calls this after the member has been created and before saving the model key. It uses `MEMBER_ROUTED_SLOTS.items` to reverse the same mapping that validation checked earlier.

*Call graph*: 1 external calls (items).


##### `_inert`  (lines 193–207)

```
def _inert(answer: str) -> str
```

**Purpose**: This defuses intake-form text so it cannot accidentally look like a prompt variable later. It protects a workspace from being broken by public form input containing special double braces such as `{{name}}`.

**Data flow**: It receives one free-text answer from the intake form. It repeatedly thins doubled opening and closing braces until no doubled brace remains. It returns readable text that still resembles what the person typed, but no longer contains the special pattern that prompt rendering treats as a variable.

**Call relations**: `agent_prompt` calls this before placing public intake answers into the main agent’s prompt. It is a small safety step in the larger flow that turns unauthenticated form data into cautious background context.

*Call graph*: called by 1 (agent_prompt).


##### `agent_prompt`  (lines 210–229)

```
def agent_prompt(profile: SignupProfile | None) -> str
```

**Purpose**: This builds the first system prompt for a new workspace’s main agent. If the signup included intake answers, it includes them as untrusted background information rather than as instructions the agent should blindly obey.

**Data flow**: It receives either no profile or a profile with business and goals text. With no profile, it returns the normal default agent prompt unchanged. With a profile, it defuses the answers with `_inert`, wraps them with `wall` to mark them as untrusted outside text, and returns a combined prompt.

**Call relations**: `OnboardControl._seat` calls this while creating the default agent for a new or newly seated workspace. It hands off to `_inert` for brace safety and to `wall` for the project’s standard “this text came from outside” protection.

*Call graph*: calls 1 internal fn (_inert); called by 1 (_seat); 1 external calls (wall).


##### `_labelled`  (lines 232–268)

```
def _labelled(rows: Sequence[sa.RowMapping], subject: str) -> list[WorkspaceChoice]
```

**Purpose**: This turns raw database rows about possible workspaces into a clean list a signing-in person can choose from. It also refuses an ambiguous case where one signup subject appears to map to more than one workspace.

**Data flow**: It receives database rows and the verified signup subject. It computes each workspace’s display subject from its first member, filters subject matches so only the correct workspace remains, notes which workspaces the email is already a member of, and adds a short UUID prefix when two labels would look the same. It returns a list of `WorkspaceChoice` objects, or raises an HTTP error if the subject is ambiguous.

**Call relations**: `OnboardControl._choices` calls this after doing the cross-workspace query. It relies on `workspace_subject` to derive the same label the rest of onboarding uses, then packages the result for the API response.

*Call graph*: called by 1 (_choices); 3 external calls (__init__, HTTPException, workspace_subject).


##### `_verified_signup`  (lines 271–290)

```
def _verified_signup(email: str, domain: str | None, signup_subject: str | None) -> tuple[str, str, str]
```

**Purpose**: This checks that the claimed domain or signup subject really follows from the verified email address. It stops a caller from using one verified address to claim someone else’s company domain or exact email subject.

**Data flow**: It receives an email, an optional domain, and an optional signup subject. It trims and lowercases them, derives the email’s domain, checks that the stated values are either the verified domain or the exact email where allowed, and fills in the missing subject when needed. It returns the normalized member email, verified domain, and signup subject, or raises an HTTP validation error.

**Call relations**: Both `OnboardControl._seat` and `OnboardControl._choices` call this before trusting signup identity information. It delegates only the basic domain extraction to `email_domain` and performs the policy checks here.

*Call graph*: called by 2 (_choices, _seat); 2 external calls (HTTPException, email_domain).


##### `OnboardControl.router`  (lines 300–307)

```
def router(self) -> APIRouter
```

**Purpose**: This creates the FastAPI router for the private onboarding endpoints. It is the place where the five URL paths are connected to the methods that answer them.

**Data flow**: It starts with the control token stored on the `OnboardControl` object. It creates an API router under `/internal/onboard`, attaches the token guard as a required dependency, registers the seat, membership, choices, fleet, and invitations routes, and returns the router to be mounted by the application.

**Call relations**: During API setup, the application asks this object for its router. The router uses FastAPI’s dependency mechanism so `OnboardControl._guard` runs before any registered route method is allowed to read or write data.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `OnboardControl._guard`  (lines 309–311)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: This is the lock on the private onboarding API. It rejects any request that does not present exactly the configured bearer token.

**Data flow**: It receives the HTTP `Authorization` header, or an empty string if none was provided. It compares that header with `Bearer <control_token>`. If they match, nothing is returned and the request continues; if not, it raises a 401 unauthorized error.

**Call relations**: `OnboardControl.router` attaches this guard to every onboarding route. FastAPI calls it before route methods like `_seat`, `_choices`, or `_fleet`, so failed authentication stops the request before any database access.

*Call graph*: 1 external calls (HTTPException).


##### `OnboardControl._seat`  (lines 313–406)

```
async def _seat(self, request: SeatRequest) -> EnsuredWorkspace
```

**Purpose**: This creates or confirms a workspace for a verified signup and seats the member in it. It is the central onboarding write path: workspace creation, first admin selection, default agent creation, signup credit, and optional member model key storage all happen here.

**Data flow**: It receives a `SeatRequest` containing a workspace ID, email identity fields, optional intake profile, and optional model key. It verifies the signup identity, enters the workspace context, opens a workspace-scoped database transaction, creates the workspace if missing, locks it, checks that the workspace belongs to the signup subject, creates or finds the member, inserts the default agent prompt, credits a newly founded workspace, and reads whether the member is an admin. After the transaction, it stores the optional model key in the member’s credential slot. It returns an `EnsuredWorkspace` saying which workspace was used, whether the member is an admin, and whether this sign-in founded it.

**Call relations**: This route is called through the router after `_guard` succeeds. It calls `_verified_signup` for identity safety, `agent_prompt` for the default agent text, database insert/select helpers for persistent records, `create_member` for seat rules, billing helpers for the signup grant, and credential helpers if a model key was supplied.

*Call graph*: calls 2 internal fn (_verified_signup, agent_prompt); 14 external calls (__init__, HTTPException, insert, select, workspace_tx, member_slot, credit, set_reserve, create_member, signup_workspace_id (+4 more)).


##### `OnboardControl._membership`  (lines 408–428)

```
async def _membership(self, workspace_id: UUID, email: str) -> Membership
```

**Purpose**: This checks whether an email is still a member of a chosen workspace and whether that member is an admin. It prevents a removed user from being silently recreated during sign-in.

**Data flow**: It receives a workspace ID and email address. It normalizes the email, enters that workspace’s context, and queries the workspace-scoped database for the matching member’s admin flag. If no member exists, it raises a 404 error; otherwise it returns a `Membership` object containing the admin status.

**Call relations**: This route is used after a person has picked a workspace from the choices list. It uses the workspace-scoped transaction path rather than the owner path, because it is checking one known workspace, and it returns only the small fact the sign-in flow needs next.

*Call graph*: 5 external calls (__init__, HTTPException, select, workspace_tx, ws).


##### `OnboardControl._choices`  (lines 430–455)

```
async def _choices(self, email: str, domain: str | None=None, signup_subject: str | None=None) -> WorkspaceChoices
```

**Purpose**: This lists every workspace a verified address may enter. That includes workspaces where the exact email is already a member and, when appropriate, the deterministic workspace named by the verified signup subject.

**Data flow**: It receives an email plus optional domain and signup subject. It normalizes and verifies those fields, logs that a cross-workspace read is happening, runs an owner-level SQL query to find matching memberships and subject-owned workspaces, converts the raw rows into user-facing choices with `_labelled`, and returns them inside `WorkspaceChoices`.

**Call relations**: This route is called during sign-in before the person chooses where to go. It calls `_verified_signup` to keep identity claims honest, uses `owner_tx` because the answer may span many workspaces, and hands the database rows to `_labelled` so the response has safe labels and membership flags.

*Call graph*: calls 2 internal fn (_labelled, _verified_signup); 5 external calls (__init__, text, owner_tx, warn, signup_workspace_id).


##### `OnboardControl._fleet`  (lines 457–464)

```
async def _fleet(self) -> Fleet
```

**Purpose**: This returns the total number of workspaces, called “craft” in the response. It supports a landing-page style view that wants a live count of the fleet.

**Data flow**: It takes no request-specific data. It logs that a cross-workspace read is happening, opens an owner-level database transaction, counts rows in the workspace table, and returns a `Fleet` object with that count.

**Call relations**: This route is exposed through the onboarding router and protected by `_guard`. It uses `owner_tx` because counting all workspaces is outside any single workspace boundary, and it records a warning for operator visibility.

*Call graph*: 4 external calls (__init__, select, owner_tx, warn).


##### `OnboardControl._invitations`  (lines 466–539)

```
async def _invitations(self, after_invited_at: datetime | None=None, after_workspace_id: UUID | None=None, after_email: str | None=None) -> Invitations
```

**Purpose**: This returns one page of pending teammate invitations, oldest first. It lets an external sweeper send or process invitation emails without loading the entire table at once.

**Data flow**: It receives an optional cursor made of three parts: invited time, workspace ID, and email. If only part of the cursor is present, it rejects the request because paging would be unsafe. It builds a database query for invited members, joins to the inviter and first workspace member for email and label details, applies the cursor if present, limits the result to one page, and returns `Invitation` objects inside `Invitations`.

**Call relations**: This route is called by a repeating invitation sweep rather than by an interactive sign-in. It uses `owner_tx` because invitations can belong to any workspace, builds the page with SQLAlchemy query pieces, and uses `workspace_subject` so each invitation names the workspace the same way the choices screen does.

*Call graph*: 9 external calls (__init__, __init__, HTTPException, DateTime, literal, select, tuple_, owner_tx, workspace_subject).


### `core/src/ufo/onboard/onboarding.py`

`orchestration` · `first-run startup/init`

This is the “cold start” path behind initialization. When a brand-new installation is set up, the system needs a safe, repeatable way to create its permanent basics: a workspace, an admin member, and the main agent the user will talk to. Without this file, first run could leave the system half-created, missing credentials, or accidentally create duplicate workspaces and admins.

The flow is deliberately cautious. Before touching the database, it checks that the chosen model has the needed environment variable for its API key, if the system knows one is required. It also checks whether extension onboarding steps need a credential store key. This is like checking that you have the house keys before pouring the foundation.

Once those checks pass, it opens a database transaction and creates the workspace, admin member, and main agent together. A transaction means the database changes are treated as one unit: either the important pieces are saved together, or the operation fails. If a member already exists, it raises `AlreadyInitialized` instead of creating duplicates.

After the core workspace exists, extensions are allowed to run onboarding steps using their own scoped context. Extension failures are logged and skipped, so a broken add-on cannot prevent the main workspace from existing or stop other extensions from trying their setup.

#### Function details

##### `run_onboarding_steps`  (lines 51–79)

```
async def run_onboarding_steps(manifests: tuple[Manifest, ...], workspace_id: UUID, credentials: CredentialStore | None) -> None
```

**Purpose**: Runs the first-time setup steps provided by installed extensions for a newly created workspace. It keeps extension failures isolated, so one bad extension does not break the core setup or block other extensions.

**Data flow**: It receives the installed extension manifests, the new workspace ID, and an optional credential store. It enters that workspace’s context, walks through each extension, builds the extension’s scoped context from its declared credential slots, and calls each onboarding step. If credentials are missing, it logs that the steps were skipped for that extension; if a step fails, it logs the failure and continues.

**Call relations**: After the main workspace has been created, `Onboarding.run_steps` calls this function to let extensions finish their own setup. Inside that flow, it uses the workspace context helper to make the workspace current, asks `context_for` for the extension-specific view of credentials and services, and uses logging to record skipped or failed steps.

*Call graph*: called by 1 (run_steps); 3 external calls (log, context_for, ws).


##### `Onboarding.run`  (lines 95–98)

```
async def run(self) -> Onboarded
```

**Purpose**: Runs the full onboarding process from start to finish. It is the high-level method for creating the core workspace and then running extension setup.

**Data flow**: It starts with the onboarding object’s stored configuration, email address, model choice, credentials, manifests, and reasoning setting. It first creates the core workspace and receives the IDs of the new workspace and member. It then runs follow-up steps for provisioning and extensions, and finally returns the created workspace/member identity.

**Call relations**: This is the top-level method other code would call when it wants initialization to happen. It delegates the careful core creation to `Onboarding.create`, then hands the result to `Onboarding.run_steps` so post-creation work happens only after the essentials exist.

*Call graph*: calls 2 internal fn (create, run_steps).


##### `Onboarding.create`  (lines 100–106)

```
async def create(self) -> Onboarded
```

**Purpose**: Creates only the durable core of the installation: required checks, workspace, first admin, and main agent. It intentionally does this before extension steps so the system is usable even if an add-on later fails.

**Data flow**: It reads the onboarding object’s model, configuration, credentials, manifests, email, and reasoning choice. First it checks for the model key, then checks whether extension steps require a credential key, and only then writes the core records to the database. The result is an `Onboarded` value containing the new workspace ID and member ID.

**Call relations**: `Onboarding.run` calls this as the first phase of initialization. This method coordinates three smaller checks/actions: `_require_model_key`, `_require_credentials_for_steps`, and `_create_workspace`.

*Call graph*: calls 3 internal fn (_create_workspace, _require_credentials_for_steps, _require_model_key); called by 1 (run).


##### `Onboarding.run_steps`  (lines 108–110)

```
async def run_steps(self, onboarded: Onboarded) -> None
```

**Purpose**: Runs the setup that happens after the core workspace exists. This includes provisioning agents from extensions and running extension onboarding steps.

**Data flow**: It receives the `Onboarded` result from core creation, mainly using the workspace ID. It applies agent provisioning based on the installed manifests, then calls the shared extension onboarding function with the manifests, workspace ID, and credential store. It does not return a value; its effect is the extra setup it performs.

**Call relations**: `Onboarding.run` calls this after `Onboarding.create` succeeds. It first hands control to `AgentProvisioning` for extension-provided agent setup, then hands control to `run_onboarding_steps` so each extension can run its declared onboarding tasks.

*Call graph*: calls 1 internal fn (run_onboarding_steps); called by 1 (run); 1 external calls (__init__).


##### `Onboarding._require_credentials_for_steps`  (lines 112–123)

```
def _require_credentials_for_steps(self) -> None
```

**Purpose**: Stops initialization early if installed extensions have onboarding steps but no credential store is available. This prevents creating a workspace that immediately cannot complete required extension setup.

**Data flow**: It looks at the onboarding object’s credential store and extension manifests. If a credential store exists, it allows the process to continue. If no store exists but at least one extension has onboarding steps, it raises an error explaining which environment setting is needed.

**Call relations**: `Onboarding.create` calls this before any database write. It acts as an early gate, similar to the model-key check, so the system avoids leaving behind a half-initialized workspace.

*Call graph*: called by 1 (create).


##### `Onboarding._require_model_key`  (lines 125–133)

```
def _require_model_key(self) -> None
```

**Purpose**: Checks whether the selected model needs an environment-provided key before the first conversation can work. If the key is required but missing, it stops initialization with a clear error.

**Data flow**: It asks `_model_key_env` for the name of the environment variable needed by the selected model. If no known key is required, it does nothing. If a key name is returned, it checks the deployment environment for that value and raises an error if it is absent.

**Call relations**: `Onboarding.create` calls this before creating database records. It relies on `_model_key_env` to identify the needed key and on `deploy_env` to read the environment in the same way the deployed system expects.

*Call graph*: calls 1 internal fn (_model_key_env); called by 1 (create); 1 external calls (deploy_env).


##### `Onboarding._model_key_env`  (lines 135–138)

```
def _model_key_env(self) -> str | None
```

**Purpose**: Finds the environment variable name that supplies the API key for the chosen model, when the system knows it ahead of time. Some extension-provided model providers resolve their keys later, so this can return nothing.

**Data flow**: It reads the configuration, installed manifests, and selected model name from the onboarding object. It builds or consults the model registry, then asks that registry what environment variable is associated with the model. It returns the variable name, or `None` if there is no eager check to perform.

**Call relations**: `Onboarding._require_model_key` calls this to decide whether there is a model key to check before initialization. It delegates provider knowledge to `model_registry`, rather than hard-coding every model’s credential rules here.

*Call graph*: called by 1 (_require_model_key); 1 external calls (model_registry).


##### `Onboarding._create_workspace`  (lines 140–167)

```
async def _create_workspace(self) -> Onboarded
```

**Purpose**: Writes the first permanent records for a new installation: the workspace, the first admin member, and the main agent. It also prevents accidental double-initialization.

**Data flow**: It opens a workspace database transaction, checks whether any member already exists, and stops with `AlreadyInitialized` if one does. Otherwise it creates fresh IDs, inserts the workspace, creates the admin member using the provided email address, inserts the main agent with the chosen model and default prompt/icon/name, and returns an `Onboarded` value with the new workspace and member IDs.

**Call relations**: `Onboarding.create` calls this only after credential and model checks pass. It uses the database transaction helper so the core records are created together, calls `create_member` for the member-specific work, uses SQLAlchemy to build database inserts and selects, and returns the identity that later steps need.

*Call graph*: called by 1 (create); 7 external calls (__init__, __init__, insert, select, workspace_tx, create_member, uuid4).


### Demo durable records
Sample onboarding data seeds a realistic conversation into durable storage so the portal can be inspected with representative records.

### `core/src/ufo/onboard/seed.py`

`domain_logic` · `onboarding/demo seed run`

This file is a seed writer: it builds a complete fake-but-realistic conversation directly in the database and blob storage. That matters because the normal extension interface is not allowed to invent finished conversation history. Finished turns, transcripts, costs, and audit-like records are sensitive internal state, so this code lives next to the system code that is already trusted to write those rows.

The main class, KitchenSink, acts like a careful stage crew. First it removes earlier demo runs that it knows it created. It identifies them using private queue keys, not by title, so it does not delete a real user’s conversation just because it has the same name. It also refuses to delete a prior run if a real member spoke in it or if transcript access was disclosed, because then it has become real workspace history.

After cleanup, it opens a new web conversation, inserts three completed turns, creates nested subagent conversations, stores two shared files, and writes the final transcript blob. The result is one conversation titled “Kitchen sink” that exercises many portal display paths at once. Without this file, testing visual changes to the conversation UI would require manually producing many hard-to-reach states.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: This helper creates a user message that includes a hidden reference to the turn it belongs to. It is used so the transcript can connect a visible user prompt back to the stored turn record.

**Data flow**: It takes a turn ID and the words the user supposedly said. It wraps the turn ID in a small context block, appends the user’s text, and returns a Message object marked as coming from the user.

**Call relations**: KitchenSink._said calls this when building the final transcript. It hands back framed user messages that become part of the conversation history written by KitchenSink.write.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: This is the top-level action that creates one fresh kitchen-sink conversation. Someone would use it when they want the workspace to contain a realistic demo conversation for checking the web portal.

**Data flow**: It starts with the KitchenSink object’s workspace, agent, member, email, and blob store. It creates new random IDs, clears safe-to-delete older demo data, writes the conversation rows, subagent runs, attached files, and final transcript, then returns the new conversation ID.

**Call relations**: This function is the main coordinator for the file. It calls _clear before writing anything, then _open, _runs, _files, and _said, and finally sends the encoded transcript to blob storage under the transcript key.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: This removes previous kitchen-sink demo runs that are safe to delete. It keeps repeated seed runs tidy without touching conversations that may have become real workspace history.

**Data flow**: It asks _prior for earlier seed-created runs. For each one, it deletes database rows through _drop, removes the web chat row from the extension store, and deletes related blobs such as artifacts and transcripts.

**Call relations**: KitchenSink.write calls this before creating the new demo. It relies on _prior to decide what is safe and on _drop to remove database records, then it finishes cleanup in the extension store and blob store.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: This searches for earlier kitchen-sink runs that this seed code created and that are still safe to erase. Its safety checks prevent the cleanup step from deleting records that now involve a real user action or transcript disclosure.

**Data flow**: It reads the workspace database. It finds root web conversations with the seed queue prefix, follows child subagent conversations through their queue keys, collects their turns, and then checks for any non-seed turn or transcript access row. It returns only the runs that pass those checks.

**Call relations**: KitchenSink._clear calls this at the start of cleanup. It produces _PriorRun records that tell _clear and _drop exactly which conversations and turns belong to a removable old seed run.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: This deletes the database records for one old seed run. It also reports which blob keys belonged to shared artifacts so the caller can remove the matching stored files afterward.

**Data flow**: It receives a _PriorRun containing conversation IDs and turn IDs. It reads shared artifact blob keys, deletes shared artifact rows, conversation change rows, turn rows, and conversation rows, then returns the artifact blob keys it found.

**Call relations**: KitchenSink._clear calls this after _prior has declared a run safe to remove. _drop removes database state, and _clear uses its returned blob keys to clean up blob storage too.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: This creates the main web conversation and its three completed turns. It also gives the conversation its visible title and creates the chat row the web portal expects.

**Data flow**: It receives a new conversation ID and three turn IDs. It inserts a conversation row, inserts one completed turn for each terminal frame from _terminals, retitles the conversation to “Kitchen sink,” and writes a small chat record containing the agent ID and user email.

**Call relations**: KitchenSink.write calls this after cleanup. It uses _terminals to get the finished-turn summaries, writes rows inside a workspace database transaction, then calls the surface retitling helper and the scoped extension store so the web surface can find the conversation.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: This builds the final summary frames for the three demo turns. These frames include model name, token count, cost, and, for the last turn, a still-open question for the user.

**Data flow**: It takes no outside input beyond the KitchenSink object. It creates three TerminalFrame objects: two plain completed frames and one completed frame that contains multiple user questions with options and free text. It returns them as a tuple.

**Call relations**: KitchenSink._open calls this while inserting the turn rows. The terminal frames become the stored end state for the demo turns, which lets the portal display costs and pending user-input UI.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–371)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: This creates nested subagent activity for the demo conversation. It shows the portal what it looks like when a turn launches a subagent, and that subagent launches another one.

**Data flow**: It receives the main conversation ID and the parent turn ID that should appear to spawn work. It creates child and grandchild IDs, calls _run twice to insert the subagent conversations and their turns, then writes a transcript blob for the child subagent conversation.

**Call relations**: KitchenSink.write calls this after opening the main conversation. It delegates row creation to _run, then builds and stores a small transcript using message, text, tool-use, and tool-result blocks.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 373–408)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: This inserts one completed subagent conversation and its single completed turn. It is the small reusable piece that lets _runs build both the child and grandchild subagent examples.

**Data flow**: It receives a turn ID, a parent turn ID, a subagent profile name, and the answer text the subagent should report. It creates a new conversation ID, inserts a subagent conversation tied to the parent, inserts one completed turn with a JSON-like result in its terminal frame, and returns the new conversation ID.

**Call relations**: KitchenSink._runs calls this once for the first subagent and once for the nested subagent. The returned conversation ID is used when _runs writes the child transcript blob.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 410–431)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: This attaches two example files to one of the demo turns. The files let the portal show shared artifacts such as a Markdown report and a CSV data file.

**Data flow**: It receives the turn ID that should own the attachments. For each built-in file body, it encodes the text, writes the bytes to blob storage under a new artifact key, and inserts a shared_artifact database row with filename, media type, size, and ownership information.

**Call relations**: KitchenSink.write calls this after creating the conversation and subagent runs. It connects stored blob content to the selected turn so the web portal can list and open the attachments.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 433–499)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: This builds the visible transcript messages for the main conversation. It gives the demo realistic back-and-forth chat, including tool calls, tool results, assistant explanations, and the final request for user input.

**Data flow**: It receives the three turn IDs created for the conversation. It uses those IDs to frame user prompts, creates message objects for assistant tool use and user tool results, and returns the full ordered message tuple.

**Call relations**: KitchenSink.write calls this at the end, wraps its returned messages in a Conversation object, encodes it, and stores it in blob storage. It uses _framed for the user messages that need to point back to specific turns.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).


### Member seating rules
Seat management logic creates members, grants or removes seats, decides who agents may answer, and preserves at least one seated administrator.

### `core/src/ufo/runtime/seats.py`

`domain_logic` · `cross-cutting: admission, member creation, seat changes, and per-turn access checks`

A “seat” here means permission for a workspace member to receive answers from the agent. The member record itself is treated like an identity record: removing a seat does not delete the person, it only stops the agent from answering them. This matters because a revoked person may still appear in old work, scheduled tasks, or history, and the system needs one consistent answer everywhere: no seat, no response.

The file provides two main kinds of tools. The Seats class works inside one workspace. It can check whether one member, or a group of members, is still seated; show a snapshot of all members and their seat status; grant a seat back; or revoke a seat. Revoking is careful: it locks the workspace row before counting admins, like asking everyone to line up at one counter, so two admins cannot both remove the other at the same time and leave no seated admin.

The rest of the file supports member lookup and creation. It normalizes and checks email addresses, derives workspace identity from email domains or exact signup addresses, finds members safely within a workspace, and inserts new members in one shared path. That shared path prevents different entry points from applying different rules.

#### Function details

##### `gate_member`  (lines 37–48)

```
def gate_member(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat access. If there is a direct speaker, that person is checked; otherwise the member the work is being done for is checked.

**Data flow**: It receives a possible speaker member ID and a possible “on behalf of” member ID. It returns the speaker ID when present, otherwise the on-behalf-of ID, or nothing if neither exists. It changes no stored data.

**Call relations**: This small rule is shared by admission and later checks so the system does not disagree about whose seat matters for a turn. It does not call out to the database; it simply gives the rest of the seating flow one member ID to use.


##### `SeatSnapshot.seated`  (lines 72–73)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently have seats. It is a convenient summary for screens or tools that show seat status.

**Data flow**: It reads the snapshot’s member entries, counts entries whose seated flag is true, and returns that number. It does not change the snapshot.

**Call relations**: Seats.snapshot builds the snapshot that contains these entries. This property then gives callers a simple total without making them repeat the counting logic.


##### `Seats.admits`  (lines 84–98)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Answers the basic access question: may the agent answer this member right now? A member is admitted only if they belong to this workspace and their seat has not been revoked.

**Data flow**: It receives a database connection and a member ID. It looks up that member row inside this workspace and checks whether the seated_at field is filled in. It returns true for a seated workspace member and false for an unknown or unseated member.

**Call relations**: This is the quick single-person check used whenever the system needs to decide if one member can proceed. It asks the database directly through the provided connection so the answer matches the current transaction.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 100–120)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether every member in a given group still has a seat. This is useful for work that may involve several members and must stop if any required person loses access.

**Data flow**: It receives a database connection and a collection of member IDs. If the collection is empty, it returns true. Otherwise it counts how many of those IDs are seated members of this workspace and returns true only when the count matches the requested set.

**Call relations**: This is the group version of Seats.admits. Larger flows can call it during repeated turn checks, resume checks, or dispatch checks to avoid doing one database trip per person.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 122–145)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a read-only picture of every member in the workspace and whether each one is seated and an admin. It is meant for reporting or seat-management views.

**Data flow**: It receives a database connection. It reads all member rows for this workspace in creation order, turns each row into a SeatEntry, wraps them in a SeatSnapshot, and returns that snapshot.

**Call relations**: Seat-management tools call this when they need to show the current state instead of changing it. It hands the result to SeatSnapshot, whose seated property can then summarize the count.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 147–157)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores access for a workspace member with a given email address. If the member already has a seat, it quietly does nothing.

**Data flow**: It receives a database connection and an email address. It first finds the member in this workspace by email. If the member is unseated, it writes the current time into seated_at and updates the row timestamp; otherwise there is no change.

**Call relations**: It relies on Seats._member_by_email to ensure the email belongs to this workspace and to get the current seat state. Admin-facing tools can call this to let an unseated member speak to the agent again.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 159–182)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes access for a workspace member with a given email address. It refuses to remove the last seated admin, because then nobody left in chat could restore seats.

**Data flow**: It receives a database connection and an email address. It first locks the workspace row so competing revokes happen one at a time, finds the member by email, and returns early if they are already unseated. If they are the only seated admin, it raises an error; otherwise it clears seated_at and updates the row timestamp.

**Call relations**: It uses Seats._member_by_email to identify the target and Seats._seated_admin_count when the target is an admin. It does not stop running work directly; instead, later admission and per-round checks see the revoked seat and refuse or park the work.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 184–201)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds one member of this workspace by email and returns the facts needed for seat changes. It raises a clear error when the email is not a member of the workspace.

**Data flow**: It receives a database connection and an email address. It trims and lowercases the email for comparison, reads the matching member row in this workspace, and returns the member ID, current seated_at value, and admin flag. If no row is found, it raises UnknownMember.

**Call relations**: Seats.grant and Seats.revoke use this helper before changing a seat. Keeping the lookup here means both operations apply the same email matching and workspace boundary.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 203–212)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in this workspace currently have seats. It exists to protect the rule that at least one seated admin must remain.

**Data flow**: It receives a database connection, counts member rows in this workspace where the member is both seated and an admin, and returns the count as a number.

**Call relations**: Seats.revoke calls this only when someone is trying to unseat an admin. The count is checked after the workspace row is locked, so concurrent revokes cannot both think another admin will remain.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 215–228)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts a safe, lowercased domain from an email address, such as “example.com” from “me@example.com”. If the value is not exactly one simple local@domain address with no whitespace, it returns an empty string.

**Data flow**: It receives a string, trims and lowercases it, splits it around the @ sign, rejects missing parts, extra @ signs, or whitespace, and returns the domain when valid. It changes no stored data.

**Call relations**: create_member uses this as the shared shape check before any member row is created. Workspace identity helpers also call it so domain matching and member creation agree about what counts as a usable email.

*Call graph*: called by 4 (create_member, workspace_by_domain, workspace_domain, workspace_subject).


##### `signup_workspace_id`  (lines 231–233)

```
def signup_workspace_id(subject: str) -> UUID
```

**Purpose**: Creates the deterministic workspace ID for a hosted signup subject. Deterministic means the same subject always produces the same UUID, like a repeatable label rather than a random ticket.

**Data flow**: It receives a subject string, lowercases it, feeds it into UUID version 5 generation using the DNS namespace, and returns the resulting UUID. It does not read or write the database.

**Call relations**: workspace_subject, workspace_domain, and workspace_by_domain use this to tell whether a workspace is identified by one exact email address or by a domain. That keeps signup addressing consistent across the file.

*Call graph*: called by 3 (workspace_by_domain, workspace_domain, workspace_subject); 1 external calls (uuid5).


##### `workspace_subject`  (lines 236–245)

```
def workspace_subject(first_email: str, workspace_id: UUID) -> str
```

**Purpose**: Returns the signup subject that represents a workspace: either the first member’s exact email address or that email’s domain. This gives invitations and workspace choices one shared label.

**Data flow**: It receives the first member’s email and the workspace ID. It checks whether that exact email would generate this workspace ID; if so, it returns the email. Otherwise it returns the email’s domain.

**Call relations**: It combines signup_workspace_id and email_domain. Other code can use this one answer instead of separately guessing how a workspace should be named.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id).


##### `workspace_domain`  (lines 248–266)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the email domain that owns a workspace, but only when the workspace is actually domain-based. Personal-email workspaces do not claim a whole provider domain like gmail.com.

**Data flow**: It receives a database connection and workspace ID. It reads the workspace’s first member email, extracts its domain, and checks whether the workspace ID was created from the exact email instead. It returns the domain for a domain workspace, or None when there is no member, no valid domain, or the workspace is personal-email based.

**Call relations**: It uses email_domain and signup_workspace_id to apply the same identity rule used elsewhere. Callers can ask this when deciding whether an email domain should automatically point to a workspace.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `workspace_by_domain`  (lines 269–302)

```
async def workspace_by_domain(connection: AsyncConnection, domain: str) -> UUID | None
```

**Purpose**: Looks up which workspace, if any, is addressed by a given email domain. It deliberately skips personal-email workspaces so shared email providers do not accidentally map to one user’s workspace.

**Data flow**: It receives a database connection and a domain string. It validates the domain by pretending it is part of an email, searches for workspaces whose first member email ends with that domain, orders candidates predictably, skips any workspace whose ID comes from the exact first email, and returns the first matching workspace ID or None.

**Call relations**: It uses email_domain to clean the input and signup_workspace_id to filter out personal-email workspaces. This supports join or signup flows that need to find a workspace from someone’s verified email domain.

*Call graph*: calls 2 internal fn (email_domain, signup_workspace_id); 2 external calls (execute, select).


##### `member_by_email`  (lines 305–321)

```
async def member_by_email(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID | None
```

**Purpose**: Finds the member ID for an email address inside one specific workspace. It returns None rather than creating anything when the address is not already a member.

**Data flow**: It receives a database connection, a workspace ID, and an email address. It lowercases and trims the email for comparison, searches only within the given workspace, and returns the matching member ID or None.

**Call relations**: This is a safe lookup helper for flows that have a verified email and a workspace and need to see whether the person is already a member. Its query includes the workspace boundary so it does not accidentally read a member from another workspace.

*Call graph*: 2 external calls (execute, select).


##### `member_is_admin`  (lines 324–335)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a given seated member is an admin in a workspace. Unseated admins do not count for this answer.

**Data flow**: It receives a database connection, workspace ID, and member ID. It searches for a matching member row that is in the workspace, seated, and marked as admin, then returns true or false.

**Call relations**: Admin-only actions can call this before allowing a change. It reads the same member table used by seat checks, so admin power depends on still holding a seat.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 338–409)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False, invited_by: UUID | None=None) -> UUID
```

**Purpose**: Creates a workspace member through the one shared path used by onboarding, invitations, and joins. New members are seated by default through the database row, and duplicate creation races return the already-created member instead of making a second identity.

**Data flow**: It receives a database connection, workspace ID, email, optional admin flag, and optional inviter ID. It validates the email shape, lowercases it, locks the workspace row, tries to insert a new member with timestamps and invitation details when present, and returns the new member ID. If another caller already created the same workspace/email row, it reads and returns that existing member ID instead.

**Call relations**: It calls email_domain so every member creation route obeys the same email rule. It uses a database insert that ignores uniqueness conflicts, which lets two simultaneous attempts for the same email safely collapse into one member.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 412–420)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate source for jobs that need to run once for every workspace that has at least one member. It keeps knowledge of the member table inside core code.

**Data flow**: It defines a small query that selects distinct workspace IDs from the member table, wraps that query as WorkspaceCandidates, and returns it. It does not run the query immediately.

**Call relations**: Extensions can ask for these candidates without writing their own direct member-table query. The nested member_workspaces.with_a_member function supplies the actual database selection when the candidate system needs it.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 417–418)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the database query for “all workspaces that have at least one member.” It is intentionally broad because the later job can decide what to do with each workspace.

**Data flow**: It takes no inputs from the caller. It builds and returns a SQL query selecting distinct workspace IDs from member rows. It does not execute the query itself.

**Call relations**: member_workspaces passes this query builder to owner_candidates. The candidate framework can later call it to find which workspaces should be considered for a member-related job.

*Call graph*: 1 external calls (select).
