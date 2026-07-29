# Schema migration and database bootstrapping  `stage-1`

This stage happens before the system starts doing its normal work. It prepares the database, which is the system’s long-term filing cabinet, so later services can safely read and write stored state. It uses Alembic, a migration tool that applies database changes in order, like following numbered renovation instructions.

Core database migrations build and evolve the main filing cabinets for workspaces, users, agents, conversations, turns, messages, permissions, billing, scheduling, connections, memory, and other shared records. Extension database migrations do the same for add-on features, such as evaluation test data, searchable indexes, sample tables, memories, and user-created skills.

The direct source files provide the machinery around those migrations. The database helper opens safe connections, starts transactions tied to the right workspace, and runs upgrades. The schema setup for the control gateway checks that required tables exist before traffic is served. The row-level security setup adds PostgreSQL rules that keep one workspace from seeing another workspace’s rows. Together, these pieces make sure the database is ready, current, and safely separated before runtime code depends on it.

## Sub-stages

- [Core database migrations](stage-1.1.md) `stage-1.1` — 59 files
- [Extension database migrations](stage-1.2.md) `stage-1.2` — 18 files

## Files in this stage

### Workspace isolation
Establishes PostgreSQL roles and row-level security policies that separate workspace data before application traffic begins.

### `control/src/ufo_control/rls.py`

`domain_logic` · `startup / database bootstrap`

This file is about preventing one workspace from seeing or changing another workspace’s data. PostgreSQL has a feature called row-level security, or RLS, which is like giving every table a gatekeeper: even if a user can query the table, PostgreSQL only lets through rows that match a rule. Here, the rule says: the row’s workspace identifier must match the current workspace identifier stored in the database session setting app.workspace_id.

The file does two main jobs. First, it prepares a restricted database role called ufo_serve. That is the role the application uses when serving normal traffic. Its password is derived from a secret seed, so it can be recreated predictably without storing the final password directly. The setup also grants this role the table and sequence access it needs, while leaving the actual row filtering to RLS.

Second, it walks through every public table, except Alembic’s migration version table, and makes sure the workspace policy is present and correct. If a table already has exactly the expected policy, it leaves it alone. If not, it enables RLS and recreates the policy. If another database session is blocking the table, the code fails after a short timeout and reports who appears to be holding the lock, instead of hanging forever. Without this file, the app could have permissions without the database-level workspace fence that makes those permissions safe.

#### Function details

##### `owner_dsn`  (lines 22–26)

```
def owner_dsn() -> str
```

**Purpose**: Reads the database connection string for the powerful owner account. This account is needed for setup work that the normal serving role should not be allowed to do.

**Data flow**: It looks in the process environment for UFO_CONTROL_POSTGRES_OWNER_DSN. If the value is present, it returns it. If it is missing, it raises an error so setup cannot continue without the privileged database connection it needs.

**Call relations**: This is a small entry helper for code that needs the owner database connection. It does not call other project functions; it simply turns a required environment setting into a usable connection string or a clear failure.


##### `serve_password`  (lines 29–33)

```
def serve_password() -> str
```

**Purpose**: Creates the password for the restricted PostgreSQL role used by the application. The password is derived from a secret seed instead of being typed or stored directly.

**Data flow**: It reads UFO_CONTROL_PG_ROLE_SEED from the environment. If the seed exists, it combines the seed with the fixed role name ufo_serve, hashes that text with SHA-256, and returns the hexadecimal hash as the password. If the seed is missing, it raises an error because the serving role cannot be safely created or used.

**Call relations**: ensure_serve_role calls this when creating or updating the database role, and serve_dsn calls it when building the application connection string. It hands both callers the same repeatable password so the role setup and the connection string stay in sync.

*Call graph*: called by 2 (ensure_serve_role, serve_dsn); 1 external calls (sha256).


##### `serve_dsn`  (lines 36–37)

```
def serve_dsn(postgres_host: str, app_database: str) -> str
```

**Purpose**: Builds the database connection string that the application should use for normal serving traffic. It uses the restricted ufo_serve role rather than the powerful owner account.

