# Workspace identity, member seats, and execution context  `stage-18.1`

This stage is shared behind-the-scenes support for almost everything the system does. It answers the basic questions: “Which workspace are we acting in?”, “Who is the member or operator?”, “Which agent is allowed to act?”, and “What data may this code touch?”

The workspace context is the main anchor. It records the current workspace for a request or background job, so secrets, billing, and database work do not get mixed between customers. The database safety layer then uses that workspace to enforce row-level security, meaning Postgres only exposes rows for the selected workspace. Bearer tokens act like signed ID cards: they prove a member’s identity and workspace without keeping a server-side session. Operator tools use a shared helper to read those tokens, verify them, and decide what an operator may inspect. Agent scope adds another guardrail by making sure agent-owned actions run only as the correct agent in the correct workspace. Seats decide which members the agent may serve, including creating members, granting or removing access, and ensuring at least one admin keeps a seat.

## Files in this stage

### Database isolation boundary
Postgres row-level safety is established so every database operation is constrained to the selected workspace.

### `control/src/ufo_control/rls.py`

`domain_logic` · `startup / database bootstrap`

This file is the database gatekeeper for shared workspace data. The project stores many workspaces in the same Postgres database, so it needs a hard rule that one workspace cannot accidentally read or write another workspace’s rows. Postgres provides this through row level security, often called RLS, which means the database itself filters rows before the application can touch them.

The file does two main jobs. First, it prepares a limited database login role, `ufo_serve`, with a password derived from a secret seed. This role is what the serving application should use instead of the more powerful owner role. Second, it walks through every public table and makes sure each one has the expected workspace policy. The policy says, in effect: “only rows whose workspace id matches the workspace id set on this database session are allowed.” For the workspace table itself, the `id` column is used; for other tables, the `workspace_id` column is required.

The code is careful not to hang forever if another database task is holding a table lock. It sets a lock timeout, and if a table cannot be checked or updated in time, it reports which database sessions are blocking it. Without this file, the shared database could lose one of its most important safety rails: workspace isolation enforced by the database itself.

#### Function details

##### `owner_dsn`  (lines 22–26)

```
def owner_dsn() -> str
```

**Purpose**: Reads the database connection string for the powerful Postgres owner account. This is needed when setup code must create roles, grant permissions, or install security policies.

**Data flow**: It looks in the process environment for `UFO_CONTROL_POSTGRES_OWNER_DSN`. If the value is present, it returns that connection string. If it is missing, it stops immediately with an error so the program does not continue without the authority needed to set up RLS.

**Call relations**: No caller is shown in the supplied graph, but this is meant to be used by startup or administration code before database security setup begins. It supplies the owner connection information that other setup steps need.


##### `serve_password`  (lines 29–33)

```
def serve_password() -> str
```

**Purpose**: Creates the password for the limited `ufo_serve` database role from a secret seed. This lets deployments recreate the same password without storing it directly in this file.

**Data flow**: It reads `UFO_CONTROL_PG_ROLE_SEED` from the environment. It combines that seed with the fixed role name, hashes the result with SHA-256, and returns the hexadecimal password text. If the seed is missing, it raises an error because the serving role cannot be safely created or used.

**Call relations**: When `ensure_serve_role` prepares the database role, it calls this function to know what password to set. When `serve_dsn` builds the application connection string, it calls the same function so the connection string matches the role that was created.

*Call graph*: called by 2 (ensure_serve_role, serve_dsn); 1 external calls (sha256).


##### `serve_dsn`  (lines 36–37)

```
def serve_dsn(postgres_host: str, app_database: str) -> str
```

**Purpose**: Builds the database connection string that the serving application should use. It points to the limited `ufo_serve` role instead of the owner account.

**Data flow**: It receives a Postgres host name and an application database name. It asks `serve_password` for the derived role password, then combines the role name, password, host, and database into a `postgresql+asyncpg` connection string. The result is a ready-to-use DSN, which is a database address with login details.

**Call relations**: This function sits after password derivation and before normal application database access. It hands callers a connection string for the restricted role, so later database work runs under the RLS-protected identity.

*Call graph*: calls 1 internal fn (serve_password).


##### `ensure_serve_role`  (lines 40–64)

```
async def ensure_serve_role(admin_dsn: str) -> None
```

**Purpose**: Creates or refreshes the limited Postgres role used by the serving application. It also gives that role the basic table and sequence permissions it needs, while relying on RLS to limit which rows it may access.

**Data flow**: It receives an admin database connection string, derives the serving role password, and opens a Postgres connection. It sets a lock timeout, creates or updates the `ufo_serve` role, grants role-switching rights to the owner role, sets idle transaction timeouts, applies schema/table/sequence grants, and creates a companion database named after the current database with `_dbos` added if it does not already exist. It closes the connection when finished.

**Call relations**: This is a top-level setup step for role provisioning. It calls `serve_password` for the role secret, `_grant_serve_role` to apply permissions, and `_ensure_database` to create the related database if needed.

