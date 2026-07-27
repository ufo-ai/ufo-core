# Control-plane database setup and row isolation  `stage-19.1`

This stage is part of startup for the control gateway, the service that manages gateway-specific data before normal requests are served. Its job is to make sure the database is ready and safe to use. It is kept separate from the main application schema because it owns control-gateway tables and the rules for keeping different workspaces apart.

The schema.py file is the table builder and inspector. It prepares the PostgreSQL tables the gateway needs, and checks that they exist in the expected form. This makes setup an intentional step at launch, instead of letting request code quietly create or change tables while the system is already running.

The rls.py file sets up the safety fence. It configures PostgreSQL row-level security, which means the database itself filters rows so one workspace cannot see another workspace’s data. It also creates a limited database role used by the application when handling requests. Together, these files give the gateway a known database shape and a built-in boundary between tenants.

## Files in this stage

### Database bootstrap and isolation
Sets up the control gateway database schema and PostgreSQL row-level-security boundary for workspace-isolated request handling.

### `control/src/ufo_control/rls.py`

`domain_logic` · `startup/bootstrap`

This file protects a shared database where many workspaces live side by side. The risk is simple: if the application accidentally runs a broad query, it could read or change another workspace’s data. PostgreSQL row-level security, or RLS, is the guardrail used here: the database itself refuses rows unless their workspace ID matches the current workspace setting. Think of it like a hotel key card system where every door checks the card, rather than trusting guests to only open the right door.

The file has two main jobs. First, it prepares a database login role called `ufo_serve`. This is the role the running service should use. Its password is derived from a secret seed, its permissions are narrowed to normal table and sequence access, and timeouts are set so bad transactions do not sit open forever. It also makes sure a related DBOS database exists.

Second, it walks through every public table and makes sure the workspace policy is present and correct. It skips the migration bookkeeping table, checks whether each table already has the expected policy, and only changes tables that need fixing. If a table is locked by another database session, it fails after a short timeout and reports who is holding the lock instead of hanging indefinitely. Tables must either be the `workspace` table itself or have a `workspace_id` column; otherwise this file refuses to create a weak or meaningless policy.

#### Function details

##### `owner_dsn`  (lines 22–26)

```
def owner_dsn() -> str
```

**Purpose**: Reads the database connection string for the powerful owner account. This is needed because creating roles, granting permissions, and changing security policies require owner-level access.

**Data flow**: It looks in the process environment for `UFO_CONTROL_POSTGRES_OWNER_DSN`. If the value is present, it returns that string. If it is missing, it stops immediately with an error so the system does not try to bootstrap security without the needed credentials.

**Call relations**: This is a small access point for the owner database address. Other startup code can call it before using the setup functions in this file, so the rest of the RLS setup has a trusted connection string to work with.


##### `serve_password`  (lines 29–33)

```
def serve_password() -> str
```

**Purpose**: Creates the password for the limited `ufo_serve` database role from a secret seed. This avoids storing a separate plain password while still producing the same password every time.

**Data flow**: It reads `UFO_CONTROL_PG_ROLE_SEED` from the environment. It combines that seed with the serve role name, hashes the result with SHA-256, and returns the hexadecimal password text. If the seed is missing, it raises an error because the serve role cannot be safely created or used.

**Call relations**: Both `ensure_serve_role` and `serve_dsn` depend on this function. The first uses it to set the role’s password in PostgreSQL; the second uses it to build the application connection string for that same role.

*Call graph*: called by 2 (ensure_serve_role, serve_dsn); 1 external calls (sha256).


##### `serve_dsn`  (lines 36–37)

```
def serve_dsn(postgres_host: str, app_database: str) -> str
```

**Purpose**: Builds the database connection string that the application should use when serving normal traffic. It points to the limited `ufo_serve` role rather than the powerful owner role.

**Data flow**: It receives a PostgreSQL host and an application database name. It asks `serve_password` for the derived password, then returns a full async PostgreSQL connection string containing the role, password, host, and database.

**Call relations**: This function sits after password derivation and before runtime database access. Code that needs to connect as the safe serving role can call it instead of assembling the connection string by hand.

*Call graph*: calls 1 internal fn (serve_password).


##### `ensure_serve_role`  (lines 40–62)

```
async def ensure_serve_role(admin_dsn: str) -> None
```

**Purpose**: Creates or refreshes the limited PostgreSQL role used by the service, and gives it only the access it needs. This is part of making sure the app runs behind the workspace safety boundary instead of as an all-powerful database owner.

