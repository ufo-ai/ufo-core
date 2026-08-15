# Control database and Alembic preflight entrypoints  `stage-1.1`

This stage is part of deployment and startup preparation, before the main application begins serving real traffic. Its job is to make sure the databases are in the right shape and that access rules are in place, instead of discovering problems during a live request.

The control package marker, __init__.py, is the small doorway that lets Python import the control database code. The schema.py file does the practical setup work for the control gateway. It creates or checks the PostgreSQL tables that the gateway depends on, making database preparation an intentional step. The rls.py file adds the safety fence around shared data. “Row-level security” means PostgreSQL checks each individual row and only lets a workspace see its own data. This file creates the application database role and applies those rules to public tables.

The env.py file in the core schema migrations is the migration entrypoint. It connects Alembic, the tool that updates database structure over time, to the project’s expected schema and runs the needed changes. Together, these files prepare both structure and access boundaries before normal work starts.

## Files in this stage

### Package entrypoint
The package marker makes the control database preparation modules importable.

### `control/src/ufo_control/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as a package when it contains an `__init__.py` file. That means code elsewhere can write imports that start with `ufo_control`, and Python will know that this directory is part of the project’s import structure. Think of it like putting a label on a drawer: the drawer may contain many useful tools, but this label mainly tells people and the system how to find them. Because the file is empty, it does not run setup code, expose shortcuts, or change how the package behaves. Its value is structural: without it, some Python tools or older Python import modes might not recognize `ufo_control` as a normal package, which could make imports fail or behave inconsistently.


### Control database setup
The control gateway preflight prepares required tables and enforces workspace isolation through PostgreSQL row-level security.

### `control/src/ufo_control/rls.py`

`domain_logic` · `startup / database bootstrap`

This file protects shared database tables so one workspace cannot accidentally see or change another workspace’s rows. PostgreSQL row-level security, or RLS, is a database feature that acts like a filter built into the table itself: even if a query asks for all rows, PostgreSQL only allows rows that match the current workspace setting.

The file does two main jobs. First, it prepares a limited database role called `ufo_serve`, which is the role the running service should use instead of the more powerful owner role. Its password is derived from a secret seed, so the system can recreate it consistently without storing the password directly. The setup also grants the role the table and sequence permissions it needs, while keeping ownership and administration separate.

Second, it walks through the public database tables and makes sure each one has the expected workspace policy. The policy compares a table’s workspace column with the database session setting `app.workspace_id`. The special `workspace` table uses its `id` column; other tables must have a `workspace_id` column. If a table lacks the needed column, setup fails rather than leaving the table unprotected.

The code is careful about database locks. It sets short timeouts, checks whether a table already has the right policy before changing it, and reports who is holding a blocking lock if setup cannot continue.

#### Function details

##### `owner_dsn`  (lines 22–26)

```
def owner_dsn() -> str
```

**Purpose**: Reads the database connection string for the powerful owner account from an environment variable. This is needed because role and policy setup must be done with privileges stronger than the normal application role.

**Data flow**: It looks in the process environment for `UFO_CONTROL_POSTGRES_OWNER_DSN`. If the value exists, it returns that string. If it is missing, it stops with a clear error so the system does not try to continue without the authority needed to secure the database.

**Call relations**: This is a small entry helper for other startup code. Nothing in this file calls it directly, but external setup code can use it before calling the role or policy bootstrap functions.


##### `serve_password`  (lines 29–33)

```
def serve_password() -> str
```

**Purpose**: Creates the password for the limited `ufo_serve` database role from a secret seed. This avoids hard-coding the password while still making it stable across runs.

**Data flow**: It reads `UFO_CONTROL_PG_ROLE_SEED` from the environment. It combines that seed with the fixed role name, hashes the result with SHA-256, and returns the hexadecimal password string. If the seed is missing, it raises an error because the service role cannot be safely created or used.

**Call relations**: `ensure_serve_role` calls this when creating or updating the PostgreSQL role. `serve_dsn` calls it when building the connection string the application will use.

