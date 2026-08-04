# Security, Secrets, Permissions, and Grant Boundaries  `stage-21` (cross-cutting infrastructure)

This stage is the system’s security guardrail. It runs behind the scenes during sign-in, request handling, tool use, connector access, sandbox browsing, and file downloads. Its main job is to make sure every action is tied to the right workspace, person, agent, audience, and permission.

The workspace and database files set the boundary between customers: one marks “this is the active workspace,” while the database safety fence keeps rows from leaking across workspaces. Agent scope records which agent is acting. Audience labels control who may see conversation information. Bearer tokens identify a member and workspace without storing a server-side session. Operator session logic does the same for staff-only tools.

Several files protect secrets and access grants. Credential files show which secrets are needed, store them safely, verify credential requests, and reveal values only to the proxy at the last moment. The direct connector credential path lets background sync jobs use member-supplied API keys without exposing them to agents.

The remaining pieces sign and verify special tokens: for shared artifact downloads, sandbox hostnames and ports, early surface routing, and general tamper-proof payloads. Together they act like sealed labels and locked doors.

## Files in this stage

### Workspace and actor fences
Establishes the active workspace, database row isolation, and current agent authority used by later security checks.

### `core/src/ufo/workspace.py`

`domain_logic` · `cross-cutting`

A workspace is the project or customer area a request belongs to. Many parts of the system need to know that workspace before they can safely read a secret, write a bill, or touch workspace-owned database rows. This file provides that shared “current workspace” handle.

The main pattern is `with ws(workspace_id): ...`. Inside that block, `ws_current()` can recover the current workspace without every function needing a `workspace_id` argument. This is like putting a labeled folder on the desk: everything done while that folder is open goes into that folder, not someone else’s.

`WorkspaceScope` is the object returned for the current workspace. It is the only route to workspace credentials and billing. Credentials are looked up first in the workspace’s own credential store, which supports customer-provided keys. If none is stored, the code falls back to a platform-wide environment variable. If no key exists, it fails clearly.

Billing works the same way. Code opens a `billable_event()` block, records model usage as work happens, and the usage is written only if the block finishes successfully. If an error interrupts the block, no spend is recorded. This prevents failed calls from being charged and keeps costs tied to the workspace that was bound at the boundary of the job or user turn.

#### Function details

##### `init_workspace_credentials`  (lines 29–33)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that this process should use for workspace-specific secrets. If no store is provided, the system can still use platform-wide environment variable defaults, but it cannot read or write stored workspace credentials.

**Data flow**: A credential store object, or `None`, comes in during setup. The function saves it in this module’s shared `_store` variable. Later credential reads, writes, and rotations use that saved store if it exists.

**Call relations**: This is meant to be called once during application startup before workspace code needs secrets. It does not call other helpers itself; instead, it prepares the shared store that `WorkspaceScope.credential`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential` rely on later.


##### `BillableEvent.usage`  (lines 49–51)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Adds one piece of metered model usage to the current billing event. Code uses it each time an AI model or other priced service reports usage that should be charged if the overall operation succeeds.

**Data flow**: The caller provides a model name, a usage record, and optionally a pricing table. The function places those three items into the event’s private list. Nothing is written to the database yet; it is only queued for later billing.

**Call relations**: This is used inside a `WorkspaceScope.billable_event` block. The event collects usage during the block, and when that block exits cleanly, `WorkspaceScope.billable_event` reads the collected entries and passes them on to the accounting layer.


##### `WorkspaceScope.credential`  (lines 60–74)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Finds the secret value for a named credential slot, such as an API key, for this exact workspace. It first tries the workspace’s own stored credential and then falls back to a platform environment variable; if neither exists, it raises a clear configuration error.

**Data flow**: The function starts with the bound workspace id, a credential slot name, and optionally the name of an environment variable. If a credential store is configured, it asks that store for this workspace’s value. If the store says the slot is unset, it checks the environment using the supplied environment name or the uppercased slot name. It returns the secret string when found, or raises `CredentialSlotUnset` when no usable value exists.

**Call relations**: Workspace-scoped code reaches this through `ws_current().credential(...)`, so the secret lookup is tied to the workspace already bound by `ws`. When a stored credential is missing, it uses `CredentialSlotUnset` as the signal to fall back or, if the fallback also fails, to stop the caller with a loud error instead of silently using the wrong key.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.rotate_credential`  (lines 76–81)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing stored workspace credential only if the caller’s expected old value matches. This compare-before-replace behavior helps avoid overwriting someone else’s newer change by accident.

**Data flow**: The function receives a slot name, the expected current secret, and the new plaintext secret. If no credential store is configured, it returns `False` because there is nothing stored to rotate. Otherwise it asks the store to rotate the credential for this workspace and returns the store’s success or failure result.

**Call relations**: This is part of the credential-writing side of `WorkspaceScope`. It relies on the credential store installed by `init_workspace_credentials`; without that store, rotation cannot happen. It does not affect environment-variable defaults, because those are not workspace credential rows.


##### `WorkspaceScope.put_credential`  (lines 83–87)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores the first or replacement plaintext credential for this workspace in the configured credential store. It is used when an authorized owner supplies a workspace-specific secret.

**Data flow**: The caller gives a slot name and plaintext secret. The function checks that a credential store exists. If not, it raises a runtime error; if yes, it sends the workspace id, slot, and plaintext to the store to be saved.

**Call relations**: Like credential rotation, this depends on `init_workspace_credentials` having installed a store at startup. It is called through a `WorkspaceScope`, so the saved secret is attached to the currently bound workspace rather than being a global setting.


##### `WorkspaceScope.billable_event`  (lines 90–100)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a safe billing block for this workspace. Usage can be collected during the block, and it is written to the workspace ledger only if the block finishes without an error.

**Data flow**: When entered, it creates an empty `BillableEvent` and gives it to the caller. The caller adds usage records to that event while doing work. After the caller’s block finishes successfully, the function opens a workspace database transaction and records each queued usage item for this workspace. If there is no usage, it writes nothing; if the block raises an exception, the code after the yield is skipped and nothing is billed.

**Call relations**: This function constructs a `BillableEvent` for callers to fill. On success it hands each collected entry to `ufo.accounting.record_workspace_usage`, using a database connection from `ufo.db.workspace_tx`. This ties the accounting write to the same workspace context that was established by `ws`.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 104–112)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Temporarily binds a workspace id as the current workspace for a block of code. It is the boundary marker used by a user turn or background job so everything inside knows which workspace it belongs to.

**Data flow**: A workspace id comes in. The function sets that id into the current workspace context and yields a `WorkspaceScope` for the caller to use. When the block ends, even if an error happened, it resets the context back to its previous value.

**Call relations**: Code wraps workspace-scoped work in `with ws(workspace_id):`. Internally this uses `ufo.db.current_workspace.set` before yielding and `ufo.db.current_workspace.reset` afterward, and it creates a `WorkspaceScope` so callers can access credentials and billing for the bound workspace.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 115–121)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the workspace scope that is currently bound. If no workspace has been bound, it raises `WorkspaceUnbound` so credentialed, billed, or database-scoped work cannot continue in an unsafe ambiguous state.

**Data flow**: The function reads the current workspace id from the shared context. If an id is present, it wraps it in a new `WorkspaceScope` and returns it. If no id is present, it raises an error explaining that the caller must use `with ws(workspace_id):` first.

**Call relations**: This is the lookup counterpart to `ws`. `ws` sets the current workspace; later code calls `ws_current` to recover it. It uses `ufo.db.current_workspace.get` to read the context, creates `WorkspaceScope` when one exists, and creates `WorkspaceUnbound` when the context is missing.

*Call graph*: 3 external calls (__init__, __init__, get).


### `control/src/ufo_control/rls.py`

`io_transport` · `startup/bootstrap database provisioning`

This file protects a shared Postgres database where many workspaces live side by side. The risk is simple: if a query forgets to filter by workspace, one customer or project could see or change another’s data. To reduce that risk, the file uses Postgres row-level security, often called RLS. Row-level security is a database rule that automatically hides rows unless they match a condition, like a security guard checking every shelf before letting someone take an item.

The file has two main jobs. First, it provisions a “serve” database role with a predictable password derived from a secret seed. This role gets access to ordinary tables and sequences, but it is separate from the owner role that performs database setup. Second, it walks through every public table and makes sure each one has the expected workspace policy. The policy says: a row is visible or writable only when its workspace column matches the current database setting named app.workspace_id.

The code is careful about locks. Database schema changes can block if another session is holding a strong table lock, so it sets a short timeout. If a table is blocked, it reports which database sessions appear to be holding the lock instead of hanging forever. It skips Alembic’s migration bookkeeping table, because that table is not workspace data.

#### Function details