*Call graph*: calls 3 internal fn (_ensure_database, _grant_serve_role, serve_password); 1 external calls (connect).


##### `bootstrap_policies`  (lines 67–94)

```
async def bootstrap_policies(dsn: str) -> None
```

**Purpose**: Checks every public table and installs the expected workspace RLS policy where needed. This is the main routine that makes the database enforce workspace separation.

**Data flow**: It receives a database connection string, connects to Postgres, sets a short lock timeout, and lists public tables. It skips the migration bookkeeping table, then checks each remaining table. If a table already has the correct policy, it leaves it alone. If not, it opens a short transaction and recreates the policy. If a table is blocked by another database lock, it gathers information about the blocker and raises a clear error instead of waiting indefinitely.

**Call relations**: This is the policy bootstrap driver. For each table it asks `_conformant` whether the table already matches the expected shape. If not, it hands the table to `_policy_for`. If locking fails, it asks `_lock_holders` for a human-readable explanation of who is blocking progress.

*Call graph*: calls 3 internal fn (_conformant, _lock_holders, _policy_for); 1 external calls (connect).


##### `_conformant`  (lines 97–125)

```
async def _conformant(connection: asyncpg.Connection, table: str) -> bool
```

**Purpose**: Answers one question: does this table already have the exact RLS setup this project expects? It helps avoid unnecessary database changes when a table is already safe.

**Data flow**: It receives an open database connection and a table name. It reads Postgres system catalogs, which are Postgres’s internal records about tables and policies. It checks that row level security is enabled, that the named policy exists, and that the policy expression, write check, role, command, and permissive mode match the expected values. It asks `_scope_column` which column should identify the workspace, then returns `true` only if everything matches.

**Call relations**: During `bootstrap_policies`, this function is called before any policy-changing work. If it says the table is conformant, bootstrap moves on. If it says no, `bootstrap_policies` proceeds to recreate the policy through `_policy_for`.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (fetchrow).


##### `_lock_holders`  (lines 128–143)