**Data flow**: It takes an administrator connection string, derives the serve password, opens a PostgreSQL connection, and sets a lock timeout. It checks whether the serve role exists, creates or updates it, allows it to set the workspace ID database setting, applies idle transaction timeouts, grants table and sequence permissions, and ensures a related DBOS database exists. It always closes the database connection when done.

**Call relations**: This is one of the main bootstrap routines in the file. It calls `serve_password` to get credentials, `_grant_serve_role` to apply permissions, and `_ensure_database` to create the companion database if needed. It uses `asyncpg.connect` to talk directly to PostgreSQL.

*Call graph*: calls 3 internal fn (_ensure_database, _grant_serve_role, serve_password); 1 external calls (connect).


##### `bootstrap_policies`  (lines 65–92)

```
async def bootstrap_policies(dsn: str) -> None
```

**Purpose**: Checks every public table and makes sure the workspace row-level security policy is enabled and correct. Without this, a new or changed table might accidentally allow cross-workspace data access.

**Data flow**: It receives a database connection string, connects to PostgreSQL, sets a short lock timeout, and lists all public tables. For each table except the migration version table, it first checks whether the current policy already matches the expected one. If not, it opens a short transaction and recreates the policy. If PostgreSQL cannot get the needed lock in time, it asks who is holding the lock and raises a clear error message.

**Call relations**: This is the main policy-enforcement pass. It relies on `_conformant` for the cheap check, `_policy_for` for actually enabling and recreating the policy, and `_lock_holders` to explain lock timeouts. It is meant to be run during startup or deployment bootstrap before the service relies on the database boundary.

*Call graph*: calls 3 internal fn (_conformant, _lock_holders, _policy_for); 1 external calls (connect).


##### `_conformant`  (lines 95–123)

```
async def _conformant(connection: asyncpg.Connection, table: str) -> bool
```

**Purpose**: Decides whether one table already has exactly the workspace security policy this project expects. This avoids unnecessary database changes when a table is already safe.

**Data flow**: It receives an open database connection and a table name. It reads PostgreSQL’s catalog records for row-level security and the named policy, finds the correct workspace column through `_scope_column`, builds the expected policy expression, and compares the stored policy details to that expected shape. It returns `true` only when RLS is enabled and the policy matches exactly.

**Call relations**: `bootstrap_policies` calls this before doing any policy-changing work. If it says the table is already conformant, the bootstrap loop moves on; if it says no, `bootstrap_policies` proceeds to `_policy_for` to repair or create the policy.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (fetchrow).


##### `_lock_holders`  (lines 126–141)