##### `owner_dsn`  (lines 22–26)

```
def owner_dsn() -> str
```

**Purpose**: Reads the database connection string for the Postgres owner user from an environment variable. This is needed because only an owner-level connection can safely create roles and apply database-wide setup.

**Data flow**: It looks in the process environment for UFO_CONTROL_POSTGRES_OWNER_DSN. If the value is present, it returns that connection string. If it is missing, it stops with a clear error so the system does not try to continue without the privileged database access it needs.

**Call relations**: This is a small entry helper for whichever startup or deployment code needs the owner connection string. No other function in this file calls it directly, but it belongs to the same setup flow as role creation and policy bootstrapping.


##### `serve_password`  (lines 29–33)

```
def serve_password() -> str
```

**Purpose**: Creates the password for the limited application database role. Instead of storing the password directly, it derives one from a secret seed and the role name.

**Data flow**: It reads UFO_CONTROL_PG_ROLE_SEED from the environment. If the seed is missing, it raises an error. If it exists, it combines the seed with the serve role name, hashes that text with SHA-256, and returns the hexadecimal password string.

**Call relations**: The role setup path calls this before creating or updating the serve role. The DSN-building helper also calls it so the generated application connection string uses the same derived password.

*Call graph*: called by 2 (ensure_serve_role, serve_dsn); 1 external calls (sha256).


##### `serve_dsn`  (lines 36–37)

```
def serve_dsn(postgres_host: str, app_database: str) -> str
```

**Purpose**: Builds the database connection string that normal serving code should use. That string points at the limited serve role instead of the more powerful owner role.

**Data flow**: It receives a Postgres host and an application database name. It asks serve_password for the derived password, then combines the role name, password, host, and database into a SQLAlchemy-style async Postgres URL. The result is a connection string.

**Call relations**: This function depends on serve_password so its password matches the role created by ensure_serve_role. Code outside this file can use the returned DSN when it wants to connect as the safer serve role.

*Call graph*: calls 1 internal fn (serve_password).


##### `ensure_serve_role`  (lines 40–64)

```
async def ensure_serve_role(admin_dsn: str) -> None
```

**Purpose**: Makes sure the limited Postgres role used by the application exists, has the right password, and has the expected permissions. It also prepares a related DBOS database owned by that serve role.

**Data flow**: It receives an administrator or owner DSN, derives the serve password, opens a Postgres connection, and sets a lock timeout. It checks whether the serve role already exists; if not, it creates it, and if it does, it refreshes the password. It grants the role relationship, sets idle-in-transaction timeouts for relevant roles, grants table and sequence access, creates the companion database if needed, and then closes the connection.

**Call relations**: This is one of the main bootstrap actions in the file. It calls serve_password for the credential, _grant_serve_role to apply permissions, and _ensure_database to create the companion database when absent. It uses asyncpg to talk to Postgres asynchronously, meaning the program can wait for database work without blocking the whole event loop.

*Call graph*: calls 3 internal fn (_ensure_database, _grant_serve_role, serve_password); 1 external calls (connect).


##### `bootstrap_policies`  (lines 67–94)

```
async def bootstrap_policies(dsn: str) -> None
```

**Purpose**: Checks every ordinary public table and makes sure the workspace row-level security policy is present and correct. This is the safety pass that enforces the shared-workspace boundary across the schema.

**Data flow**: It receives a database DSN, opens a connection, sets a short lock timeout, and fetches all public table names. It skips the Alembic migration table. For each other table, it first asks _conformant whether the table already has the right policy. If not, it opens a short transaction and asks _policy_for to enable and recreate the policy. If a lock timeout happens, it asks _lock_holders for a human-readable explanation and raises an error with that detail. It closes the connection at the end.

**Call relations**: This is the main policy bootstrap flow. It uses _conformant for the cheap “is this already right?” check, _policy_for for the actual database changes, and _lock_holders only when Postgres says a table is blocked by another session.

*Call graph*: calls 3 internal fn (_conformant, _lock_holders, _policy_for); 1 external calls (connect).


##### `_conformant`  (lines 97–125)

```
async def _conformant(connection: asyncpg.Connection, table: str) -> bool
```

**Purpose**: Checks whether one table already has the exact row-level security setup this system expects. It avoids unnecessary schema changes when the database is already correct.

**Data flow**: It receives an open Postgres connection and a table name. It reads Postgres catalog tables to see whether row-level security is enabled and whether the managed policy exists with the expected condition, command type, and role coverage. It asks _scope_column which column should be used for the workspace check. It returns true only when everything matches exactly; otherwise it returns false.

**Call relations**: bootstrap_policies calls this before doing any policy-changing work. If it returns true, that table is left alone. If it returns false, bootstrap_policies moves on to _policy_for so the table can be corrected.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (fetchrow).


##### `_lock_holders`  (lines 128–143)