```
async def _lock_holders(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Builds a readable report of database sessions that currently hold locks on a table. This makes lock timeout errors useful instead of mysterious.

**Data flow**: It receives an open database connection and a table name. It queries Postgres lock and activity views for other sessions that hold granted locks on that table. It turns each session into text showing the process id, role, state, transaction age, and a shortened version of the running query. It returns that combined text, or a message saying no holder is visible.

**Call relations**: This is used only when `bootstrap_policies` catches a lock timeout while checking or changing a table. It gives `bootstrap_policies` the details needed to raise an error that points operators toward the blocking database session.

*Call graph*: called by 1 (bootstrap_policies); 1 external calls (fetch).


##### `_grant_serve_role`  (lines 146–159)

```
async def _grant_serve_role(connection: asyncpg.Connection) -> None
```

**Purpose**: Applies the table, sequence, and schema permissions that let the limited serving role use the public schema. These permissions allow database operations in general, while RLS decides which rows are allowed.

**Data flow**: It receives an open database connection. It first adjusts default privileges for future objects owned by `ufo_owner`, then grants `ufo_serve` usage on the public schema, read/write access on current public tables, and usage on current public sequences. It does not return a value; it changes database privileges.

**Call relations**: `ensure_serve_role` calls this after creating or updating the serving role. It is the permission-granting part of role setup, separate from password creation and database creation.

*Call graph*: called by 1 (ensure_serve_role); 1 external calls (execute).


##### `_ensure_database`  (lines 162–165)

```
async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None
```

**Purpose**: Creates a named Postgres database if it does not already exist. This makes setup repeatable: running it twice will not fail just because the database was already created.

**Data flow**: It receives an open database connection, a database name, and an owner role name. It checks Postgres’s database catalog for that name. If it is absent, it sends a `create database` command with the chosen owner. If it is already present, it does nothing.

**Call relations**: `ensure_serve_role` calls this near the end of role setup to make sure the related `_dbos` database exists and is owned by the serving role.

*Call graph*: called by 1 (ensure_serve_role); 2 external calls (execute, fetchval).


##### `_policy_for`  (lines 168–175)

```
async def _policy_for(connection: asyncpg.Connection, table: str) -> None
```

**Purpose**: Installs the project’s workspace RLS policy on one table. It is the function that actually turns the rule into database commands.

**Data flow**: It receives an open database connection and a table name. It asks `_scope_column` which column should be compared with the current workspace id. It enables row level security on the table, removes any old policy with the managed policy name, and creates a new policy that filters both reads and writes to rows matching `app.workspace_id`, a session setting in Postgres.

**Call relations**: `bootstrap_policies` calls this only for tables that `_conformant` says are missing or drifting from the expected policy. `_policy_for` depends on `_scope_column` so it applies the rule to the correct workspace-identifying column.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (execute).


##### `_scope_column`  (lines 178–192)

```
async def _scope_column(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Decides which column on a table identifies the workspace. This keeps the RLS rule consistent while allowing the workspace table itself to use its primary `id` column.

**Data flow**: It receives an open database connection and a table name. If the table is the `workspace` table, it returns `id`. Otherwise, it checks whether the table has a `workspace_id` column. If it does, it returns `workspace_id`. If not, it raises an error because the table cannot be safely protected by the workspace policy.

**Call relations**: Both `_conformant` and `_policy_for` call this before comparing or creating a policy. It is the shared rule that prevents a public table from silently escaping workspace scoping.

*Call graph*: called by 2 (_conformant, _policy_for); 1 external calls (fetchval).


### Request and operator identity
Agent execution scope, signed member identity, and operator session lookup define who is acting in a workspace.

### `core/src/ufo/agent_scope.py`

`domain_logic` · `cross-cutting during agent-scoped work`

Some parts of the system need to know “who is acting right now?” without passing an agent ID through every single function call. This file provides that ambient identity, similar to wearing a temporary name badge while doing a task. The badge says both the workspace and the agent, because an agent only makes sense inside a specific workspace.

The main tool is the `agent(agent_id)` context manager. Code enters it with `with agent(agent_id):`, and from then until the block ends, calls can ask for the current agent. It stores this information in a `ContextVar`, which is a safe per-task storage area: separate async tasks or request flows do not accidentally share the same current agent.

The file is deliberately strict. If code tries to switch from one bound agent to another inside the same active agent scope, it raises an error instead of silently changing identity. If code asks for the current agent when none is bound, it raises `AgentUnbound`. If the workspace has changed since the agent was bound, it also raises an error. These checks matter because agent-scoped capabilities are security-sensitive: without them, one agent might accidentally act in another agent’s workspace or code might perform privileged work without a clear owner.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: Creates a temporary agent identity for the current block of work. Use it around code that should run as a specific agent inside the already-current workspace.

**Data flow**: It receives an `agent_id`. It reads the current workspace using `ws_current()`, combines the workspace ID and agent ID into an `AgentScope`, and stores that scope as the current agent. If another different agent is already bound, it stops with an error. While the `with` block runs, callers can retrieve the scope; when the block exits, the previous agent setting is restored.

**Call relations**: This function is the entry point for binding an agent identity. It calls `ufo.workspace.ws_current` to attach the agent to the active workspace, then creates an `AgentScope`. Later, code inside the block typically calls `agent_current` to check who the current agent is.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: Returns the agent identity that is currently bound. It is used by code that needs to confirm which agent owns the capability or action being used.

**Data flow**: It reads the current value from the agent context. If no agent has been set, it raises `AgentUnbound` with a clear message telling the caller to use `with agent(agent_id):`. If an agent is set, it also reads the current workspace and checks that it still matches the workspace stored with the agent. If everything matches, it returns the `AgentScope` containing both IDs.

**Call relations**: This function is called by agent-scoped code when it needs the current identity. It relies on `agent` having already placed an `AgentScope` in the context, and it calls `ufo.workspace.ws_current` to make sure that identity still belongs to the active workspace.

*Call graph*: 2 external calls (__init__, ws_current).


### `core/src/ufo/bearer.py`

`domain_logic` · `login, request handling, cross-cutting authentication`

This file is the shared rulebook for UFO bearer tokens. A bearer token is a string a user can present as proof of identity, like a stamped wristband at an event. The stamp matters: anyone can write a name on paper, but only the real stamp proves it came from the system.

The token contains three claims: the workspace ID, the member email, and an expiry time. The contents are encoded into a URL-safe text form, then signed with HMAC-SHA256. HMAC is a way to make a tamper-proof signature using a shared secret. If someone changes even one character in the token body, the signature no longer matches.

The signing secret comes from the `UFO_TOKEN_SECRET` environment variable. That means extensions can ask this core code to verify a token without needing to know the secret themselves.

The file supports two main situations. In one, a process is pinned to a single workspace and checks that the token belongs to that workspace. In the other, a shared service accepts requests for many workspaces and reads the workspace from the verified token. It also defines the browser cookie name and login path used by the surrounding session flow.

#### Function details

##### `mint_token`  (lines 33–50)

```
def mint_token(secret: str, workspace_id: str, email: str, ttl: timedelta, now: datetime | None=None) -> str
```

**Purpose**: Creates a signed token that says a specific email belongs to a specific workspace until a specific time. This is used by token issuers so every minted token has the same shape and signing rules.

**Data flow**: It receives a secret, workspace ID, email address, time-to-live, and optionally a fixed current time. It trims and lowercases the email, calculates the expiry time, turns the claims into compact JSON, encodes that JSON safely for use in a token, signs the encoded body with the secret, and returns one string containing the body and signature.

**Call relations**: This is the issuing side of the token flow. Other parts of the system call it when they need to give a user a valid bearer token. The tokens it creates are later checked by `verified_claims`, `verify_token`, or `workspace_claim`.

*Call graph*: 4 external calls (urlsafe_b64encode, now, new, dumps).


##### `verified_claims`  (lines 53–76)

```
def verified_claims(token: str, now: int | None=None) -> tuple[str, str] | None
```

**Purpose**: Checks whether a token is real, unexpired, and shaped correctly. If it passes, it returns the workspace and email claims; otherwise it returns nothing.

**Data flow**: It receives a token and optionally a current timestamp for testing or controlled checks. It reads the signing secret from the environment, splits the token into body and signature, recreates the expected signature, compares it safely, decodes the body, parses the JSON, checks that the workspace, email, and expiry have the right types, and rejects expired tokens. On success it outputs the workspace string and email string.

**Call relations**: This is the shared verification core. `verify_token` uses it when the caller already knows which workspace should match. `workspace_claim` uses it when the caller needs to discover the workspace from the token itself. It relies on `_secret` to get the signing key and `_b64url_decode` to read the encoded token body.

*Call graph*: calls 2 internal fn (_b64url_decode, _secret); called by 2 (verify_token, workspace_claim); 4 external calls (now, compare_digest, new, loads).


##### `verify_token`  (lines 79–90)

```
def verify_token(token: str, workspace_id: UUID, now: int | None=None) -> str | None
```

**Purpose**: Checks that a token authenticates a member for one expected workspace. It returns the member email when the token is valid for that workspace, and returns nothing when it is invalid, expired, or for a different workspace.

**Data flow**: It receives a token, the workspace UUID that this process expects, and optionally a current timestamp. It asks `verified_claims` to prove the token first. If verification succeeds, it compares the token's workspace claim to the expected workspace ID. If they match, it returns the lowercased email; if not, it returns nothing.

**Call relations**: This function builds on the general token checker for the single-workspace case. It is the extra gate that stops a valid token from one workspace being accepted in another workspace.

*Call graph*: calls 1 internal fn (verified_claims).


##### `workspace_claim`  (lines 93–104)

```
def workspace_claim(token: str, now: int | None=None) -> UUID | None
```

**Purpose**: Finds the workspace named by a valid token. This is useful for shared services that serve many workspaces and need to decide which workspace a request belongs to.

**Data flow**: It receives a token and optionally a current timestamp. It first asks `verified_claims` to confirm the token is signed and not expired. Then it converts the workspace claim from text into a UUID object. If verification fails or the workspace text is not a valid UUID, it returns nothing.

**Call relations**: This function is the many-workspace counterpart to `verify_token`. Instead of comparing against a known workspace, it trusts the workspace only after `verified_claims` has proven the token was signed by this deployment.

*Call graph*: calls 1 internal fn (verified_claims); 1 external calls (UUID).


##### `_secret`  (lines 107–111)

```
def _secret() -> str
```

**Purpose**: Reads the token signing secret from the environment. It stops verification immediately if the secret is missing, because tokens cannot be trusted without it.

**Data flow**: It looks up `UFO_TOKEN_SECRET` in the process environment. If a value is present, it returns that value. If the value is absent or empty, it raises an error explaining that the secret must be set.

**Call relations**: `verified_claims` calls this before checking a token signature. This keeps the secret lookup in one place, so callers do not need to pass or hold the secret themselves.

*Call graph*: called by 1 (verified_claims).


##### `_b64url_decode`  (lines 114–115)

```
def _b64url_decode(value: str) -> bytes
```

**Purpose**: Decodes the token body from URL-safe base64 text back into bytes. It also restores any missing padding characters that were removed to keep the token shorter.

**Data flow**: It receives the encoded token body as text. It adds the right number of `=` padding characters, decodes the URL-safe base64 text, and returns the original bytes so they can be parsed as JSON.

**Call relations**: `verified_claims` uses this after the signature has matched. It is a small helper that keeps the token-reading details separate from the higher-level security checks.

*Call graph*: called by 1 (verified_claims); 1 external calls (urlsafe_b64decode).


### `core/src/ufo/ext/operator.py`

`domain_logic` · `operator request handling`

Operator tools are powerful internal pages, so they need a careful way to know who is using them and which customer workspace they are looking at. This file is the shared gatekeeper for those tools. It looks for a bearer token, which is a long-lived secret proving the user is allowed in, but it deliberately avoids accepting that token in the URL. URLs often end up in browser history, logs, and copied links, so putting a secret there would be risky.

The flow is like a secure front desk. First, `operator_bearer` checks the safest places for the token: the Authorization header, then a browser cookie, and only during the login POST, the submitted form body. Next, `resolve_operator_workspace` verifies that token and checks that the user's email belongs to the special operator email domain. Only after that domain check can the request choose a workspace with `?ws=`. Without `?ws=`, the workspace stored inside the token is used.

Finally, `bind_operator_session` turns a posted token into an HTTP-only cookie. HTTP-only means normal browser JavaScript cannot read it, which helps protect the secret. The cookie is shared across operator surfaces, so an operator signs in once and can move between these internal tools.

#### Function details

##### `operator_bearer`  (lines 25–38)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: This function finds the operator bearer token for a request without ever reading it from the URL. It exists to keep the long-lived credential out of places like access logs and browser history.

**Data flow**: It receives a web request. It first checks the Authorization header for a `Bearer ...` value, then checks the shared operator session cookie, then, only for POST requests, reads the submitted form and looks for the `token` field. It returns the cleaned-up token text if one is found, or an empty string if not.

**Call relations**: When an operator-only surface needs to decide who is making a request, `resolve_operator_workspace` calls this function first. If the token was posted in a form, this function asks the request object to parse that form before handing the token back.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 41–65)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function decides which workspace an operator request is allowed to act within. It verifies the bearer token, confirms the user belongs to the operator email domain, and then chooses either the token's own workspace or a requested workspace override.