```
async def _lock_holders(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Explains who is currently holding locks on a table when the bootstrap process cannot proceed. This turns a vague database timeout into a useful diagnostic message.

**Data flow**: It receives an open database connection and a table name. It queries PostgreSQL’s lock and activity views for other sessions that hold granted locks on that table, then formats their process ID, database user, state, transaction age, and a shortened query. It returns that summary string, or a fallback message if no holder is visible.

**Call relations**: `bootstrap_policies` calls this only after a lock timeout while checking or changing a table policy. Its result is included in the raised error so an operator can find the blocking database session.

*Call graph*: called by 1 (bootstrap_policies); 1 external calls (fetch).


##### `_grant_serve_role`  (lines 144–157)

```
async def _grant_serve_role(connection: asyncpg.Connection) -> None
```

**Purpose**: Gives the `ufo_serve` role the normal database permissions it needs, while removing default grants that could make future tables too automatically permissive. It shapes the role into a limited service account.

**Data flow**: It receives an open database connection. It executes SQL statements that revoke default table and sequence privileges from future owner-created objects, grant use of the public schema, and grant select, insert, update, delete, and sequence usage on current objects. It does not return data; it changes database permissions.

**Call relations**: `ensure_serve_role` calls this after creating or updating the serve role. It is the permissions step in the role setup story, following credential setup and before the companion database check.

*Call graph*: called by 1 (ensure_serve_role); 1 external calls (execute).


##### `_ensure_database`  (lines 160–163)

```
async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None
```

**Purpose**: Makes sure a named PostgreSQL database exists, creating it if it is missing. In this file it is used to prepare a related DBOS database owned by the limited serve role.

**Data flow**: It receives an open database connection, a database name, and an owner role name. It checks PostgreSQL’s database list for that name. If the database is absent, it creates it with the requested owner; if it already exists, it leaves it alone.

**Call relations**: `ensure_serve_role` calls this near the end of role setup. After the serve role is ready, this helper makes sure the extra database named from the current app database is present.

*Call graph*: called by 1 (ensure_serve_role); 2 external calls (execute, fetchval).


##### `_policy_for`  (lines 166–173)

```
async def _policy_for(connection: asyncpg.Connection, table: str) -> None
```

**Purpose**: Enables and recreates the workspace row-level security policy for one table. This is the repair path when a table is missing the policy or has a policy that does not match the expected one.

**Data flow**: It receives an open database connection and a table name. It asks `_scope_column` which column identifies the workspace, builds a condition that compares that column to PostgreSQL’s current `app.workspace_id` setting, enables row-level security on the table, drops the old managed policy if it exists, and creates the expected policy for both reads and writes.

**Call relations**: `bootstrap_policies` calls this inside a short transaction when `_conformant` reports that a table needs work. `_policy_for` depends on `_scope_column` so it does not create a policy on a table that has no reliable workspace identifier.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (execute).


##### `_scope_column`  (lines 176–190)

```
async def _scope_column(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Finds the column that should be used to tie a table’s rows to a workspace. It also rejects tables that cannot be safely protected by the standard workspace policy.

**Data flow**: It receives an open database connection and a table name. If the table is the main `workspace` table, it returns `id`, because each workspace row identifies itself. Otherwise it checks whether the table has a `workspace_id` column. If it does, it returns `workspace_id`; if not, it raises an error explaining that the table is not workspace-scoped.

**Call relations**: Both `_conformant` and `_policy_for` call this. The check path uses it to know what policy expression should already exist, and the creation path uses it to build the new policy safely.

*Call graph*: called by 2 (_conformant, _policy_for); 1 external calls (fetchval).


### `control/src/ufo_control/schema.py`

`io_transport` · `startup and migration`

This file is the safety gate for the control service’s database layout. The service stores several “ledgers” in PostgreSQL, meaning durable tables that record important gateway state such as stored objects, invites, and Slack connection data. If those tables are missing or outdated, the gateway cannot safely answer requests.

The important idea is separation: one migration command shapes the database, and gateway replicas only check that the shape is already there. This avoids a common race where two running processes both try to create the same database table at the same time. Even commands like “create if not exists” can still collide internally in PostgreSQL, so this file uses a PostgreSQL advisory transaction lock, which is like taking a numbered ticket that says “only one database shaper may work right now.”

`shape_control_schema` is the migration path. It connects to the database, takes the lock, optionally removes an old invite table format that cannot name objects properly, and then runs all schema, table, and index creation statements. `require_control_schema` is the runtime guard. It connects to the database and refuses to let the gateway start if any required ledger table is absent, telling the operator to run the migration command first.

#### Function details

##### `shape_control_schema`  (lines 41–59)

```
async def shape_control_schema(dsn: str) -> None
```

**Purpose**: This function brings the control database schema up to the expected current shape. It is meant to be run by the migration command before gateway replicas begin serving traffic.

**Data flow**: It takes a database connection string as input, opens a PostgreSQL connection, and starts one transaction. Inside that transaction it takes a database-level advisory lock so only one schema-shaping process can proceed at a time. It checks for an older invite table layout that lacks object numbers; if found, it drops that table because its invite codes could no longer be honored safely. It then runs the prepared database definition statements and closes the connection when finished. The result is no returned value, but the database is left with the required schema and tables.

**Call relations**: This is the writer side of the startup story. A migration command or deployment job calls on it before the gateway runs. Its only outside handoff is to `asyncpg.connect`, which opens the PostgreSQL connection used for the transaction and statements.

*Call graph*: 1 external calls (connect).


##### `require_control_schema`  (lines 62–71)

```
async def require_control_schema(dsn: str) -> None
```

**Purpose**: This function checks that the needed control database tables already exist. It protects the gateway from starting in a broken state where requests would depend on missing ledgers.

**Data flow**: It takes a database connection string, opens a PostgreSQL connection, and checks each required ledger table by asking PostgreSQL whether that table name exists. If every table is present, it returns without producing a value. If any table is missing, it raises an error explaining that `ufo-control migrate` must be run before starting the gateway. It always closes the database connection afterward.

**Call relations**: This is the reader and guard side of the startup story. Gateway startup code would call it before serving requests, after migration is expected to have happened. Like the migration function, it relies on `asyncpg.connect` to talk to PostgreSQL, but it does not create or alter anything; it only verifies and fails fast if setup is incomplete.

*Call graph*: 1 external calls (connect).