**Data flow**: It receives a PostgreSQL host and application database name. It asks serve_password for the role password, then combines the role name, password, host, and database name into an async PostgreSQL connection URL. The returned string is ready for code that connects through SQLAlchemy with asyncpg.

**Call relations**: This function depends on serve_password so the generated connection string matches the role created by ensure_serve_role. It is used when the system needs a safe, workspace-filtered database connection for application work.

*Call graph*: calls 1 internal fn (serve_password).


##### `ensure_serve_role`  (lines 40–62)

```
async def ensure_serve_role(admin_dsn: str) -> None
```

**Purpose**: Creates or refreshes the restricted PostgreSQL role used by the serving application. It also grants the role the basic database access it needs and prepares a companion database if it does not exist.

**Data flow**: It receives an administrator database connection string. It connects to PostgreSQL, computes the serving role password, sets a lock timeout, and then either creates ufo_serve or updates its password. It grants permission for that role to set the app.workspace_id session value, applies an idle transaction timeout to key roles, grants table and sequence access through _grant_serve_role, checks the current database name, and asks _ensure_database to create a related database if needed. Finally, it closes the connection.

**Call relations**: This is one of the main setup routines in the file. It calls serve_password to get the expected password, _grant_serve_role to apply permissions, and _ensure_database to create the extra database. It uses asyncpg to talk directly to PostgreSQL during bootstrap.

*Call graph*: calls 3 internal fn (_ensure_database, _grant_serve_role, serve_password); 1 external calls (connect).


##### `bootstrap_policies`  (lines 65–92)

```
async def bootstrap_policies(dsn: str) -> None
```

**Purpose**: Checks every public table and makes sure the workspace row-level security policy is installed correctly. This is the main routine that enforces the database-side workspace boundary.

**Data flow**: It receives a database connection string, connects to PostgreSQL, sets a short lock timeout, and fetches the names of public tables. For each table except the Alembic migration tracking table, it first asks _conformant whether the existing policy is already exactly right. If it is not, it opens a short transaction and calls _policy_for to enable RLS and recreate the policy. If a table lock times out, it asks _lock_holders who is blocking progress and raises a clearer error. It closes the database connection at the end.

**Call relations**: This is the policy bootstrap driver. It uses _conformant as the cheap check, _policy_for as the repair step, and _lock_holders as the diagnostic path when PostgreSQL cannot get the needed lock. It is meant to run during setup or migration-like startup work before serving data safely.

*Call graph*: calls 3 internal fn (_conformant, _lock_holders, _policy_for); 1 external calls (connect).


##### `_conformant`  (lines 95–123)

```
async def _conformant(connection: asyncpg.Connection, table: str) -> bool
```

**Purpose**: Checks whether one table already has the exact workspace security rule this project expects. It avoids changing the table when nothing needs to change.

**Data flow**: It receives an open database connection and a table name. It reads PostgreSQL’s catalog information for that table: whether row-level security is enabled, what the named policy says, whether the policy applies to all commands, and which roles it covers. It calls _scope_column to find which column should identify the workspace. It then compares the database’s existing policy text to the expected rule and returns true only if every important part matches.

**Call relations**: bootstrap_policies calls this before doing any policy-changing database commands. If _conformant returns true, bootstrap_policies skips the table. If it returns false, bootstrap_policies moves on to _policy_for to recreate the policy.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (fetchrow).


##### `_lock_holders`  (lines 126–141)