```
async def _lock_holders(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Builds a readable report of database sessions that are currently holding locks on a table. This helps explain why policy setup could not proceed.

**Data flow**: It receives an open Postgres connection and a table name. It queries Postgres lock and activity views for other sessions holding granted locks on that table. It turns each matching session into a short text snippet with process id, role, state, transaction age, and the start of the SQL query. If nothing visible is found, it returns a fallback message.

**Call relations**: bootstrap_policies calls this only after a lock timeout while checking or changing a table. Its output is included in the raised error so an operator can find the blocking database session.

*Call graph*: called by 1 (bootstrap_policies); 1 external calls (fetch).


##### `_grant_serve_role`  (lines 146–159)

```
async def _grant_serve_role(connection: asyncpg.Connection) -> None
```

**Purpose**: Applies the table, sequence, and schema permissions needed by the limited serve role. It gives enough access for normal application work while also cleaning up default privileges that could grant access too broadly in the future.

**Data flow**: It receives an open Postgres connection. It revokes certain default future table and sequence privileges from the serve role, grants usage on the public schema, grants read/write permissions on all current public tables, and grants sequence usage. It does not return a value; the database permissions are the result.

**Call relations**: ensure_serve_role calls this after creating or updating the serve role. It is the permission-setting step in the role provisioning story.

*Call graph*: called by 1 (ensure_serve_role); 1 external calls (execute).


##### `_ensure_database`  (lines 162–165)

```
async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None
```

**Purpose**: Creates a named Postgres database if it does not already exist. In this file, it is used to prepare the companion DBOS database for the application.

**Data flow**: It receives an open Postgres connection, a database name, and an owner role. It checks Postgres system data to see whether the database name already exists. If it is absent, it issues a create database command with the requested owner. It returns nothing.

**Call relations**: ensure_serve_role calls this near the end of role setup, after it knows the serve role exists. That way, the companion database can be owned by the limited serve role.

*Call graph*: called by 1 (ensure_serve_role); 2 external calls (execute, fetchval).


##### `_policy_for`  (lines 168–175)

```
async def _policy_for(connection: asyncpg.Connection, table: str) -> None
```

**Purpose**: Enables and recreates the managed workspace row-level security policy for one table. This is the function that actually changes a table when its policy is missing or out of date.

**Data flow**: It receives an open Postgres connection and a table name. It asks _scope_column which column represents the workspace for that table. It builds a condition requiring that column to equal the current app.workspace_id database setting. Then it enables row-level security, drops the old managed policy if one exists, and creates a fresh policy using the condition for both reading and writing.

**Call relations**: bootstrap_policies calls this when _conformant says a table is not already correct. It relies on _scope_column so the workspace table itself uses id, while other workspace-scoped tables use workspace_id.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (execute).


##### `_scope_column`  (lines 178–192)

```
async def _scope_column(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Decides which column should be used to tie a table’s rows to a workspace. It also catches tables that are not safely workspace-scoped.

**Data flow**: It receives an open Postgres connection and a table name. If the table is the workspace table, it returns id because each workspace row is identified by its own id. Otherwise it checks whether the table has a workspace_id column. If that column exists, it returns workspace_id. If not, it raises an error because the table cannot be protected by the standard workspace policy.

**Call relations**: _conformant calls this when comparing the expected policy text, and _policy_for calls it when building the policy to apply. It is the shared rule that keeps policy checking and policy creation using the same workspace column.

*Call graph*: called by 2 (_conformant, _policy_for); 1 external calls (fetchval).


### `core/src/ufo/agent_scope.py`

`domain_logic` · `cross-cutting`

Some actions in this project belong to a specific agent, and agents also live inside a specific workspace. This file provides a small safety boundary around that idea. It is like giving a worker a temporary name badge at the door: while they are inside, other parts of the system can check the badge, but they cannot quietly swap it for someone else’s.

The central data is an AgentScope, which records two identifiers: the workspace and the agent. The file stores the current scope in a ContextVar, which is a Python tool for keeping per-task state. That matters in programs doing many things at once, because each task can have its own “current agent” without accidentally sharing it with another task.

The agent context manager starts an agent boundary. It reads the current workspace, pairs it with the chosen agent ID, and makes that pair available until the with block ends. If code tries to switch to a different agent while one is already bound, it fails immediately.

The agent_current function is the checker. It returns the current agent scope only if one has been bound and the workspace still matches. Without this file, code that depends on agent identity could run with no agent, the wrong agent, or an agent from the wrong workspace.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This function creates a temporary agent boundary for a block of code. Code inside the block can reliably know which agent is acting, and the function prevents silently changing to a different agent partway through.

**Data flow**: It takes an agent ID as input and reads the current workspace ID from the workspace context. It combines those into an AgentScope, checks whether an agent is already bound, and raises an error if the existing binding is for a different agent or workspace. If the binding is allowed, it stores the scope for the duration of the with block, yields it to the caller, and then restores the previous state when the block finishes.

**Call relations**: Higher-level code uses this when it is about to run work on behalf of an agent. During that work, other code can call agent_current to retrieve the same scope. This function depends on ws_current to make sure the agent is tied to the workspace that is active at the moment the boundary is opened.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function answers the question, “Which agent is currently bound?” It is meant for code that must not run unless it is clearly inside an agent boundary.

**Data flow**: It reads the stored current agent scope. If there is none, it raises AgentUnbound with a message telling the caller to wrap the work in an agent context. If there is a scope, it also reads the current workspace and compares it with the workspace saved in the agent scope. If they match, it returns the AgentScope; if they do not, it raises an error because the agent and workspace context no longer agree.

**Call relations**: Code that needs the active agent calls this after some outer flow has entered agent. It hands back the verified AgentScope so the caller can use the agent and workspace IDs safely. It also calls ws_current as a guardrail, making sure an agent binding is not accidentally reused across a different workspace.

*Call graph*: 2 external calls (__init__, ws_current).


### Disclosure and member tokens
Controls shared artifact downloads, audience disclosure labels, and signed member identity tokens for request authorization.

### `core/src/ufo/artifact_token.py`

`domain_logic` · `artifact sharing and download request handling`

This file is a small security gate for artifact downloads. An artifact is a stored file that the system is willing to share, such as a file produced by a tool or workflow. Instead of letting anyone request any stored blob directly, the system requires a signed token. You can think of the token like a stamped ticket: it names the file, says when the ticket stops working, and carries a tamper-proof stamp made with the deploy’s secret key.

The file defines a fixed artifact key prefix, `artifacts/`, so tokens cannot be used to fetch unrelated stored data. It also defines `ArtifactClaims`, the safe result of a verified token: the blob key to read, the filename to suggest to the downloader, and the expiry time.

`mint_artifact_token` packages the blob key, filename, and expiry time as JSON and signs that package. `verify_artifact_token` reverses the process: it checks the signature, reads the JSON, confirms the blob key stays inside the artifact namespace, and rejects expired tokens. If anything looks wrong, it raises `ArtifactTokenError` instead of returning partial or unsafe data. Without this file, artifact links would either be easy to forge or would need some other shared access-control mechanism before serving bytes.

#### Function details

##### `mint_artifact_token`  (lines 35–39)

```
def mint_artifact_token(secret: str, blob_key: str, filename: str, expires_at: int) -> str
```

**Purpose**: Creates a signed download token for one artifact file. Someone would use it when generating a share link, so the download route can later prove that the link was created by this deployment and has not been changed.

**Data flow**: It receives the shared secret, the stored blob key, the suggested download filename, and the expiry time. It first refuses to continue if the secret is missing. Then it turns the file details into a JSON byte string and signs those bytes with the secret. The result is a token string that can be placed in a download URL.

**Call relations**: This is the issuing side of the flow. A sharing feature, such as a file-sharing builtin, calls it when it wants to hand a user a temporary artifact link. The token it produces is later handed to `verify_artifact_token` by the artifact download route before any file bytes are served.

*Call graph*: 3 external calls (__init__, dumps, sign_token).


##### `verify_artifact_token`  (lines 42–63)

```
def verify_artifact_token(token: str, secret: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks whether a download token is valid and safe to use. It protects the artifact route from forged tokens, changed file names or keys, expired links, and attempts to use an artifact token to reach non-artifact storage.

**Data flow**: It receives a token, the shared secret, and the current time. It refuses to work if the secret is missing. It verifies the token’s signature, decodes the JSON payload, and turns the payload into `ArtifactClaims`. Then it checks that the blob key starts with `artifacts/`, does not contain a parent-directory escape such as `..`, and has not expired. If all checks pass, it returns the verified claims; if any check fails, it raises `ArtifactTokenError`.

**Call relations**: This is the receiving side of the flow. The artifact download route calls it when a request arrives with a token. If verification succeeds, the route can use the returned claims to find the artifact blob and suggest a filename. If verification fails, the route should serve no bytes.

*Call graph*: 6 external calls (__init__, __init__, timestamp, loads, PurePosixPath, verify_token).


### `core/src/ufo/audience.py`

`domain_logic` · `cross-cutting conversation handling`

An audience is a small string label, but it carries an important safety meaning: it says what information a conversation may read and where that information may be revealed. This file is like a badge checker at a doorway. It creates badges for shared workspace conversations, member-specific conversations, normal rooms, and rooms that include an outside organization.

The file uses a typed string called `Audience` so the code can distinguish these disclosure labels from ordinary text. It provides helper functions to build valid audience strings, such as a member audience or a room audience, and rejects unsafe room names that are empty or contain colons, because colons are used as separators inside the label format.

The most important guard is `parse_audience`. It does not merely accept any string. It rebuilds the expected audience and compares it with the input, which catches malformed or sneaky values. `audience_subjects` then turns an audience into the set of storage “subjects” it may read. A key rule is that foreign shared rooms only read their own room subject, not the broader workspace-shared subject, so internal memories are not recalled into a place where outsiders are present. `narrow_audience` supports moving from a broader audience to a safer, more specific one, while blocking unexpected disclosure changes.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: Creates the audience label for a conversation that is either shared with the whole workspace or tied to one specific member. It is used when the code needs a standard, safe spelling for that audience.

**Data flow**: It receives either a member UUID or `None`. If there is no member, it returns the shared audience label. If there is a member, it prefixes the member ID with the member subject prefix and returns that as an `Audience`.

**Call relations**: When `parse_audience` checks a member-style audience string, it calls this function to rebuild the official version of that member audience. That lets parsing confirm that the incoming text exactly matches the format this project expects.

*Call graph*: called by 1 (parse_audience).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates an audience label for a normal room on a given surface, such as a chat platform or other place where a conversation happens. It gives the rest of the system one consistent way to name room-level disclosure.

**Data flow**: It receives a surface name and a room name. It passes them to the shared room-building helper with the normal room prefix, and returns the resulting `Audience` label.

**Call relations**: This is the public helper for normal rooms. It delegates the actual validation and string construction to `_room_audience`, and `parse_audience` uses it to verify that a parsed normal room audience is written in the official form.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: Creates an audience label for a room that includes an outside organization. This distinction matters because foreign rooms get stricter read permissions than internal rooms.

**Data flow**: It receives a surface name and a room name. It passes them to the shared room-building helper with the foreign-room prefix, and returns the resulting `Audience` label.

**Call relations**: This is the public helper for externally shared rooms. It relies on `_room_audience` for validation and formatting, and `parse_audience` uses it when confirming that a foreign room audience string is valid.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: Builds the common string format for both normal and foreign room audiences, while enforcing simple rules that keep the format unambiguous. It is the shared quality check behind the two room audience helpers.

**Data flow**: It receives a prefix, a surface, and a room. It checks that the surface and room are not empty and do not contain colons. If either value is unsafe, it raises an error. Otherwise, it joins the pieces into one audience string and returns it.

**Call relations**: `room_audience` and `foreign_room_audience` both call this helper so their validation rules stay identical. The rest of the file reaches this logic through those two clearer, higher-level functions.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: Checks that a raw string is a valid audience label and returns it as an `Audience`. This is the main gatekeeper that prevents malformed disclosure labels from being trusted.

**Data flow**: It receives text. If the text is the shared audience, it accepts it. Otherwise, it splits the text at colons to identify whether it names a member, a normal room, or a foreign room. For member labels, it checks that the member part is a real UUID. For room labels, it checks the room structure. In each case, it rebuilds the expected audience using the official helper and compares it with the input. If anything does not match, it raises an error; if everything matches, it returns the audience.

**Call relations**: This function is used by `audience_member`, `audience_subjects`, and `narrow_audience` before they make decisions from an audience value. It calls `conversation_audience`, `room_audience`, and `foreign_room_audience` as trusted builders, and uses UUID parsing to validate member IDs.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `audience_member`  (lines 58–62)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: Extracts the member ID from a member-specific audience, if the audience is member-specific. It is useful when later code needs to know whether a conversation belongs to one particular person.

**Data flow**: It receives an `Audience`. First it validates the audience with `parse_audience`. If the parsed audience does not start with the member prefix, it returns `None`. If it does, it removes the prefix, turns the remaining text back into a UUID, and returns that UUID.

**Call relations**: This function depends on `parse_audience` so it never extracts an ID from an invalid label. It does not call other project helpers after that; it simply converts the already-validated member part into a UUID for callers that need the member identity.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 65–72)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: Decides which stored information subjects a conversation with this audience may read. This is where the file enforces the important safety rule that outside-facing rooms must not recall internal shared information.

**Data flow**: It receives an `Audience` and validates it with `parse_audience`. If the audience is a foreign room, it returns only that exact audience as the readable subject. For all other audiences, it returns both the workspace-shared subject and the audience’s own subject.

**Call relations**: This function is called by code that needs to know what memories or records are allowed for a conversation. It relies on `parse_audience` first, so its permission decision is based on a well-formed audience label.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 75–90)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: Combines a current audience and a requested audience while only allowing changes that keep disclosure the same or make it more specific. It prevents a conversation from silently jumping to an unrelated audience.

**Data flow**: It receives the current audience and the requested audience. It validates both. If they are the same, or if the request is merely the shared audience, it keeps the current audience. If the current audience is shared, it allows moving to the requested audience. If both are room-style audiences for the same surface and room, it chooses the stricter foreign version when either side is foreign. If none of those safe cases apply, it raises an error.

**Call relations**: This function calls `parse_audience` before comparing labels, so it only works with valid audience values. It is the decision point used when one part of the system asks to adjust a conversation’s audience, and it either returns the safe resulting audience or stops the change with an error.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/bearer.py`

`domain_logic` · `cross-cutting authentication during token minting and request handling`

This file is the shared rulebook for UFO “bearer” tokens. A bearer token is like a stamped wristband: whoever presents it can enter, but only if the stamp is genuine and not expired. Here, the stamp is an HMAC, which is a cryptographic signature made with a shared secret key. The token contains three claims: the workspace id, the member email, and an expiry time.

The important problem this solves is trust. A client may send a token saying “I am alice@example.com in workspace X,” but the system must not believe that text unless it can prove the token was minted by someone who knew the secret key. This file signs new tokens with `mint_token`, and checks incoming tokens with `verified_claims`, `verify_token`, and `workspace_claim`.

The file deliberately keeps the token self-contained. There is no database lookup to ask whether a token exists. Instead, the payload is encoded, signed, and later verified using the `UFO_TOKEN_SECRET` environment variable. If the signature is wrong, the token is malformed, the expiry has passed, or the workspace does not match, the checking functions return `None`. That makes failure safe: callers get no identity or workspace information unless the proof is valid.

#### Function details

##### `mint_token`  (lines 28–45)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a new signed token for a member email in a workspace. It is used by token issuers so every surface creates the same kind of token with the same security rules.

**Data flow**: It takes a secret key, a workspace id, an email address, a time-to-live, and optionally a current time. It trims and lowercases the email, calculates an expiry timestamp, turns the claims into compact JSON, encodes that JSON in URL-safe base64, signs the encoded body with HMAC-SHA256, and returns one string containing the body and signature separated by a dot. If the secret is empty, it stops with an error instead of minting an unsafe token.

**Call relations**: This is the issuing half of the token system. Other token-minting code calls on it when a hosted member or local developer needs a bearer token. The tokens it produces are later understood by `verified_claims`, so the creation and checking formats stay in sync.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 48–71)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a token is genuine and still valid, then returns the workspace and email it proves. If anything looks wrong, it returns `None` so callers do not accidentally trust bad data.