**Data flow**: It receives the current request and a surface authentication object. It asks `operator_bearer` for the token, verifies the token's claims, and reads the claimed workspace and email address. If the email domain is not the operator domain, it rejects the request by returning `None`. If no `ws` query value is present, it returns the workspace UUID from the token. If `ws` is present, it treats it as either a raw workspace UUID or, if it is not a UUID, turns the lowercased customer domain into a stable UUID using DNS-based UUID generation.

**Call relations**: This is the main authorization checkpoint for operator surfaces. It relies on `operator_bearer` to collect the token safely, `verified_claims` to prove the token is real, and `email_domain` to enforce the operator-domain gate before allowing workspace re-scoping.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 68–80)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens an operator browser session by saving a posted bearer token into the shared operator cookie, then redirecting the browser back to the page. It lets operators authenticate once and continue using the internal tools without reposting the token each time.

**Data flow**: It receives the surface context and the current request. It reads the submitted form and looks for a non-empty `token` field. If the token is missing, it returns a JSON error with a 400 status code. If the token is present, it creates a redirect response back to the same URL, sets the token as the `ufo_debug` session cookie with `SameSite=Lax`, and returns that response.

**Call relations**: This is used at the moment a browser session is opened. It depends on the request's form parser to read the submitted token, returns `JSONResponse` for bad input, uses `RedirectResponse` for the normal successful path, and calls `set_session_cookie` so later requests can be recognized by `operator_bearer`.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### Member eligibility and workspace context
Seat rules determine which members may be served while the current workspace context anchors secrets, billing, and scoped access.