```
async def _lock_holders(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Builds a human-readable report of database sessions that are currently holding locks on a table. This helps explain why policy setup could not continue.

**Data flow**: It receives an open database connection and a table name. It queries PostgreSQL’s lock and activity views for other sessions holding granted locks on that table. It turns each matching session into a short text summary with the process id, database user, state, transaction age, and a trimmed version of the running query. If no holder is visible, it returns a message saying so.

**Call relations**: bootstrap_policies calls this only after PostgreSQL reports that a table lock could not be obtained in time. The returned text is included in the raised error so an operator can see what was blocking the bootstrap work.

*Call graph*: called by 1 (bootstrap_policies); 1 external calls (fetch).


##### `_grant_serve_role`  (lines 144–157)

```
async def _grant_serve_role(connection: asyncpg.Connection) -> None
```

**Purpose**: Gives the restricted serving role the ordinary access it needs to use public tables and sequences. The row-level security policies still decide which rows that role may actually see or change.

**Data flow**: It receives an open database connection. It first adjusts default privileges for future objects created by the owner role, then grants ufo_serve permission to use the public schema, read and write all current public tables, and use all current public sequences. It changes database permissions but returns no value.

**Call relations**: ensure_serve_role calls this after creating or updating the ufo_serve role. It supplies the broad table-level permissions that make normal queries possible, while bootstrap_policies supplies the row-level rules that make those permissions safe per workspace.

*Call graph*: called by 1 (ensure_serve_role); 1 external calls (execute).


##### `_ensure_database`  (lines 160–163)

```
async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None
```

**Purpose**: Creates a named PostgreSQL database if it does not already exist. This keeps setup repeatable: running it again does not recreate an existing database.

**Data flow**: It receives an open database connection, a database name, and an owner role name. It asks PostgreSQL whether a database with that name exists. If not, it creates the database and assigns the given owner. If it already exists, it does nothing.

**Call relations**: ensure_serve_role calls this near the end of role setup. After discovering the current application database name, ensure_serve_role asks this helper to make sure the related database owned by the serving role is present.

*Call graph*: called by 1 (ensure_serve_role); 2 external calls (execute, fetchval).


##### `_policy_for`  (lines 166–173)

```
async def _policy_for(connection: asyncpg.Connection, table: str) -> None
```

**Purpose**: Installs the workspace row-level security policy for one table. It is the repair step used when a table is missing the policy or has an outdated one.

**Data flow**: It receives an open database connection and a table name. It calls _scope_column to decide which column represents the workspace for that table. It builds the rule saying that the row’s workspace column must equal the session’s app.workspace_id value, enables row-level security on the table, drops any old policy with the managed name, and creates a fresh policy for both reading and writing rows.

**Call relations**: bootstrap_policies calls this inside a short transaction when _conformant says the table is not already correct. It relies on _scope_column so the workspace table itself uses id while ordinary workspace-scoped tables use workspace_id.

*Call graph*: calls 1 internal fn (_scope_column); called by 1 (bootstrap_policies); 1 external calls (execute).


##### `_scope_column`  (lines 176–190)

```
async def _scope_column(connection: asyncpg.Connection, table: str) -> str
```

**Purpose**: Decides which column should be used to tie a table’s rows to a workspace. It also rejects public tables that cannot be safely protected by the expected workspace rule.

**Data flow**: It receives an open database connection and a table name. If the table is the workspace table itself, it returns id, because each workspace row identifies itself. Otherwise, it checks whether the table has a workspace_id column. If it does, it returns workspace_id. If not, it raises an error because the table is neither the workspace table nor clearly scoped to a workspace.

**Call relations**: _conformant calls this when checking whether an existing policy has the right expression, and _policy_for calls it when building a new policy. This makes it the shared source of truth for how each table is connected to a workspace.

*Call graph*: called by 2 (_conformant, _policy_for); 1 external calls (fetchval).


### Schema readiness and database access
Prepares the gateway schema and exposes the transaction-safe database entry points that run migrations and bind work to a workspace.

### `control/src/ufo_control/schema.py`

`orchestration` · `startup and deployment migration`

This file is the gateway’s database “shape checker.” The control service depends on several ledger tables: a main gateway store, invite records, and Slack connection records. If those tables are missing, the gateway cannot safely know what invitations or connections exist, so it must refuse to run until the database is prepared.

The important idea is that database structure changes are done deliberately, once, during deployment. PostgreSQL commands like “create this table if it does not exist” sound safe, but two processes can still race: both look, both think the table is missing, and one loses with an error. To avoid that, `shape_control_schema` takes a PostgreSQL advisory lock, which is like putting a “one person at the workbench” sign on the schema update. Other migrators wait, then see the finished schema and do nothing.

The file also contains a compatibility cleanup for an older invite table shape. If the invite ledger exists but lacks the `email_domain` column, the code drops it because those old invite rows cannot be honored correctly anymore.

`require_control_schema` is the safety gate used by the gateway side. It checks that every required ledger table exists. If not, it gives a clear error telling the operator to run `ufo-control migrate` first.

#### Function details

##### `shape_control_schema`  (lines 41–59)

```
async def shape_control_schema(dsn: str) -> None
```

**Purpose**: This function brings the control database schema up to the expected current shape. It is meant to be run by the migration step before gateway replicas start, so live gateway requests never have to create or reshape database tables.

**Data flow**: It takes a database connection string as input. It opens a PostgreSQL connection, starts one transaction, takes a lock so only one schema shaper can work at a time, checks whether an old invite table format is present, drops that unusable old invite table if needed, then runs the schema and table creation statements. When finished, it closes the connection and returns nothing; the database is the thing that has changed.

**Call relations**: This function begins by asking `asyncpg.connect` to open the database connection. During deployment it is the active worker that performs the migration work described by this file, using the DDL statements collected from the gateway store, invite, and Slack connection modules.

*Call graph*: 1 external calls (connect).


##### `require_control_schema`  (lines 62–71)

```
async def require_control_schema(dsn: str) -> None
```

**Purpose**: This function checks that the required control database tables already exist. It is a guardrail for gateway startup: if the ledgers are missing, the gateway should stop with a useful message instead of serving requests against an incomplete database.

**Data flow**: It takes a database connection string, opens a PostgreSQL connection, and looks up each required ledger table by name. If every table is present, it returns normally. If any table is absent, it raises a `RuntimeError` explaining which table is missing and telling the operator to run `ufo-control migrate`; it always closes the connection afterward.

**Call relations**: This function also starts by using `asyncpg.connect` to talk to PostgreSQL. It is the checking counterpart to `shape_control_schema`: the migration function creates or updates the schema, while this function is used later by the gateway to confirm that the migration happened before it starts serving.

*Call graph*: 1 external calls (connect).


### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, teardown`