**Data flow**: It receives a token string and optionally a current timestamp. It reads the signing secret from the environment, splits the token into payload and signature, recomputes the expected signature, and compares the signatures in a timing-safe way. If the signature matches, it decodes the payload, parses the JSON, checks that the workspace, email, and expiry fields have the expected types, and rejects expired tokens. The successful output is a pair: workspace id string and email string.

**Call relations**: `verify_token` and `workspace_claim` both rely on this function as the first gate. It calls `_secret` to get the local signing key and `_b64url_decode` to read the token body before handing verified claims back to the higher-level checks.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 74–85)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Authenticates a token for one specific workspace and returns the member email if it belongs there. This protects a workspace from accepting a valid token that was minted for a different workspace.

**Data flow**: It takes a token, the workspace UUID that this process expects, and optionally a current timestamp. It asks `verified_claims` to prove the token first. If verification fails, or if the token’s workspace claim does not exactly match the expected workspace id, it returns `None`. If both checks pass, it returns the email in lowercase.

**Call relations**: This is the pinned-workspace check. It builds on `verified_claims` and adds the tenant boundary check that callers need when one deployment is meant to serve one workspace.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 88–99)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Finds the workspace UUID from a valid token. This is useful when a shared service receives requests for many workspaces and must decide the workspace from the signed token itself.

**Data flow**: It takes a token and optionally a current timestamp. It first asks `verified_claims` to prove the token signature and expiry. If that fails, it returns `None`. If the token is valid, it tries to turn the workspace claim string into a UUID object; if that string is not a valid UUID, it also returns `None`.

**Call relations**: This function is the shared-fleet path. Instead of comparing against one known workspace like `verify_token`, it trusts the workspace claim only after `verified_claims` has confirmed the token is authentic and unexpired.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 102–106)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the `UFO_TOKEN_SECRET` environment variable. Verification cannot be safe without this secret, so the function fails loudly if it is missing.

**Data flow**: It looks in the process environment for `UFO_TOKEN_SECRET`. If a value is present, it returns that string. If no value is set, it raises a runtime error explaining that the secret is required to verify member bearer tokens.

**Call relations**: `verified_claims` calls this before checking a token signature. This keeps secret loading in one small place, so the rest of the verification code does not duplicate environment-variable rules.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 109–110)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the token payload from URL-safe base64 back into bytes. It also restores any missing padding, because the token format strips padding to keep the string shorter and cleaner.

**Data flow**: It receives the encoded payload string from a token. It adds the right number of `=` padding characters, decodes the URL-safe base64 text, and returns the raw bytes that can then be parsed as JSON.

**Call relations**: `verified_claims` calls this after the token signature has matched. It is a small helper that turns the signed body back into readable payload data for the claim checks.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### Credential grants and storage
Defines visible credential requirements and safely stores, validates, and reveals secrets only through approved grant flows.

### `core/src/ufo/credential_kind.py`

`domain_logic` · `request handling`

Extensions can declare that they need a credential, such as an API key. This file turns those declarations into a readable object kind named "credential". Think of each credential slot like a labeled safe-deposit box: the label is visible, and the system can say whether the box is empty or full, but it never opens the box for display.

The important split is between the declaration and the stored secret. The declaration comes from an extension manifest: the slot name, description, extension name, and where the credential may be injected. The database row only exists when a value has been stored. If there is no row, the slot still appears, but as empty.

The main class, CredentialObjects, provides the object-style actions. Listing shows all declared slots with a filled/empty summary. Reading one slot returns its public declaration and timestamps if a stored value exists. Status returns whether it is filled, and may also report the resolved host for credentials that depend on a chosen account or host. Creating or updating is refused because filling or rotating a secret requires a private handoff through request_credentials. Deleting is allowed only for workspace admins, and it clears the stored value without removing the declared slot.

#### Function details

##### `CredentialObjects.list`  (lines 74–87)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows every credential slot declared by active extensions, whether or not a secret has been supplied. It gives a safe overview: names, descriptions, and filled-or-empty state, but never the secret itself.

**Data flow**: It receives the current tool context and a list query. It builds a name-to-slot lookup, asks the database which slots currently have stored credential rows, then creates one summary row per declared slot. The result is a paged object list shaped according to the query.

**Call relations**: When the object system needs to list credentials, it calls this method. This method leans on _named to understand the declared slots and _filled_slots to learn which ones have stored values, then hands the finished rows to object_page so the caller gets a normal paginated object response.

*Call graph*: calls 2 internal fn (_filled_slots, _named); 2 external calls (__init__, object_page).


##### `CredentialObjects.get`  (lines 89–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the public details for one credential slot. It explains what the slot is for and whether there are database timestamps for a stored value, while still hiding the credential value.

**Data flow**: It receives a slot name. First it checks the declared slots; if the name is unknown, it returns nothing. If the slot exists, it queries the credential table for the current workspace and that slot, reading only creation and update times. It then returns an ObjectDetail containing a CredentialSpec with the public declaration plus those timestamps, or null timestamps if the slot is empty.

