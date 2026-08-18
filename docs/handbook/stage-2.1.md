# Database preparation runners and Alembic entrypoints  `stage-2.1`

This stage is the database “make sure the ground is ready” step. It is used by operators or startup jobs before the service begins normal work, so the application does not run against missing or outdated tables. The database schema is the layout of the database: which tables and columns exist, like the floor plan of a warehouse.

The UFO control gateway has its own preparation file, control/src/ufo_control/schema.py. It creates the PostgreSQL tables the gateway needs, upgrades them when the expected layout changes, and checks that the database is safe to use. This protects multiple gateway replicas from accepting traffic while the shared database is not ready.

The core project uses core/src/ufo/schema/migrations/env.py as its Alembic entrypoint. Alembic is the tool that applies database migrations, which are step-by-step schema changes. This file tells Alembic how to connect to the project database and where to find the project’s table definitions, so migrations run in the right place and in the right order.

## Files in this stage

### Schema preparation entrypoints
Operator-facing database preparation code first protects the UFO control gateway schema, then delegates project-wide migration execution to Alembic.

### `control/src/ufo_control/schema.py`

`orchestration` · `deploy migration and gateway startup`

This file is the database “blueprint and building inspector” for the control gateway. The gateway depends on several ledgers, meaning database tables that record invites, signup claims, and Slack Connect delivery work. If those tables are missing or have the wrong columns, requests and background jobs would fail later, often one customer at a time and away from the deploy logs.

The file separates two jobs. `shape_control_schema` is the migration job: it connects to PostgreSQL, takes an advisory lock, which is a database lock used here so only one migration writer reshapes the schema at a time, then creates or updates the schema. It also knows how to move older database layouts forward. For example, old Slack delivery rows keyed by a signup claim are translated to the newer domain-based table so the system does not forget who was already invited to Slack.

`require_control_schema` is the gateway-side safety check. It does not create anything. It only verifies that the ledgers and declared columns are present, and tells the operator to run `ufo-control migrate` if not.

A notable safeguard is `HEAD_SHAPE`: the file reads the expected columns from its own create-table statements, then checks the live database against that expectation. This catches a common migration mistake: adding a column to the fresh-create path but forgetting to add an `alter table` step for existing databases.

#### Function details

##### `_definitions`  (lines 98–114)

```
def _definitions(body: str) -> list[str]
```

**Purpose**: This helper splits the inside of a SQL table definition into separate column or constraint definitions. It is careful not to split on commas that appear inside parentheses, such as in a `check` rule listing allowed values.

**Data flow**: It receives a string containing the body of a `create table` statement. It walks through the text character by character, keeping track of how deeply nested it is inside parentheses. It returns a list of definition strings, split only at top-level commas.

**Call relations**: This is a small parsing tool used by `_declared_shape`. `_declared_shape` needs clean pieces of a table definition so it can tell which column names the code says should exist.

*Call graph*: called by 1 (_declared_shape).


##### `_declared_shape`  (lines 117–136)

```
def _declared_shape(statements: tuple[str, ...]) -> dict[str, frozenset[str]]
```

**Purpose**: This function reads the file’s own `create table if not exists` statements and works out which columns each table is supposed to have. It turns the SQL blueprint into a simple table-to-columns map that later checks can compare with the real database.

**Data flow**: It receives the tuple of SQL statements. For each create-table statement, it pulls out the table name, asks `_definitions` to split the column list, skips table-level constraints such as primary keys and checks, and records the declared column names. It returns a dictionary whose keys are table names and whose values are sets of expected columns.

**Call relations**: It calls `_definitions` while building `HEAD_SHAPE`, the module-level record of the current expected database shape. That record is later used by `_drifted_columns` to detect whether a database is missing any columns this build expects.

*Call graph*: calls 1 internal fn (_definitions).


##### `_drifted_columns`  (lines 142–156)

```
async def _drifted_columns(connection: asyncpg.Connection) -> list[str]
```

**Purpose**: This function finds columns that the code expects but the live database does not have. It is the final inspection step that catches schema drift before the gateway serves traffic or before a migration is accepted.

**Data flow**: It receives an open PostgreSQL connection. It asks PostgreSQL’s information schema, a built-in catalog of tables and columns, which columns currently exist in the relevant ledgers. It compares that live list with `HEAD_SHAPE` and returns a sorted list like `schema.table.column` for every missing declared column.

**Call relations**: Both `shape_control_schema` and `require_control_schema` call this after connecting to the database. In the migration flow, it confirms that the reshape really reached the declared head version. In the gateway startup flow, it confirms that the database already matches the running build.