This file protects the boundary between different workspaces, meaning different tenants or customer areas that must not see each other’s data. Its main job is to make sure every normal database transaction knows which workspace it belongs to before it reads or writes anything. For PostgreSQL, it does this by setting a per-transaction database setting called a GUC, which is just a named setting inside PostgreSQL. Row-level security, or RLS, then uses that setting to decide which rows are visible. If no workspace was set, the database policy fails closed instead of accidentally showing data.

The file keeps the database engines private and exposes transactions through two main context managers. `workspace_tx` is the normal path: it opens a transaction and pins it to the current workspace. `owner_tx` is the special path for background sweeps that must list work across all workspaces; callers are expected to use it only to find identifiers, then re-enter the proper workspace before reading real contents.

It also handles practical database plumbing: building engines safely for async code, warming up the first connection to avoid event-loop deadlocks, applying Alembic migrations, and setting SQLite pragmas so local SQLite behaves more reliably. Without this file, workspace isolation, startup migration, and safe database lifecycle cleanup would be scattered and much easier to get wrong.

#### Function details

##### `_build_engine`  (lines 41–52)

```
def _build_engine(url: str) -> AsyncEngine
```

**Purpose**: Creates an asynchronous database engine from a database URL. It also adds SQLite-specific setup when needed and forces the engine to complete its first connection setup before anyone else can use it.

**Data flow**: It receives a database URL, creates an async SQLAlchemy engine without connection pooling, adds SQLite connection hooks if the database is SQLite, warms up the engine with a first connect, and returns the ready engine.

**Call relations**: Both `init_db` and `init_owner_db` call this when they publish the normal engine or the owner engine. Before returning, it hands the engine to `_first_connect` so later callers do not trip over first-use initialization from multiple event loops.

*Call graph*: calls 1 internal fn (_first_connect); called by 2 (init_db, init_owner_db); 1 external calls (create_async_engine).


##### `init_db`  (lines 55–59)

```
def init_db(url: str) -> None
```

**Purpose**: Initializes the normal application database engine. This is the engine used by workspace-scoped database work.

**Data flow**: It receives a database URL, checks that the normal engine has not already been created, builds it, and stores it in the module’s private `_engine` variable. If someone tries to initialize twice, it raises an error instead of silently replacing the engine.

**Call relations**: This is called by the application’s setup code before database transactions are used. It delegates engine creation to `_build_engine`, and later `workspace_tx` and sometimes `owner_tx` rely on the stored engine.