*Call graph*: called by 2 (ensure_serve_role, serve_dsn); 1 external calls (sha256).


##### `serve_dsn`  (lines 36–37)

```
def serve_dsn(postgres_host: str, app_database: str) -> str
```

**Purpose**: Builds the database connection string for the limited service role. This is the connection the app should use when it wants PostgreSQL to enforce workspace boundaries.

**Data flow**: It receives a PostgreSQL host and application database name. It asks `serve_password` for the derived password, then returns a connection URL containing the `ufo_serve` username, that password, the host, and the database name.

**Call relations**: This function depends on `serve_password` so the generated connection string always matches the password assigned by `ensure_serve_role`. It is meant to be called by startup or configuration code that needs the normal application database URL.

*Call graph*: calls 1 internal fn (serve_password).


##### `ensure_serve_role`  (lines 40–64)

```
async def ensure_serve_role(admin_dsn: str) -> None
```

**Purpose**: Creates or updates the limited PostgreSQL role used by the service, then grants it the permissions it needs. This makes sure the app connects as a restricted user instead of as the database owner.

**Data flow**: It receives an administrator database connection string. It derives the service password, connects to PostgreSQL, sets a lock timeout, creates or updates the `ufo_serve` role, grants role relationship permissions, sets idle transaction timeouts, grants access to public tables and sequences, and creates a related database if it does not already exist. It closes the database connection when finished.

**Call relations**: This is one of the main setup routines in the file. It calls `serve_password` for the role credential, `_grant_serve_role` to apply table and sequence access, and `_ensure_database` to create the companion database when needed.

*Call graph*: calls 3 internal fn (_ensure_database, _grant_serve_role, serve_password); 1 external calls (connect).


##### `bootstrap_policies`  (lines 67–94)

```
async def bootstrap_policies(dsn: str) -> None
```

**Purpose**: Checks every public table and installs the workspace row-level security policy where needed. This is the routine that turns the database schema into a workspace-safe schema.

**Data flow**: It receives a database connection string, connects to PostgreSQL, sets a short lock timeout, lists all public tables, and skips the Alembic migration-version table. For each remaining table, it first asks `_conformant` whether the existing policy is already correct. If not, it opens a short transaction and calls `_policy_for` to recreate the policy. If a database lock blocks the work too long, it asks `_lock_holders` who is blocking it and raises an error with that information.

**Call relations**: This is the other main setup routine in the file. It coordinates the policy-checking path through `_conformant`, the policy-writing path through `_policy_for`, and the error-reporting path through `_lock_holders`.

*Call graph*: calls 3 internal fn (_conformant, _lock_holders, _policy_for); 1 external calls (connect).


##### `_conformant`  (lines 97–125)

```
async def _conformant(connection: asyncpg.Connection, table: str) -> bool
```

**Purpose**: Checks whether one table already has exactly the workspace security policy this system expects. It lets setup skip unnecessary database changes when a table is already safe.

**Data flow**: It receives an open database connection and a table name. It reads PostgreSQL’s system catalogs to see whether row-level security is enabled and whether the named policy has the expected condition, applies to all commands, and applies to the public role. It asks `_scope_column` which column should be used for this table. It returns `true` only if everything matches exactly; otherwise it returns `false`.

**Call relations**: `bootstrap_policies` uses this as the fast inspection step before doing any table-changing work. When `_conformant` needs to know which column should define the workspace boundary, it hands that question to `_scope_column`.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (fetchrow).


##### `_lock_holders`  (lines 128–143)