**Call relations**: This is used when someone opens a single credential object. It uses _named to match the requested name to a declared slot, uses the current workspace to keep the database lookup scoped correctly, and wraps the result in ObjectDetail for the broader object API.

*Call graph*: calls 1 internal fn (_named); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects.status`  (lines 115–139)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live state of a credential slot in a compact form, mainly whether the slot is filled. If the slot has host-related rules and a credential store is available, it also reports the resolved host.

**Data flow**: It receives a slot name and an optional expected generation value. It looks up the declared slot; if it does not exist, it returns nothing. For a known slot, it checks whether a matching credential row exists in the current workspace. It returns a dictionary with filled set to true or false, and may add host information computed from the credential store and the slot's host rule.

**Call relations**: This fits into object status checks, where callers need a quick machine-readable answer rather than the full public spec. It uses _named for declaration lookup, queries the workspace database for stored state, and may hand off to credential_host to resolve host information without exposing the secret.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 141–150)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create, fill, or update a credential through the normal object apply path. This protects secrets by forcing users into the dedicated request_credentials flow, which is designed for private credential handoff.

**Data flow**: It receives the target name, the requested credential spec, the previous spec if any, and an optional expected generation value. It does not inspect or store the spec. It immediately raises a VerbNotSupported error with a message explaining the safer path to use.

**Call relations**: The object framework would call this for create or update-style operations. Instead of passing anything onward, this method stops the operation at the door and tells the caller that filling or rotating credentials belongs in request_credentials.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 152–168)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot, but does not remove the slot declaration. Only a workspace admin can do this, because deleting a credential can break an extension that depends on it.

**Data flow**: It receives the current tool context and the slot name. It first asks the context whether the speaker is a workspace admin. If not, it raises an AdminRequired error. If allowed, it finds the declared slot, then deletes any matching credential row for the current workspace from the credential table. Afterward, the slot remains visible but becomes empty.

**Call relations**: The object system calls this for delete requests on credential objects. This method uses the tool context for the admin check, _named to translate the object name to the declared slot, and the workspace database transaction to remove only the stored secret row for the current workspace.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 170–171)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Builds a convenient lookup table from credential slot names to their declarations. Other methods use it so they can quickly answer, "Is this a real declared slot, and what does it say?"

**Data flow**: It reads the slots stored on the CredentialObjects instance. It passes them to named_slots, which organizes them by name, and returns that dictionary to the caller.

**Call relations**: This is an internal helper used by list, get, status, and delete. It keeps all those methods from repeating the same slot-indexing step before they do their own work.

*Call graph*: called by 4 (delete, get, list, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 173–182)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the current workspace. It only returns slot names, not credential values.

**Data flow**: It opens a workspace database transaction and selects the slot column from all credential rows belonging to the current workspace. It gathers those slot names into a frozen set and returns it, giving callers a safe filled-slot checklist.

**Call relations**: The list method calls this when it needs to label every declared slot as filled or empty. This helper is the database-reading half of that job, while list combines the result with the manifest declarations.

*Call graph*: called by 1 (list); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/credentials.py`

`domain_logic` · `cross-cutting`

This file is the project’s safety box for “bring your own key” credentials. A workspace may need private values, like API tokens, but those values must not appear in chat, logs, or inside the sandbox as plain secrets. Instead, this file encrypts secrets before saving them, later decrypts them only when the proxy or another trusted part needs to inject them.

It also controls the private handoff where a member is asked to provide a credential. The system creates a sealed credential request, like a signed and locked envelope. The private surface can ask the member for the value, and when the value comes back, the code checks that the envelope was made by this deployment, has not expired, names the expected workspace, member, and slot, and is for the right purpose.

Some credentials are not typed by a member at all. They come from outside providers, such as an app installation or short-lived token. This file supports those too, by sealing provider state and by defining a common interface for sources that can mint secrets on demand.

Finally, it deals with credential “slots”: named places where extensions declare what secret they need, and sometimes which host that secret may be sent to. This keeps the sandbox, proxy, and stored database state in agreement about whether a credential exists and where it is safe to use.

#### Function details

##### `_slug`  (lines 28–29)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a human-written slot name into a simple, URL-like name made of lowercase letters, numbers, and dashes. This gives the system a stable plain name to use when referring to a credential slot.

**Data flow**: It receives raw text → lowercases it, replaces runs of non-letter-or-number characters with dashes, and trims extra dashes at the ends → returns the cleaned-up slot name.

**Call relations**: When slot declarations are turned into public names, named_slots asks this helper to make the first version of each name before checking whether any names collide.

*Call graph*: called by 1 (named_slots); 1 external calls (sub).


##### `named_slots`  (lines 32–47)

```
def named_slots(slots: 'tuple[DeclaredSlot, ...]') -> 'dict[str, DeclaredSlot]'
```

**Purpose**: Builds the official names used to address declared credential slots. If two slots would get the same simple name, it adds a short stable fingerprint so each one can still be addressed safely.

**Data flow**: It receives all declared slots → groups them by their simplified name → keeps unique names as-is, but adds a short hash based on extension and original name when there is a collision → returns a dictionary from official slot name to the slot declaration.

**Call relations**: This is used when the system needs one shared naming scheme for credential slots. It relies on _slug for readable names and uses hashing only when readability alone would be ambiguous.

*Call graph*: calls 1 internal fn (_slug); 1 external calls (sha256).


##### `seal_credential_request`  (lines 90–91)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: Locks a credential request state into an encrypted string that can be handed around without exposing or allowing changes to its contents. It is the basic sealing tool used for member prompts, provider authorization, and installation bindings.

**Data flow**: It receives an encryption helper and a structured request state → converts the state to JSON text and encrypts it → returns the encrypted text as a string.

**Call relations**: CredentialRequests.seal, CredentialRequests.authorize, and seal_installation call this when they need to create a sealed envelope that another part of the system can later verify.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 94–120)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: Opens and checks a sealed credential request. It rejects anything expired, tampered with, malformed, or sealed for a different purpose.

**Data flow**: It receives an encryption helper, encrypted text, an expected purpose, and optionally a time limit → decrypts the text, parses the saved state, and checks the purpose → returns the trusted state or raises a clear invalid-request error.

**Call relations**: Authorization and installation flows call this before trusting any sealed value. CredentialRequests.open_authorization, authorized_slot_workspace, and open_installation each add their own extra checks after this basic opening step.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 139–152)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: Creates a sealed request for a member to fill one or more credential slots by typing values privately. It refuses slots that are unknown or not allowed to be filled by a member.

**Data flow**: It receives a workspace, a member, and requested slot names → checks that every slot is declared and member-fillable → builds a request state and encrypts it → returns the sealed request string.

**Call relations**: This is used when the system asks a member for credentials. It hands the actual sealing work to seal_credential_request after enforcing the rules about which slots can be typed by people.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 154–167)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: Creates a sealed authorization request for a provider-backed credential, such as an OAuth-style flow. It records which slot is being authorized and the provider state needed to finish the flow.

**Data flow**: It receives a workspace, member, slot name, and provider payload → checks that the slot exists and the payload is not empty → seals those facts into encrypted text → returns the sealed authorization string.

**Call relations**: Provider authorization setup uses this before sending a member away to authorize with an outside service. It uses seal_credential_request so the callback can later prove it belongs to this exact request.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 169–183)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: Verifies a sealed provider authorization and extracts its provider payload. It makes sure the authorization belongs to the expected workspace, member, and slot.

**Data flow**: It receives sealed text plus the workspace, member, and slot expected by the caller → opens the seal → compares the saved claims with the expected ones → returns the provider payload or raises an error if anything does not match.

**Call relations**: This is used when a provider flow needs to continue from a sealed authorization. It builds on open_credential_request, then adds the checks that are specific to member-owned authorization.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 186–200)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: Stores a provider installation id as a sealed binding to a workspace and slot. This prevents someone from typing or guessing a raw installation id and using another organization’s provider installation.

**Data flow**: It receives an encryption helper, workspace, slot, and installation id → wraps them in a state marked as an installation binding → encrypts that state → returns the sealed binding string.

**Call relations**: Provider integration code uses this when an installation has been authorized and must be saved as a credential value. It uses the same sealing helper as requests, but marks the purpose differently.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 203–214)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: Verifies a sealed provider installation binding and returns the installation id. It rejects forged data, bindings for another workspace, bindings for another slot, and ordinary credential request seals.

**Data flow**: It receives an encryption helper, workspace, slot, and sealed binding → opens it with the installation-binding purpose and no expiry time → checks workspace, slot, and payload → returns the installation id.