*Call graph*: calls 1 internal fn (_build_engine).


##### `init_owner_db`  (lines 62–71)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Initializes a special database engine for owner-level access. This engine is used only for carefully controlled cross-workspace scans.

**Data flow**: It receives an owner database URL, checks that the owner engine is not already set, builds it, and stores it in `_owner_engine`. A second initialization attempt raises an error.

**Call relations**: Startup code calls this when the process has an owner database credential. It uses `_build_engine`, and `owner_tx` later chooses this engine when it needs to enumerate work across workspaces.

*Call graph*: calls 1 internal fn (_build_engine).


##### `_first_connect`  (lines 74–94)

```
def _first_connect(engine: AsyncEngine) -> None
```

**Purpose**: Forces the database engine to perform its one-time first connection setup immediately and safely. This avoids a deadlock that can happen if two event loops try to initialize the same engine at once.

**Data flow**: It receives an engine, starts a short-lived thread, creates a private event loop there, opens and closes one connection, then returns. If that setup failed in the thread, it re-raises the same error to the caller.

**Call relations**: `_build_engine` calls this before making an engine available. Inside the helper thread, `_first_connect.run` performs the actual async open-and-close step.

*Call graph*: called by 1 (_build_engine); 1 external calls (Thread).


##### `_first_connect.run`  (lines 81–88)

```
def run() -> None
```

**Purpose**: Runs the first database connection warm-up inside a fresh thread and event loop. It exists so the warm-up works the same whether setup code is already inside an async event loop or not.

**Data flow**: It creates a new event loop, runs `_open_and_close` with the engine, records any exception in a shared list, and closes the loop afterward.

**Call relations**: This is the worker function launched by `_first_connect` through `threading.Thread`. It hands off the actual database touch to `_open_and_close`.

*Call graph*: calls 1 internal fn (_open_and_close); 1 external calls (new_event_loop).


##### `_open_and_close`  (lines 97–99)

```
async def _open_and_close(engine: AsyncEngine) -> None
```

**Purpose**: Opens one database connection and immediately closes it. Its purpose is not to do useful database work, but to trigger SQLAlchemy’s first-use initialization.

**Data flow**: It receives an async engine, enters an async connection context, does nothing inside it, and exits so the connection is closed.

**Call relations**: `_first_connect.run` calls this during engine warm-up. It uses SQLAlchemy’s `AsyncEngine.connect` operation to force the first connection path.

*Call graph*: called by 1 (run); 1 external calls (connect).


##### `dispose_db`  (lines 102–109)

```
async def dispose_db() -> None
```

**Purpose**: Shuts down any database engines created by this module. This is used during cleanup so connections and engine resources are released.

**Data flow**: It checks the normal engine and owner engine. For each one that exists, it awaits disposal, then clears the stored module variable back to `None`.

**Call relations**: Teardown or tests call this after database use is finished. It resets the state that `init_db`, `init_owner_db`, `workspace_tx`, and `owner_tx` depend on.


##### `_opened`  (lines 113–130)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a database transaction and records a metric if the transaction cannot even begin. It is the shared wrapper used by both normal workspace transactions and owner transactions.

**Data flow**: It receives an engine and a label such as `workspace` or `owner`. It tries to begin a transaction; if acquisition fails, it emits an observability metric with the path and error type, then re-raises the error. If it succeeds, it yields the open connection to the caller.

**Call relations**: `workspace_tx` and `owner_tx` call this whenever they need a transaction. It uses SQLAlchemy’s transaction begin operation and calls `ufo.o11y.emit_metric` only for failures that happen while opening the transaction.

*Call graph*: called by 2 (owner_tx, workspace_tx); 3 external calls (AsyncExitStack, begin, emit_metric).


##### `workspace_tx`  (lines 134–144)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the normal database transaction for code working inside one workspace. On PostgreSQL, it pins the transaction to the current workspace so row-level security can filter data correctly.

**Data flow**: It reads the private `_engine` and the ambient `current_workspace` context variable. It opens a transaction through `_opened`; if there is a workspace ID and the database is PostgreSQL, it sends `set_config` to store that workspace ID for this transaction. It then yields the connection for the caller’s database work.