### `core/src/ufo/seats.py`

`domain_logic` · `cross-cutting: member creation, admission checks, running-turn checks, admin seat tools`

A “seat” here means permission for a workspace member to talk to the agent and have their work continue. The member record itself is treated as a lasting identity, like a contact card in an address book. Revoking a seat does not delete the person; it only removes their right to get answers until an admin grants the seat again.

This file is the central rulebook for that behavior. It checks whether a single member is seated, whether a whole group of members are still seated, and gives a snapshot of all members with their seated and admin status. It also contains the only approved way to create a member, so all member creation paths use the same email rules and avoid duplicate rows for the same person.

The most important safety rule is that the last seated admin cannot be unseated. Since seat changes happen through chat, removing the final admin would lock the workspace out of restoring access. The file uses a database row lock, like taking a numbered ticket before changing a shared ledger, so two admins cannot be revoked at the same time in a way that accidentally leaves none.

Without this file, different parts of the system could disagree about who is allowed to speak, running tasks might keep going after access was removed, and workspaces could lose the only person able to restore seats.

#### Function details

##### `gate_member`  (lines 37–48)

```
def gate_member(speaker_member_id: UUID | None, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Chooses which member a turn should be checked against for seat access. If there is a direct speaker, it uses that person; otherwise it uses the member the turn is acting for, such as the person who scheduled or requested the work.

**Data flow**: It receives two possible member IDs: the speaker and the on-behalf-of member. It returns the speaker ID when present; if not, it returns the on-behalf-of ID. If both are missing, it returns nothing, meaning there is no member to gate on.

**Call relations**: This small rule is meant to keep admission, resume checks, dispatch checks, and per-round checks from inventing different meanings for the same turn. It does not call any helper; it simply makes the shared choice before other code asks whether that member has a seat.


##### `SeatSnapshot.seated`  (lines 72–73)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a snapshot currently have seats. This gives callers a simple number without making them repeat the counting logic.

**Data flow**: It reads the snapshot’s tuple of member entries. For each entry, it checks the seated flag, counts the true ones, and returns that total as an integer. It does not change the snapshot.

**Call relations**: This property is used after a SeatSnapshot has already been built, usually from Seats.snapshot. It depends only on the data already inside the snapshot and does not call out to the database.


##### `Seats.admits`  (lines 84–98)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Answers the basic access question: is this member part of this workspace and currently seated? It is used wherever the system must decide whether the agent may answer a member.

**Data flow**: It receives a database connection and a member ID. It looks up that member row inside this Seats object’s workspace and reads the seated_at field, which is set when the member has a seat. It returns true only if the row exists and seated_at is not empty; otherwise it returns false.

**Call relations**: The function is a direct database check. It builds a SQL select query and runs it through the async database connection. Other admission or enforcement code can call it each time access must be freshly verified, so revoking a seat takes effect without waiting for cached workspace state.

*Call graph*: 2 external calls (execute, select).


##### `Seats.all_seated`  (lines 100–120)

```
async def all_seated(self, connection: AsyncConnection, member_ids: Collection[UUID]) -> bool
```

**Purpose**: Checks whether every member in a given group still has a seat. This is useful for turns or parked work that may involve more than one member.

**Data flow**: It receives a database connection and a collection of member IDs. If the collection is empty, it returns true because there is nobody to block. Otherwise it asks the database how many of those IDs belong to this workspace and have seated_at set, then compares that count with the number requested. The result is true only when every requested member is seated.

**Call relations**: Like Seats.admits, it uses SQL through the async connection, but it checks a whole set in one database round trip. It does not call local helpers; it is designed for higher-level checks that need one answer for a group before continuing work.

*Call graph*: 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 122–145)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a read-only picture of all members in a workspace, including their email, whether they are seated, and whether they are admins. This is the data a seat-reporting or admin view needs.

**Data flow**: It receives a database connection. It queries all member rows for the workspace, ordered by creation time and ID for stable display. For each row it creates a SeatEntry, then wraps all entries in a SeatSnapshot and returns it.

**Call relations**: This function sits between raw database rows and human-facing seat information. It calls the database, turns each row into a SeatEntry, and returns a SeatSnapshot whose seated property can later count active seats.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 147–157)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Restores a member’s seat by email. It is safe to call even if the member is already seated, because in that case it does nothing.

**Data flow**: It receives a database connection and an email address. It first uses Seats._member_by_email to find the member in this workspace and learn whether they already have a seat. If seated_at is already set, it returns without changing anything. Otherwise it updates the member row so seated_at and updated_at become the current database time.

**Call relations**: This is the admin-facing “give access back” path. It hands the lookup work to Seats._member_by_email, then uses a database update only when needed. If the email does not belong to a workspace member, the helper raises UnknownMember and the grant does not happen.

*Call graph*: calls 1 internal fn (_member_by_email); 2 external calls (execute, update).


##### `Seats.revoke`  (lines 159–182)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a member’s seat by email, while preventing the workspace from losing its final seated admin. It is safe to call for someone already unseated, because then it does nothing.

**Data flow**: It receives a database connection and an email address. First it locks the workspace row in the database, so overlapping revokes are forced to happen one after another. It then finds the member by email. If the member is already unseated, it returns. If the member is an admin and the seated admin count is one, it raises LastAdminSeatRevocation. Otherwise it clears seated_at and updates updated_at.

**Call relations**: This is the admin-facing “remove access” path. It calls Seats._member_by_email to identify the target and Seats._seated_admin_count when the target is an admin. It uses SQL select and update operations through the async connection, and it deliberately does not touch running turns; other enforcement checks will notice the revoked seat on their next check.

*Call graph*: calls 2 internal fn (_member_by_email, _seated_admin_count); 4 external calls (__init__, execute, select, update).


##### `Seats._member_by_email`  (lines 184–201)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None, bool]
```