**Call relations**: Provider token minting uses this when it needs to turn a stored installation binding into the real installation id. It depends on open_credential_request for safe decryption and purpose checking.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 220–226)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: Installs the process-wide credential request authority used by routes that cannot receive it through normal request context. This is especially useful for browser callbacks from outside providers.

**Data flow**: It receives either a CredentialRequests object or nothing → stores it in a module-level variable → later code can retrieve or use that shared authority.

**Call relations**: Startup code calls this once when credential support is configured. Callback-time helpers such as authorized_slot_workspace rely on this stored authority because those callbacks arrive outside the usual conversation flow.


##### `installed_credential_requests`  (lines 229–232)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: Returns the process-wide credential request authority, or clearly fails if credential support was not configured. This gives callers one place to ask for the installed credential sealing setup.

**Data flow**: It reads the module-level stored authority → if present, returns it → if absent, raises an error saying no credential key is configured.

**Call relations**: Code that needs the global credential request setup can call this instead of touching the global variable directly. It pairs with install_credential_requests, which sets that value during startup.


##### `authorized_slot_workspace`  (lines 235–251)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: Finds which workspace a provider authorization callback belongs to, using only the sealed authorization returned by the browser. It returns nothing if the seal is invalid or does not match the expected slot and provider payload.

**Data flow**: It receives sealed text, an expected slot, and expected provider payload → opens the seal using the installed credential authority → checks the slot and payload → returns the workspace id, or None if the check fails.

**Call relations**: Provider callback routes use this when they have no normal session or turn context. It calls open_credential_request through the globally installed authority and deliberately gives callers only a workspace when the seal is trustworthy.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 258–280)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: Saves a plaintext credential value for a workspace and slot after encrypting it. It updates an existing slot if present, or creates a new row if this is the first value.

**Data flow**: It receives a workspace id, slot name, and plaintext secret → rejects an empty value → encrypts the secret → opens a database transaction → updates the existing credential row or inserts a new one → returns nothing after the database is changed.

**Call relations**: Credential fulfillment and provider binding code use this to persist a credential safely. It is the write side of CredentialStore, while CredentialStore.get is the read side used by later secret resolution.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 282–294)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Retrieves and decrypts the stored secret for one workspace and slot. If no value has been saved, it raises a specific “slot unset” error.

**Data flow**: It receives a workspace id and slot name → reads the encrypted value from the credential table → if no row exists, raises CredentialSlotUnset → otherwise decrypts the stored bytes and returns the plaintext string.

**Call relations**: Many higher-level flows depend on this as the basic read operation: slot_secret, slot_is_set, credential_host, and GitHub app token code use it to learn what a workspace has stored.

*Call graph*: called by 5 (credential_host, slot_is_set, slot_secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 296–327)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if it still has the expected old value. This protects refresh flows from overwriting a newer credential when two refreshes happen at the same time.

**Data flow**: It receives a workspace, slot, expected current plaintext, and new plaintext → rejects an empty new value → reads and decrypts the current stored value → if it is missing or different, returns False → otherwise writes the encrypted new value only if the database row is unchanged → returns whether the replacement succeeded.

**Call relations**: OAuth-style token refresh code can use this after getting a new token from a provider. It complements put by supporting safe conditional replacement rather than unconditional saving.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 351–356)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a host-choice declaration is internally consistent. In particular, the default host must be one of the allowed hosts.

**Data flow**: It reads the newly created HostChoice fields → verifies that the default value appears in the allowed host list → either finishes construction silently or raises an error explaining the bad declaration.

**Call relations**: This runs automatically when a HostChoice is created. It catches extension or configuration mistakes early, before credential_host uses the choice to decide where a secret may be sent.


##### `HostChoice.resolve`  (lines 358–363)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: Turns a stored host selection into one of the exact declared host strings. It accepts different letter casing from the stored value but never returns a host that was not declared.

**Data flow**: It receives a selected value → trims surrounding spaces and lowercases it for comparison → searches the allowed host list case-insensitively → returns the canonical declared host, or None if there is no match.

**Call relations**: credential_host uses this when a workspace has stored a host selection. The important handoff is that free text never becomes an outbound host; only a declared host can come out.


##### `CredentialSource.secret`  (lines 371–371)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: Defines the method that provider-backed credential sources must implement to produce a secret for a workspace. A source may mint a short-lived token instead of reading a member-typed value directly.

**Data flow**: An implementation receives a workspace id and credential store → uses whatever provider-specific binding or stored data it needs → returns a secret string, or None if there is nothing to mint.

**Call relations**: slot_secret calls this when a credential slot has a provider source. This protocol method lets different providers plug into the same credential-resolution path.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 373–381)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: Defines the method that provider-backed credential sources must implement to say whether a workspace is connected, without actually minting a secret. This avoids expensive or risky provider calls during simple “is this configured?” checks.

**Data flow**: An implementation receives a workspace id and credential store → checks local binding state or provider-specific stored data → returns True or False, or raises if the binding exists but cannot be used.

**Call relations**: slot_is_set calls this instead of secret when it only needs to know whether a slot is available. This keeps sandbox setup from contacting providers just to decide whether to configure a client.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 384–399)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Answers the main question: what secret should this slot provide for this workspace? It prefers a provider-minted secret when a source exists, and otherwise falls back to the stored member value.

**Data flow**: It receives the slot name, optional provider source, workspace id, and credential store → asks the source for a minted secret if there is one → if that gives nothing, tries to read the stored secret → returns the secret string or None if the slot is unset.

**Call relations**: Proxy rules, sandbox exports, and other credential consumers should resolve through this shared path. It calls CredentialSource.secret for provider-backed slots and CredentialStore.get for stored values so all roles see the same answer.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 402–420)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a slot would provide a secret without actually producing that secret. This is useful during sandbox setup, where the system needs to know what to configure but should not mint short-lived provider tokens yet.

**Data flow**: It receives the slot name, optional provider source, workspace id, and store → asks the source whether it is bound when present → if not bound by a source, checks whether a stored value exists → returns True if either path is available, otherwise False.

**Call relations**: Sandbox-opening code can use this lightweight availability check before any network use. It mirrors slot_secret’s decision path closely, using CredentialSource.bound and CredentialStore.get so the “available” answer matches the later “secret” answer.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 423–440)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: Decides which provider host a credential is allowed to be used with for a workspace. It returns a fixed declared host, or resolves a workspace’s saved choice from a closed list of allowed hosts.

**Data flow**: It receives the credential store, workspace id, and either a plain host string or HostChoice → if it is a string, returns it directly → if it is a HostChoice, reads the workspace’s selected value from the store, falls back to the default if unset, and resolves it to an allowed host → returns the chosen host or None if the stored choice is invalid.

**Call relations**: The proxy and sandbox export logic use this so they agree on the same destination host for a credential. It calls CredentialStore.get for saved choices and relies on HostChoice resolution rules to prevent arbitrary stored text from becoming a trusted network target.

*Call graph*: calls 1 internal fn (get).


### Operator sessions
Provides shared login and workspace-scoping checks for operator-only web tools.

### `core/src/ufo/ext/operator.py`

`domain_logic` · `request handling`

This file solves a security and convenience problem for internal operator web pages. Operators need to sign in once and then move between several tools, but their long-lived credential must not appear in URLs, where it could leak through browser history, bookmarks, or server logs. The file therefore looks for the token only in safer places: an Authorization header, a shared session cookie, or the body of the one form POST that opens a session.

The main flow is like a front desk for restricted rooms. First, `operator_bearer` finds the visitor’s pass. Then `resolve_operator_workspace` checks whether the pass is real, reads the email on it, and only allows access if the email belongs to the operator email domain. Once that gate passes, the request can be scoped to a workspace. With no `?ws=` value, it uses the workspace already written into the token. With `?ws=`, an operator can choose another workspace by raw UUID or by customer domain name, which is turned into a stable UUID.

Finally, `bind_operator_session` turns a submitted token into a browser cookie and redirects back to the page. The cookie is marked for HTTP-only session use, so normal page scripts do not need to touch it.

#### Function details

##### `operator_bearer`  (lines 25–38)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: This function finds the bearer token for an operator request without ever accepting it from the URL query string. It lets callers treat headers, cookies, and the initial login form as one common source of identity.

**Data flow**: It receives a web request. It first reads the Authorization header and returns the token if it is written as a Bearer token. If not, it checks the shared operator cookie. If that is also missing and the request is a POST, it reads the submitted form body and looks for the token field. The output is the cleaned token text, or an empty string if none is found.

**Call relations**: When `resolve_operator_workspace` needs to know who is making the request, it calls `operator_bearer` first. If the request is the initial POST that opens a session, this function asks the request object to parse the form so it can read the submitted token.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 41–65)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function decides whether an operator request is allowed and, if so, which workspace it should apply to. It is the gate that prevents non-operator tokens from using operator-only tools.