**Call relations**: Application request, turn, or job code uses this after setting `current_workspace`. It relies on `_opened` for the transaction and SQLAlchemy text SQL for the PostgreSQL setting.

*Call graph*: calls 1 internal fn (_opened); 1 external calls (text).


##### `owner_tx`  (lines 148–161)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the special transaction used to enumerate records across workspaces. It deliberately does not set a workspace, so callers must not treat it as normal tenant-scoped access.

**Data flow**: It chooses `_owner_engine` if present, otherwise falls back to `_engine`. It verifies that an engine exists, opens a transaction through `_opened`, and yields the connection without setting any workspace value.

**Call relations**: Background sweeps use this to find units of work across workspaces. After getting identifiers, they are expected to switch back into each row’s workspace before doing scoped reads or writes.

*Call graph*: calls 1 internal fn (_opened).


##### `apply_migrations`  (lines 164–189)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Runs database schema migrations so the database tables and columns match the code. It includes both the core project migrations and any active extension migrations.

**Data flow**: It receives a database URL and optionally a pack name. It builds an Alembic configuration, asks the extension loader for migration locations, checks for duplicate migration revision IDs, and then upgrades the database to all current migration heads.

**Call relations**: Startup tools, command-line commands, or test setup call this before normal database use. It calls the extension loader for extra migration folders, validates the Alembic script graph, then hands control to Alembic’s upgrade command.

*Call graph*: 6 external calls (__init__, upgrade, from_config, migration_locations, catch_warnings, simplefilter).


##### `_sqlite_on_connect`  (lines 192–198)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Applies SQLite settings whenever a SQLite connection is opened. These settings make local SQLite safer and more predictable for this application.

**Data flow**: It receives a raw SQLite database connection, turns off the driver’s automatic transaction behavior, enables write-ahead logging, enables foreign key checks, sets a busy timeout, and closes the temporary cursor it used.

**Call relations**: `_build_engine` registers this as a SQLite connect hook. SQLAlchemy calls it automatically each time it opens a SQLite connection.


##### `_sqlite_begin_immediate`  (lines 201–203)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions with `begin immediate`, which claims the single writer slot early. This turns some writer conflicts into orderly waiting instead of late deadlocks.

**Data flow**: It receives a SQLAlchemy connection and sends the raw SQL command `begin immediate` to SQLite. It does not return a value; it changes how the transaction starts.

**Call relations**: `_build_engine` registers this as a SQLite begin hook. SQLAlchemy calls it when a SQLite transaction begins, using `exec_driver_sql` to send the command.

*Call graph*: 1 external calls (exec_driver_sql).

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-extension-store` — Per-workspace saved extension data that add-ons use to remember their own small pieces of state.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-inbound-message-state` — The saved incoming messages and surface-provided context waiting to be admitted into a conversation turn.
- `reg-skill-store` — The shared set of built-in, extension-provided, and user-created skills available to agents.
- `reg-connector-connections` — The saved external accounts, OAuth connections, and agent grants that let tools use outside services safely.
- `reg-source-sync-state` — The saved state of external sources, synced pages, deletion markers, cursors, and retry backoff.
- `reg-search-index` — The shared searchable index and chunk store built from synced or fetched content.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-artifact-storage` — The shared file and blob storage for generated artifacts, plus the signed download state used to protect them.
- `reg-surface-installations` — The saved Slack, web, terminal, and other surface bindings used to receive messages and send replies back.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-onboarding-claims` — The hosted signup state for email claims, invitations, company-domain workspace mapping, and temporary access tokens.
- `reg-proposal-governance` — The saved proposals and safety checks used to govern prompt or system improvements before applying them.
- `reg-database-connection-pool` — The shared database engine, session factory, connection pool, and transaction context reused by migrations, request handlers, workers, and background jobs.
- `reg-evaluation-replay-state` — Saved evaluation corpora, replay fixtures, prompt-candidate runs, scores, and comparison results used before self-improvement proposals are governed.
- `reg-evaluation-environment-state` — Persisted synthetic evaluation-environment data, such as fake email and calendar records, used by evaluation connectors and replay/test workflows without touching real external services.