**Purpose**: Finds one workspace member by email and returns the details seat changes need. It centralizes the email lookup so grant and revoke use the same matching rule.

**Data flow**: It receives a database connection and an email address. It trims and lowercases the email for comparison, queries this workspace’s member table, and returns the member ID, seated_at value, and admin flag. If no matching member exists, it raises UnknownMember instead of returning a blank result.

**Call relations**: Seats.grant and Seats.revoke both call this before changing a seat. The helper talks directly to the database with a select query and turns the absence of a row into a clear error for the caller.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_admin_count`  (lines 203–212)

```
async def _seated_admin_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many admins in this workspace currently have seats. It exists to support the rule that the final seated admin cannot be removed.

**Data flow**: It receives a database connection. It asks the database to count member rows in this workspace where seated_at is set and is_admin is true. It returns that count as an integer.

**Call relations**: Seats.revoke calls this only when the target member is an admin. The count helps revoke decide whether to proceed or raise LastAdminSeatRevocation.

*Call graph*: called by 1 (revoke); 2 external calls (execute, select).


##### `email_domain`  (lines 215–228)

```
def email_domain(email: str) -> str
```

**Purpose**: Extracts the domain part of a valid email address, in lowercase. It rejects malformed addresses by returning an empty string, so bad email text cannot pass domain-based membership checks.

**Data flow**: It receives an email string. It trims spaces at the ends, lowercases it, splits it around the @ sign, and checks that there is exactly one local part and one domain part with no whitespace. If valid, it returns the domain; otherwise it returns an empty string.

**Call relations**: create_member calls this before inserting a member, so every stored member email has the same basic shape. workspace_domain also calls it when deriving a workspace’s domain from its first member.

*Call graph*: called by 2 (create_member, workspace_domain).