**Data flow**: It receives the web request and the surface authentication object, though this function does not use the auth object directly. It asks `operator_bearer` for a token, verifies the token’s claims, and reads the workspace and email from those claims. It checks that the email domain matches the configured operator domain. If no workspace override is present in `?ws=`, it returns the workspace UUID from the token. If `?ws=` is present, it returns that value as a UUID when possible, or turns the provided domain name into a stable UUID. If any required check fails, it returns `None`, meaning the request should be rejected.

**Call relations**: This function is the resolver used by operator-facing surfaces when they need to attach a request to a workspace. It hands the token lookup to `operator_bearer`, hands token checking to `verified_claims`, uses `email_domain` to enforce the operator-domain rule, and uses UUID helpers to produce the final workspace identifier.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 68–80)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens the browser session after an operator submits a token. It stores the token in the shared operator cookie and redirects the browser back to the requested page.

**Data flow**: It receives the current surface context and request. It reads the form body and looks for the required token field. If the field is missing or blank, it returns a JSON error response with a bad-request status. If the token is present, it builds a redirect response to the same URL, sets the shared operator session cookie on that response, and returns it to the browser.

**Call relations**: This function is used at the moment an operator session is being established. It relies on the request object to read the submitted form, uses `JSONResponse` for the error case, uses `RedirectResponse` for the success case, and calls `set_session_cookie` so later operator pages can authenticate through the same cookie instead of asking for the token again.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### Sandbox ingress and signing
Signs and verifies sandbox host labels, ingress permissions, surface routing labels, and the underlying tamper-resistant token payloads.

### `core/src/ufo/sandbox/ingress_host.py`

`domain_logic` · `URL creation and request handling`

A hosted sandbox site needs its own browser origin, meaning its own hostname, so its cookies, storage, redirects, and root-based assets stay separate from every other site. This file turns a conversation ID and a sandbox port into one short DNS label that can be placed in a hostname. Think of it like writing an apartment number into a mailing address, then adding a tamper-evident sticker so the front desk can reject fake addresses quickly.

The label contains three pieces: the conversation UUID, the port number, and a short HMAC signature. An HMAC is a cryptographic stamp made with a shared secret; here it proves that this deployment minted the label. The label is not the main permission check. Later requests still need a valid token or session cookie. Its job is to stop arbitrary guessed hostnames from causing the server to read conversation data or dial a sandbox.

The file also enforces one canonical spelling of each label. Base32 encoding can leave unused bits, which means several different-looking labels could decode to the same bytes. Browsers would treat those as different sites with different cookie jars, so parsing re-encodes the bytes and rejects any non-canonical spelling.

#### Function details

##### `site_label`  (lines 52–57)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: Builds the DNS label for a specific conversation and sandbox port. Code uses it when it needs the hostname where that sandboxed site should be served.

**Data flow**: It takes a conversation UUID and a port number. It first checks that the port fits in the normal 1 to 65535 TCP/UDP port range, then joins the UUID bytes and the two-byte port into an address. It signs that address with the deployment secret, appends the signature, and returns a lowercase base32 label string.

**Call relations**: This is the minting side of the flow. It calls _signature to add the tamper-evident stamp, then calls _encode to turn the raw bytes into a DNS-friendly label. parse_site_label later performs the matching checking work when a label comes back in a request.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 60–72)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: Reads a DNS label back into the conversation UUID and port it claims to name, but only if the label is well formed, canonically written, and signed by this deployment. It raises SiteLabelError when the label should not be trusted.

**Data flow**: It takes the label text from a hostname. It base32-decodes it, allowing DNS-style case differences, then re-encodes the bytes and compares that result with the lowercase label to make sure this is the single accepted spelling. It splits the decoded bytes into the address and signature, recomputes the expected signature, compares them safely, and finally returns the UUID and port if everything matches.

**Call relations**: This is the checking side of the flow, used when a request arrives with a sandbox hostname. It relies on _encode to enforce canonical spelling and _signature to verify the cryptographic stamp. If any step fails, it stops the flow with SiteLabelError before later code reads conversation data or contacts the sandbox.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 75–76)

```
def _encode(raw: bytes) -> str
```

**Purpose**: Turns raw label bytes into the exact DNS-safe text form this file accepts. It keeps labels lowercase and removes base32 padding characters so the result is short and hostname-friendly.

**Data flow**: It takes bytes, base32-encodes them, converts the encoded bytes into text, strips trailing equals-sign padding, lowercases the result, and returns that string. It does not change any outside state.

**Call relations**: site_label uses this after building and signing a raw address, so minted labels all have one standard spelling. parse_site_label uses the same routine as a mirror check: if decoding and re-encoding does not reproduce the supplied label, the label is rejected.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 79–81)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: Creates the short cryptographic signature attached to each sandbox site label. This lets the server quickly tell whether the address bytes were minted with this deployment's secret.

**Data flow**: It takes the raw address bytes, reads the ingress secret for this deployment, and computes an HMAC using SHA-256 over a fixed label kind plus the address. It returns only the first four bytes of that digest, which is enough here because the real access control happens later through tokens or cookies.