*Call graph*: called by 2 (require_control_schema, shape_control_schema); 1 external calls (fetch).


##### `shape_control_schema`  (lines 159–223)

```
async def shape_control_schema(dsn: str) -> None
```

**Purpose**: This is the migration routine that brings the control database schema up to the version declared by this code. It creates missing objects, updates older layouts, preserves important Slack delivery history, and refuses to finish if the result is still missing expected columns.

**Data flow**: It receives a database connection string. It opens a PostgreSQL connection with `asyncpg`, starts a transaction, takes a database advisory lock so competing migration attempts wait their turn, checks for known old layouts, and runs the needed SQL statements. If an old Slack delivery table is claim-keyed, it renames the old table aside, creates the new table, copies rows across using the related signup claim data, and then drops the old table. It then applies reshape statements and calls `_drifted_columns`; if any declared columns are still absent, it raises an error. Whether it succeeds or fails, it closes the connection.

**Call relations**: This function is the active migration path, normally run by `ufo-control migrate` before gateway replicas start. It calls `asyncpg.connect` to reach the database and `_drifted_columns` at the end as a built-in audit. It does not rely only on deployment ordering; its lock and idempotent SQL make repeated or overlapping migration attempts safe.

*Call graph*: calls 1 internal fn (_drifted_columns); 1 external calls (connect).


##### `require_control_schema`  (lines 226–245)

```
async def require_control_schema(dsn: str) -> None
```

**Purpose**: This is the startup guard for gateway replicas. It checks that the database has already been migrated and refuses to let the gateway run against absent tables or missing columns.

**Data flow**: It receives a database connection string. It connects to PostgreSQL, checks that each required ledger table exists, then calls `_drifted_columns` to look for missing expected columns. If a table or column is missing, it raises an error telling the operator to run `ufo-control migrate`; otherwise it returns normally. It always closes the connection afterward.

**Call relations**: This is the read-only counterpart to `shape_control_schema`. Gateway startup uses it when it wants proof that the migration job has already shaped the database. It calls `asyncpg.connect` for database access and hands the column-level inspection to `_drifted_columns`.

*Call graph*: calls 1 internal fn (_drifted_columns); 1 external calls (connect).


### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `database migration`

This file exists so the database can be safely moved from one version of the schema to another. A database schema is the layout of tables and columns. Alembic is the tool that applies step-by-step changes to that layout, like adding a new column or creating a new table.

The file starts by importing the project’s database metadata, which is the master description of what the tables should look like. When a migration runs, Alembic needs both a database connection and this metadata so it can understand the target structure.

The work happens in two layers. The asynchronous `run` function reads database settings from Alembic’s configuration, creates an async SQLAlchemy engine, opens a connection, and then asks SQLAlchemy to run the actual migration work in a synchronous-safe way. This matters because Alembic’s migration operations are traditionally synchronous, while this project uses an async database engine.

The `run_migrations` function then configures Alembic with the live connection and the project metadata, opens a transaction, and runs the migrations. A transaction is like a safety wrapper: the migration steps are grouped so the database is not left half-changed if something fails. There is also special support for SQLite batch mode, because SQLite has stricter limits around changing existing tables.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function performs the actual Alembic migration work on an already-open database connection. It prepares Alembic with the project’s table layout and then runs the pending schema changes inside a transaction.

**Data flow**: It receives a live SQLAlchemy database connection. It gives that connection and the project metadata to Alembic, also turning on SQLite-friendly batch behavior when the database is SQLite. Then it starts a transaction, runs the migrations, and leaves the database schema updated if the migration succeeds.

**Call relations**: This function is called by `run` through SQLAlchemy’s `run_sync` bridge, because Alembic’s migration code expects synchronous database access. Once called, it hands control to Alembic by configuring the migration context, beginning a transaction, and asking Alembic to run the migration scripts.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This asynchronous function sets up the database connection needed for migrations. It reads Alembic’s database configuration, creates an async database engine, opens a connection, runs the migration function, and then closes everything cleanly.

**Data flow**: It reads connection settings from Alembic’s configuration. From those settings it builds an async SQLAlchemy engine, opens a database connection, passes that connection into `run_migrations`, and finally disposes of the engine so no database resources are left open.

**Call relations**: This is the top-level async migration flow for the file. The module starts it with `asyncio.run(run())`, so when Alembic loads this environment file, `run` becomes the driver that creates the connection and then hands the actual migration work to `run_migrations`.

*Call graph*: 1 external calls (async_engine_from_config).