```
async def _lock_holders(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Explains who is currently holding a database lock on a table. This turns a vague timeout into a useful error message for operators.

**Data flow**: It receives an open database connection and a table name. It queries PostgreSQL’s lock and activity views for other sessions holding granted locks on that table. It formats their process id, username, state, transaction age, and a shortened query into a readable string. If no holder is visible, it returns a placeholder message saying so.

**Call relations**: `bootstrap_policies` calls this only after a lock timeout. Its output is included in the raised error so the person running setup can see what is blocking the security update.

*Call graph*: called by 1 (bootstrap_policies); 1 external calls (fetch).


##### `_grant_serve_role`  (lines 146–159)

```
async def _grant_serve_role(connection: asyncpg.Connection) -> None
```

**Purpose**: Gives the limited service role the practical permissions it needs on the public schema. It also adjusts default privileges so future tables do not automatically inherit unwanted direct grants.

**Data flow**: It receives an open database connection. It runs a series of PostgreSQL permission commands: revoking certain default table and sequence privileges, granting schema usage, granting read/write access to existing public tables, and granting sequence usage. It does not return a value; it changes database permissions.

**Call relations**: `ensure_serve_role` calls this after creating or updating the `ufo_serve` role. It is the permissions step in the role setup story.

*Call graph*: called by 1 (ensure_serve_role); 1 external calls (execute).


##### `_ensure_database`  (lines 162–165)

```
async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None
```

**Purpose**: Creates a PostgreSQL database if it does not already exist. This is used to make sure a related database owned by the service role is present.

**Data flow**: It receives an open database connection, a database name, and an owner role name. It checks PostgreSQL’s database list for that name. If the database is missing, it creates it with the requested owner. If it already exists, it leaves it alone.

**Call relations**: `ensure_serve_role` calls this near the end of role setup, after it has confirmed the service role exists. It keeps database creation idempotent, meaning repeated setup runs do not fail just because the database was already made.

*Call graph*: called by 1 (ensure_serve_role); 2 external calls (execute, fetchval).


##### `_policy_for`  (lines 168–175)

```
async def _policy_for(connection: asyncpg.Connection, table: str) -> None
```

**Purpose**: Installs the workspace row-level security policy for one table. This is the step that actually changes a table so PostgreSQL enforces the workspace filter.

**Data flow**: It receives an open database connection and a table name. It asks `_scope_column` which column should identify the workspace for that table. It builds a policy condition comparing that column to the session setting `app.workspace_id`, enables row-level security on the table, removes the old managed policy if present, and creates the new policy. It does not return a value; it changes the table definition.

**Call relations**: `bootstrap_policies` calls this when `_conformant` says a table is missing the correct policy or has a drifted one. `_policy_for` relies on `_scope_column` so the same column choice is used for both checking and creating policies.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (execute).


##### `_scope_column`  (lines 178–192)

```
async def _scope_column(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Decides which column represents the workspace boundary for a table. This prevents the system from silently applying a security policy to the wrong field.

**Data flow**: It receives an open database connection and a table name. If the table is the central `workspace` table, it returns `id` because each workspace row identifies itself. For any other table, it checks whether a `workspace_id` column exists. If it does, it returns `workspace_id`; if not, it raises an error because the table cannot be safely scoped to a workspace.

**Call relations**: Both `_conformant` and `_policy_for` call this. That means the code uses the same rule when deciding whether an existing policy is correct and when creating a new one.

*Call graph*: called by 2 (_conformant, _policy_for); 1 external calls (fetchval).


### `control/src/ufo_control/schema.py`

`orchestration` · `deploy migration and gateway startup`

This file is the gateway’s database “floor plan” for control data such as gateway claims, invites, and Slack connection records. Without it, a gateway replica could start serving requests while its required tables are missing, or two startup jobs could try to create the same table at the same time and one would crash with a database uniqueness error.

The file defines the set of ledger tables the gateway depends on, the SQL statements that create the schema and tables, and a PostgreSQL advisory lock. An advisory lock is a database-level lock chosen by the application; here it works like a single key to the maintenance room, so only one schema-shaping process can make changes at once.

The main migration function opens a database connection, starts one transaction, takes that lock, checks for an old invite table shape, drops that old table if needed, then runs all create-or-update statements. It also removes obsolete columns from the gateway store table. The important behavior is that running the migration again is safe: if the database is already up to date, it should do nothing meaningful.

The second function is the safety check used by the gateway. It does not create anything. It only verifies that every required ledger table exists, and if not, it tells the operator to run the migration command before starting the gateway.

#### Function details

##### `shape_control_schema`  (lines 46–68)

```
async def shape_control_schema(dsn: str) -> None
```

**Purpose**: This function brings the control database schema up to the current expected shape. It is meant to be run by the migration command before gateway replicas begin serving traffic.

**Data flow**: It receives a database connection string, uses asyncpg to connect to PostgreSQL, and opens a transaction so the changes are grouped together. Inside that transaction it takes the schema lock, checks whether an old invite table is missing the email-domain column, drops that outdated invite table if necessary, runs the schema and table creation statements, and removes obsolete columns. It returns nothing, but the database is left in the current expected shape; the connection is always closed afterward.

**Call relations**: This is the active migration path described by the file: the deployment or operator runs it before the gateway starts. Its only external handoff in the call graph is to asyncpg.connect, which gives it the live database connection it needs before it issues SQL statements.

*Call graph*: 1 external calls (connect).


##### `require_control_schema`  (lines 71–80)

```
async def require_control_schema(dsn: str) -> None
```

**Purpose**: This function checks that the required control ledger tables already exist. It protects the running gateway from silently serving requests against an unprepared database.

**Data flow**: It receives a database connection string, connects to PostgreSQL, and asks the database whether each required table name is registered. If every table is present, it finishes without returning a value. If any table is missing, it raises an error that names the absent table and tells the operator to run `ufo-control migrate`; the connection is closed either way.

**Call relations**: This is the gateway-side guard that complements the migration function. Instead of creating tables itself, it calls asyncpg.connect to inspect the database, then either allows startup to continue or stops it with a clear instruction to run the migration step first.

*Call graph*: 1 external calls (connect).


### Application migration bridge
The Alembic environment connects deployment-time migration execution to the core application schema.

### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `database migration run`

Database migrations are controlled changes to the shape of a database, such as adding a table or renaming a column. This file is the setup script Alembic uses when a migration command runs. Without it, Alembic would not know how to connect to the project’s database or what schema metadata to compare against.

The file starts by importing the project’s table metadata, which is the application’s map of what the database should look like. It then defines two steps. First, it creates an asynchronous database engine from Alembic’s configuration. An engine is the object SQLAlchemy uses to open database connections. Second, once connected, it hands that connection to Alembic so Alembic can run migration scripts inside a transaction. A transaction is like a safety envelope: either the migration work is committed together, or it can be rolled back if something goes wrong.

There is one important detail for SQLite databases. SQLite has limits around changing existing tables, so the file asks Alembic to use “batch” mode when the database dialect is SQLite. In plain terms, Alembic uses a safer table-copying approach for changes SQLite cannot do directly.

At the bottom, the file immediately runs the async setup. This means loading this migration environment starts the migration process.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function gives Alembic the live database connection and the project’s schema map, then tells it to run the migration steps. It is the moment where the migration tool is prepared with everything it needs to change the database safely.

**Data flow**: It receives an already-open database connection. It reads the database type from that connection and compares it with the project’s table metadata. It configures Alembic with those pieces, opens a transaction, runs the migration scripts, and returns nothing after the database work has been attempted.

**Call relations**: The async setup function `run` opens the connection and uses SQLAlchemy’s sync bridge to call `run_migrations`. Inside, `run_migrations` hands control to Alembic by configuring the context, starting a transaction, and then asking Alembic to run the migrations.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function prepares the database connection that migrations need. It reads Alembic’s configured database settings, opens an asynchronous SQLAlchemy connection, runs the migration work through that connection, and then closes the engine cleanly.

**Data flow**: It starts with Alembic’s configuration, especially settings whose names begin with `sqlalchemy.`. It turns those settings into an asynchronous database engine, opens a connection, passes that connection into `run_migrations`, and finally disposes of the engine so resources are released.

**Call relations**: This is the top-level async migration routine, started at the bottom of the file with `asyncio.run`. It calls SQLAlchemy’s `async_engine_from_config` to build the engine, then hands the live connection to `run_migrations`, which performs the Alembic-specific migration work.

*Call graph*: 1 external calls (async_engine_from_config).