##### `workspace_domain`  (lines 231–248)

```
async def workspace_domain(connection: AsyncConnection, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the email domain that represents a workspace. It uses the first member’s email as the source of truth.

**Data flow**: It receives a database connection and workspace ID. It reads the earliest-created member email for that workspace. If there is no member yet, it returns null. Otherwise it passes the email to email_domain and returns the resulting domain, or null if no domain can be derived.

**Call relations**: This function combines one database read with the shared email_domain parser. That keeps every caller using the same domain derivation rather than copying slightly different rules.

*Call graph*: calls 1 internal fn (email_domain); 2 external calls (execute, select).


##### `member_is_admin`  (lines 251–261)

```
async def member_is_admin(connection: AsyncConnection, workspace_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Checks whether a particular member is an admin in a particular workspace. It is a simple permission fact lookup.

**Data flow**: It receives a database connection, workspace ID, and member ID. It queries the member table for that exact row and reads the is_admin value. It returns true if the row exists and says the member is an admin; otherwise it returns false.

**Call relations**: This function is a direct database helper for code that needs to know whether a member has admin status. It does not call local helpers; it builds and executes a select query through the async connection.

*Call graph*: 2 external calls (execute, select).


##### `create_member`  (lines 264–326)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str, *, is_admin: bool=False) -> UUID
```

**Purpose**: Creates a member row for a workspace, using one shared path for onboarding, joins, and admin-added members. New members are seated by default through the database column behavior, and this function can mark them as admins when needed.

**Data flow**: It receives a database connection, workspace ID, email address, and optional admin flag. It first validates the email shape with email_domain, then lowercases the address. It locks the workspace row to keep concurrent member creation in a safe order. It then tries to insert a new member with a fresh UUID. If another request already created the same workspace/email pair, the insert does nothing and the function reads and returns the existing member ID instead.

**Call relations**: This is the single member-creation doorway for the file. It calls email_domain to enforce address shape, uses uuid4 to create a new ID when insertion succeeds, and talks to the database with select and insert operations. Its conflict behavior lets two simultaneous attempts for the same person collapse into one member row.

*Call graph*: calls 1 internal fn (email_domain); 3 external calls (execute, select, uuid4).


##### `member_workspaces`  (lines 329–337)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate source for jobs that should run on workspaces with at least one member. It gives extensions a safe way to ask for those workspaces without directly knowing the member table query.

**Data flow**: It defines a small inner query function that selects distinct workspace IDs from the member table. It passes that query builder to owner_candidates, which wraps it as a WorkspaceCandidates object. The returned object can later be used by a job scheduler or extension to choose workspaces.

**Call relations**: This function hands its inner with_a_member query to ufo.candidates.owner_candidates. The local file owns the member-table knowledge, while outside job code receives a higher-level candidate object.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 334–335)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the actual database query for member_workspaces: one row per workspace that has at least one member. It is nested so callers use the higher-level candidate object instead of calling this directly.

**Data flow**: It takes no arguments. It builds a SQL select query over the member table, selecting workspace IDs and marking them distinct so each workspace appears once. It returns the query object rather than executing it.

**Call relations**: member_workspaces passes this function into owner_candidates. Later, that candidate machinery can call it when it needs the database query for eligible workspaces.

*Call graph*: 1 external calls (select).


### `core/src/ufo/workspace.py`

`domain_logic` · `cross-cutting during requests, turns, and background jobs`

A workspace is like a customer account or tenant. Many parts of the system need to know which workspace they are working inside so they can read the right secrets, charge the right account, and keep database reads and writes separated. This file provides that shared boundary.

Code enters a workspace by using `with ws(workspace_id):`. Inside that block, `ws_current()` can recover the active workspace without every function needing a `workspace_id` argument. If code tries to get workspace-scoped data without first entering a workspace, it raises `WorkspaceUnbound` instead of guessing. That is important because guessing could leak one workspace’s data into another or record charges in the wrong place.

The `WorkspaceScope` object is the main handle. It can fetch credentials for the current workspace, falling back to platform-wide environment variables when the workspace has not stored its own key. It can also store or rotate workspace-owned credentials.

Billing works through `billable_event()`. Code records model usage during the block, but the charge is only written if the block finishes successfully. If an exception happens, nothing is billed. This is like writing items on a restaurant ticket, but only sending the ticket to the register if the meal is actually completed.

#### Function details

##### `init_workspace_credentials`  (lines 29–33)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that the rest of this file will use to look up workspace-owned secrets. It is meant to be called once during startup, so later workspace code knows where to find stored keys.

**Data flow**: It receives either a credential store object or `None`. It saves that value in a module-level variable. After that, credential lookups will use the store when one exists, or fall back to environment variables when it does not.

**Call relations**: This is setup for later calls to `WorkspaceScope.credential`, `WorkspaceScope.credential_is_stored`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential`. Those methods all consult the stored global credential store to decide whether workspace-specific secrets are available.


##### `BillableEvent.usage`  (lines 49–51)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Adds one model usage record to a billable event. Code uses this inside a billing block to say, in effect, “this workspace used this model this much.”

**Data flow**: It receives a model name, a usage object, and pricing information. It appends those three pieces to the event’s private list. It does not write to the database immediately; it only stages the usage for later billing.

**Call relations**: This is used inside the object created by `WorkspaceScope.billable_event`. That outer context collects usage during a successful operation and later writes each staged item to the workspace’s usage ledger.


##### `WorkspaceScope.credential`  (lines 60–74)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Fetches the secret value for a named credential slot for this workspace. It first tries the workspace’s own stored key, then falls back to a platform-wide environment variable if no workspace key is set.

**Data flow**: It starts with the current workspace ID, a credential slot name, and optionally an environment variable name. If a credential store is configured, it asks that store for the workspace’s value. If the store says the slot is unset, it checks the environment instead, using the provided environment name or the uppercase slot name. It returns the secret string if found; if nothing is available, it raises `CredentialSlotUnset`.

**Call relations**: This is the main path for code that needs a secret while acting inside a workspace. It relies on the credential store installed by `init_workspace_credentials`. When neither the workspace store nor the environment provides a value, it creates a `CredentialSlotUnset` error so the caller fails clearly instead of continuing without a key.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.credential_is_stored`  (lines 76–86)

```
async def credential_is_stored(self, slot: str) -> bool
```

**Purpose**: Answers whether this workspace has its own stored credential for a slot. This matters because usage with a workspace-owned provider key may be treated differently from usage paid for through platform credentials.

**Data flow**: It receives a credential slot name and checks the configured credential store. If there is no store, it returns `false`. If the store can return a value for this workspace and slot, it returns `true`. If the store reports the slot is unset, it returns `false`.

**Call relations**: This complements `WorkspaceScope.credential`. Instead of returning the secret itself, it tells other billing or provider-selection code whether the credential came from the workspace’s own storage or from the platform fallback.


##### `WorkspaceScope.rotate_credential`  (lines 88–93)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing workspace credential only if the caller’s expected current value matches. This compare-and-swap style protects against overwriting a secret that changed in the meantime.

**Data flow**: It receives a slot name, an expected existing value, and a new plaintext secret. If no credential store is configured, it returns `false`. Otherwise it asks the store to rotate the credential for this workspace and returns whether that rotation succeeded.

**Call relations**: This uses the credential store installed by `init_workspace_credentials`. It is part of the workspace credential lifecycle, alongside `put_credential` for first-time storage and `credential` for later reading.


##### `WorkspaceScope.put_credential`  (lines 95–99)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a new workspace-owned credential. It is used when an authorized owner adds an initial secret for a workspace.

**Data flow**: It receives a slot name and a plaintext secret. If no credential store is configured, it raises a runtime error because there is nowhere safe to save the secret. Otherwise it sends the workspace ID, slot, and plaintext to the credential store.

**Call relations**: This depends on the credential store set by `init_workspace_credentials`. After it stores a credential, later calls to `WorkspaceScope.credential` can retrieve that workspace-specific value instead of using a platform environment fallback.


##### `WorkspaceScope.billable_event`  (lines 102–112)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a billing block for work done by this workspace. Usage can be added during the block, and it is recorded only if the block exits without an error.

**Data flow**: It creates a fresh `BillableEvent` and yields it to the caller. The caller adds usage records to that event. When the block finishes successfully, the function opens a workspace database transaction and writes each recorded usage item to the workspace’s ledger. If there are no usage records, it writes nothing. If the caller’s block raises an exception, the write step is skipped.

**Call relations**: This calls `BillableEvent` to collect pending charges, opens a database transaction with `workspace_tx`, and hands each usage item to `record_workspace_usage`. It is the bridge between in-memory usage tracking during a model call and durable billing records for the workspace.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 116–124)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Binds a workspace ID as the active workspace for a block of code. This lets everything inside the block find the workspace safely without passing the ID through every function call.

**Data flow**: It receives a workspace ID and stores it in the current execution context. It yields a `WorkspaceScope` for that ID to the caller. When the block ends, it restores the previous context so the workspace binding does not leak into unrelated work.

**Call relations**: This is normally used at the boundary of a request, turn, or job. It sets the value that `ws_current` later reads, and it also works with database workspace context through `current_workspace.set` and `current_workspace.reset`.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 127–133)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it raises a clear error instead of allowing code to read secrets, bill usage, or access data without a workspace.

**Data flow**: It reads the current workspace ID from the execution context. If a workspace ID is present, it wraps it in a `WorkspaceScope` and returns that. If no ID is present, it raises `WorkspaceUnbound` with a message telling the caller to use `with ws(workspace_id):`.

**Call relations**: This is the lookup side of the context set by `ws`. Workspace-scoped code calls it when it needs credentials or billing, and the error path protects the system from accidental unscoped work.

*Call graph*: 3 external calls (__init__, __init__, get).