**Call relations**: site_label calls this when creating a label, and parse_site_label calls it again when checking one. Because both sides use the same secret and input bytes, a genuine label produces the same signature, while a guessed or altered label should not.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/sandbox/ingress_token.py`

`domain_logic` · `link creation and sandbox ingress request handling`

This file protects sandbox web access with short-lived signed tokens. A signed token is like a tamper-evident wristband: it carries a few facts, and the signature proves those facts were issued by this deployment rather than edited by a visitor.

The token says which workspace and conversation it belongs to, which sandbox port it opens, when it expires, and what kind of hop it is for. There are two separate token kinds. A "view" token is used in the URL that opens the sandbox view. A "session" token is later used as the browser cookie for that opened origin. Keeping these kinds separate matters because it stops a cookie from being reused as if it were a fresh view link, and stops a view link from acting like a session cookie.

The file also enforces basic safety checks after the signature is verified. It rejects malformed data, expired tokens, and ports outside the normal TCP port range. Both minting and verifying use the same deployment secret from the environment, so all parts of the system agree on what counts as authentic. If that secret is missing, the code fails loudly instead of silently allowing broken ingress behavior.

#### Function details

##### `mint_ingress_token`  (lines 50–62)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: Creates a signed token from trusted ingress claims. Callers use it when they need to give a browser temporary permission to reach a specific sandbox port for a specific hop, such as the initial view link or the later session cookie.

**Data flow**: It receives an IngressClaims object and a token kind. It turns the workspace ID, conversation ID, port, expiry time, and kind into JSON text, reads the deployment secret, signs that JSON, and returns the signed token string. It does not change the claims; it packages them into a form that can later be checked for tampering.

**Call relations**: This is the token-making side of the flow. When some other part of the system prepares sandbox access, it calls this function to produce the token that will travel in a URL or cookie. Inside, it asks ingress_secret for the shared secret and then hands the prepared payload to the token-signing helper.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 65–87)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: Checks whether a token is authentic, unexpired, well-formed, and meant for the exact ingress hop being served. If everything is valid, it returns the claims that say what access is allowed.

**Data flow**: It receives a token string, the current time, and the expected token kind. It reads the deployment secret, verifies the token signature, parses the JSON payload, checks that the kind matches, converts the workspace and conversation values into UUIDs, converts the port and expiry into numbers, rejects invalid ports, and rejects tokens whose expiry time has passed. The output is an IngressClaims object; invalid input becomes an IngressTokenError.

**Call relations**: This is the checking side of the flow, used when ingress receives a token from a browser. It mirrors mint_ingress_token by using the same secret and expected payload fields. It relies on ingress_secret for the shared secret, the token verification helper for tamper detection, and then performs the ingress-specific checks before allowing the caller to continue.

*Call graph*: calls 1 internal fn (ingress_secret); 6 external calls (__init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 90–97)

```
def ingress_secret() -> str
```

**Purpose**: Fetches the shared deployment secret used to sign and verify ingress tokens. It exists so both token creation and token checking use the same source of truth.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the value is present, it returns that string. If it is missing or empty, it raises a RuntimeError so the deployment fails clearly instead of accepting requests with no usable token secret.

**Call relations**: Both mint_ingress_token and verify_ingress_token call this before doing cryptographic signing or checking. It is the small shared step that ties the token system to deployment configuration and makes missing configuration visible early.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### `core/src/ufo/surface_token.py`

`domain_logic` · `request routing and link generation`

Some routes in this system can be reached just by following a link. At that point there may be no cookie or logged-in session to say which workspace the request belongs to. This file solves that early identification problem by putting the needed claims into a signed token that can travel in a URL.

The token is signed with a shared secret from the environment. A signature here means a cryptographic seal, made with HMAC, that proves the contents were created by this deployment and were not changed afterward. The code never exposes the secret to callers. A surface asks to mint a token with some string claims, and later asks to verify a token and get those claims back.

A key safety rule is that every token includes the name of the surface that minted it. When verifying, the requested surface name must match the one inside the token. This stops a token made for one surface from being reused as an address for another.

The file is careful about what it accepts. The special claim name "surface" is reserved. Claims must be strings, because the payload is meant to cross a URL. Invalid signatures, malformed JSON, wrong surface names, or non-string claims all produce no claims rather than partially trusted data. These tokens do not expire and do not grant access; normal route checks still decide whether the request is allowed.

#### Function details

##### `mint_surface_token`  (lines 24–35)

```
def mint_surface_token(surface: str, payload: Mapping[str, str]) -> str
```

**Purpose**: Creates a signed surface token from a surface name and a set of string claims. It is used when the system needs to put a trustworthy, tamper-proof address into a URL.

**Data flow**: It receives the surface name and a mapping of claim names to string values. It first rejects an empty surface name and rejects any payload that tries to use the reserved "surface" claim. Then it adds the real surface name to the payload, turns the payload into compact JSON text, reads the signing secret, and returns a signed token string. The input mapping is not changed.

**Call relations**: When a surface needs to hand out an address-like token, this function builds the body and asks _secret for the deployment secret. It then hands the prepared bytes to the token-signing helper, which produces the final token that can be placed in a link.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (dumps, sign_token).


##### `verify_surface_token`  (lines 38–51)

```
def verify_surface_token(surface: str, token: str) -> dict[str, str] | None
```

**Purpose**: Checks whether a token is valid for a specific surface and, if so, returns the claims inside it. It is used before trusting URL-provided routing information.

**Data flow**: It receives the expected surface name and a token string. It reads the same deployment secret, asks the token verifier to check the signature, and parses the verified bytes as JSON. If the signature is bad, the JSON is malformed, the data is not a dictionary, the embedded surface name does not match, or any remaining claim is not a string-to-string pair, it returns None. If everything is valid, it returns the claims with the internal "surface" marker removed.

**Call relations**: When a request arrives with a surface token, this function is the gatekeeper that turns it back into usable claims. It relies on _secret to get the signing key and on the token verification helper to prove the token was not forged or altered before any claim is used.

*Call graph*: calls 1 internal fn (_secret); 2 external calls (loads, verify_token).


##### `_secret`  (lines 54–58)

```
def _secret() -> str
```

**Purpose**: Fetches the shared token secret from the process environment. This keeps signing and verification tied to the deployment’s configured secret without passing that secret around in normal code.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If a value is present, it returns that string. If the value is missing or empty, it raises an error, because tokens cannot be safely signed or verified without the secret.

**Call relations**: Both mint_surface_token and verify_surface_token call this helper at the moment they need the secret. That makes it the single local place that enforces the rule that surface tokens only work when the deployment has been properly configured.

*Call graph*: called by 2 (mint_surface_token, verify_surface_token).


### `core/src/ufo/token_signing.py`

`util` · `cross-cutting`

This file is a small security helper for making “opaque” tokens: strings whose contents are not meant to be interpreted by the receiver except through this code. The payload is not encrypted, but it is encoded into URL-safe base64 text, which means it can safely travel in links, headers, or other places where punctuation can cause trouble.

The important protection is the signature. When a token is made, the file uses HMAC, a standard way to create a fingerprint from a secret key and a message. Think of it like sealing an envelope with a wax stamp that only someone with the same seal can recreate. If the token body changes, the stamp no longer matches.

A token has two parts separated by a dot: the encoded payload and the signature. `sign_token` builds that string. `verify_token` splits it apart, recalculates the expected signature with the shared secret, and compares the two safely. If the token is missing pieces, has the wrong signature, or contains unreadable base64 data, it raises `SignedTokenError` instead of returning bad data.

Without this file, other parts of the system would either have to trust raw client-provided strings, which is unsafe, or repeat this delicate signing logic in multiple places.

#### Function details

##### `sign_token`  (lines 12–16)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: This function turns raw payload bytes into a signed text token. Someone would use it when they need to hand data to an outside party and later confirm that the data came back unchanged.

**Data flow**: It takes a secret key and a payload as bytes. It first converts the payload into URL-safe base64 text, then creates an HMAC-SHA256 signature from that text using the secret. It returns one string made of the encoded body, a dot, and the encoded signature.

**Call relations**: This is the token-making half of the pair. It relies on the standard base64 tool to make safe text and on the standard HMAC tool to create the tamper-detection stamp. Later, `verify_token` is expected to read tokens produced by this function.

*Call graph*: 2 external calls (urlsafe_b64encode, new).


##### `verify_token`  (lines 19–30)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: This function checks a signed token and returns the original payload bytes only if the token is well formed and has not been changed. It protects callers from accepting forged or damaged tokens.

**Data flow**: It takes a token string and the same secret key that was used to sign it. It splits the token into body and signature, rebuilds the expected signature from the body, and compares the two using a safe comparison method. If everything matches, it decodes the body back into the original bytes. If anything is wrong, it raises `SignedTokenError` instead of returning a payload.

**Call relations**: This is the checking half of the token flow. It uses the same base64 and HMAC tools as `sign_token`, then uses `hmac.compare_digest` so the signature comparison does not leak useful timing clues. When validation fails, it hands the problem back to the caller as a clear `SignedTokenError`.

*Call graph*: 5 external calls (__init__, b64decode, urlsafe_b64encode, compare_digest, new).


### Connector secret consumption
Lets direct source connectors retrieve member-supplied API keys for provider sync without exposing the secret to agents or sandboxes.

### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `feed-sync authentication during job execution`

Some data providers cannot use the normal account-broker system, or a deployment may prefer to keep the provider key under its own control. In those cases, a member adds an API key directly. This file is the small bridge that turns that stored key into the credential a sync job can use.

The key idea is separation: the sync job runs on the host side, where it is allowed to read protected credentials. The secret is fetched from the credential store using the provider name as the slot name. It is then wrapped as a bearer credential, meaning it will be used like an HTTP “Bearer” token when calling the provider’s API.

The account value is only a routing marker in this design. It sends the connector to this direct backend, but it does not contain the secret itself. That is important because the actual key stays in the credential store and is only decrypted inside the trusted job process. It is not sent to the sandbox, not shown to the agent, and should not be logged. Think of this file like a locked key cabinet clerk: it hands the right key to the trusted worker, but never leaves copies lying around.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This method fetches the member-provided API key for a provider and returns it in the standard credential shape used by the rest of the sync system. It is used when a source is routed through the direct-auth path.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The provider name is used to read the matching secret from the workspace-scoped credential store. The secret that comes back is placed into a new Credential object as a bearer token, and that Credential is returned. The account handle is not used as secret data here; it only represents that this source was routed to direct authentication.

**Call relations**: When a feed-sync run needs credentials for a directly configured provider, it calls this method on DirectAuthProxy. The method asks the CredentialAccess object for the provider’s stored secret, then hands that secret into Credential.__init__ so the rest of the HTTP sync code can authenticate in the usual way.

*Call graph*: 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-configuration` — The final startup settings that decide how the service, security, sandbox, proxy, and enabled packs should behave.
- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-identity-auth-state` — The current proof of who is calling, such as member identity, cookies, bearer tokens, operator sessions, and signed access tokens.
- `reg-onboarding-claims-invites` — The temporary signup claims, email verification codes, invite records, and hosted gateway tokens used to admit new users.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-access-grants` — The saved approvals that say which workspace, member, agent, account, source, or conversation is allowed to use a protected resource.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-source-page-sync-state` — The source records, page bodies, cursors, revisions, deletion markers, and replay feed used to keep external content synchronized.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-egress-network-state` — The controlled network exit state, including proxy configuration, certificates, allowed destinations, metering, and last-moment credential injection.
- `reg-artifact-blob-store` — The shared file and blob store for generated artifacts, copied outputs, download records, and signed access links.
- `reg-hosted-site-state` — The durable records for generated hosted sites, including names, ports, owners, conversations, sharing, and viewing permissions.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-prompt-governance-state` — The prompt templates, rendered fingerprints, proposals, evaluations, approvals, and replacement decisions used to change agent behavior safely.
- `reg-security-audit-log` — The durable audit records for sensitive access and administrative/security-relevant actions, distinct from operational traces.
- `reg-hosted-domain-routing-state` — The hosted-control mapping from verified company domains and invite policy to the shared workspace a signer should join.
- `reg-member-seat-state` — The durable workspace membership and seat-assignment state used to decide who belongs, who is an admin, and whether a member may admit or run work.
