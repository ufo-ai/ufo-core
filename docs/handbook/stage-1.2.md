# Database migration entrypoints and baseline core schema  `stage-1.2`

This stage is the database “ground floor” for the system. It is mostly used during startup and deployment, before the main application work begins. Its job is to make sure the PostgreSQL database has the right tables in place, like checking that a workshop has the needed shelves and ledgers before people start using it.

The Alembic runner in `env.py` is the migration engine. Alembic is the tool that applies database change scripts in order. This file connects to the configured database, compares it with the known table definitions, and runs any missing steps.

The first migration, `0001_heartbeat.py`, creates the core tables for the application’s early records: workspaces, people, agents, conversations, conversation turns, and usage charges. Later, `0003_proposal.py` adds a proposals table and also defines how to undo that change if needed.

The control gateway has its own readiness check in `schema.py`. It does not build tables during live traffic. Instead, it verifies that its required ledgers already exist before the gateway starts serving requests.

## Files in this stage

### Migration entrypoints
Database startup and migration runner code ensure the control gateway sees existing ledgers and Alembic can apply schema changes against the configured database.

### `control/src/ufo_control/schema.py`

`orchestration` · `startup and deployment migration`

This file is the database “setup checklist” for the control service. The service depends on several ledger tables: a main gateway store, invite records, and Slack connection records. Without these tables, the gateway could not reliably answer requests or remember what has happened.

The important idea is that table creation is done deliberately, before gateway replicas start serving traffic. Creating database objects can look safe with “if not exists,” but two processes doing it at the same time can still trip over each other inside PostgreSQL. This file avoids that race by taking a PostgreSQL advisory lock, which is like putting a “one person at a time” sign on the setup work. If a second migrator arrives, it waits, then sees the work is already done.

The file has two public jobs. `shape_control_schema` is used by the migration command. It opens the database, starts one transaction, takes the lock, fixes an old invite-table shape if needed, and runs all schema and table creation statements. `require_control_schema` is used by the gateway before serving. It only checks that the required ledgers exist. If one is missing, it refuses to start and tells the operator to run the migration command. This keeps request-time behavior predictable: serving traffic never secretly changes the database layout.

#### Function details

##### `shape_control_schema`  (lines 41–59)

```
async def shape_control_schema(dsn: str) -> None
```

**Purpose**: This function brings the control database schema up to the expected shape. It is meant for the migration step before the gateway starts, so live gateway replicas do not try to create or change tables while handling requests.

**Data flow**: It receives a database connection string. It opens a PostgreSQL connection through `asyncpg`, starts a transaction, takes a database lock so only one schema shaper can run at a time, checks whether an old invite table is missing the object-number column, and drops that old invite table if necessary because those old invite codes cannot be honored safely. It then runs the schema and table creation statements. When finished, it closes the connection and returns nothing; the database is the thing that has changed.

**Call relations**: This is the migration-side half of the file’s design. It calls `asyncpg.connect` to reach the database, then uses SQL statements collected from the gateway store, invite, and Slack connection modules. It is intended to be called by the `ufo-control migrate` path before gateway replicas start, giving `require_control_schema` something complete to verify later.

*Call graph*: 1 external calls (connect).


##### `require_control_schema`  (lines 62–71)

```
async def require_control_schema(dsn: str) -> None
```

**Purpose**: This function checks that the required control ledgers already exist before the gateway serves traffic. It does not create anything; it fails fast with a clear message if migration has not been run.

**Data flow**: It receives a database connection string. It opens a PostgreSQL connection through `asyncpg`, asks PostgreSQL whether each expected ledger table is registered, and stops with a `RuntimeError` if any table is absent. If all tables are present, it closes the connection and returns normally without changing the database.

**Call relations**: This is the gateway-startup half of the file’s design. It calls `asyncpg.connect` to inspect the database, then checks the ledger table names gathered in `LEDGERS`. It depends on `shape_control_schema` having been run earlier by the migration command; if that did not happen, it refuses to let the gateway continue.

*Call graph*: 1 external calls (connect).


### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `schema migration`

This file is the bridge between the project’s database table definitions and Alembic, the tool that updates a database schema over time. Without it, migration commands would not know how to connect to the database or what schema they are trying to reach.

The file starts by importing the project’s table metadata, which is SQLAlchemy’s description of the tables, columns, and relationships the application expects. When Alembic runs this script, it reads the database connection settings from Alembic’s configuration, creates an asynchronous database engine, opens a connection, and then runs the actual migration work through that connection.

A small but important detail is the special SQLite behavior. SQLite has limits around changing tables in place, so the file tells Alembic to use “batch” mode for SQLite. In plain terms, that means Alembic may rebuild a table safely behind the scenes instead of trying to alter it directly.

At the bottom, the file immediately starts the asynchronous migration runner. So this is not a library module waiting for other project code to call it; it is the script Alembic executes during migration commands.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function performs the migration work using an already-open database connection. It tells Alembic which database connection to use, what the desired schema looks like, and then runs the pending migration operations inside a transaction.

**Data flow**: It receives a live SQLAlchemy database connection. It passes that connection and the project’s table metadata into Alembic’s setup step, chooses SQLite-safe batch mode when the database is SQLite, starts a transaction, and asks Alembic to run the migrations. It does not return a value; the database schema is the thing that changes.

**Call relations**: The asynchronous runner opens the database connection and hands it to this function through SQLAlchemy’s sync bridge. Once called, this function hands control to Alembic: first to configure the migration context, then to open a transaction, and finally to execute the migration scripts.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function sets up the database connection for migrations in an asynchronous environment. It reads Alembic’s configured database settings, creates an async engine, opens a connection, runs the migration function, and then cleans up the engine.

**Data flow**: It starts with configuration stored in Alembic’s context, especially settings whose names begin with `sqlalchemy.`. It turns those settings into an asynchronous database engine, opens one connection, uses that connection to run the synchronous migration function safely, then closes and disposes of the engine. It returns nothing; its result is that migrations have been attempted and resources have been released.

**Call relations**: This is the top-level migration runner in the file. The module starts it with `asyncio.run`, so Alembic’s execution of this file begins here. It creates the engine using SQLAlchemy’s `async_engine_from_config`, then delegates the actual schema update work to `run_migrations` once a connection is available.

*Call graph*: 1 external calls (async_engine_from_config).


### Core schema migrations
Initial Alembic revisions establish the baseline application tables and then extend them with proposal storage.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration / setup`

This file is like the first blueprint for the project’s database. Without it, a fresh database would have nowhere to store the basic records the system needs: which workspace something belongs to, who the member is, what agent is replying, what conversation is happening, and what each interaction costs.

It uses Alembic, a database migration tool, together with SQLAlchemy, a Python library for describing database tables. A migration is a controlled change to the database structure. The `upgrade` function builds the schema from nothing. It creates a `workspace` table first, then tables that depend on it, such as `agent`, `member`, and `conversation`. It also records surface identities, meaning external user identifiers for a particular place where the system is used, currently limited to `cli`. Conversation work is stored as `turn` rows, with checks that keep statuses and sequence numbers valid. Finally, `ledger` records token usage and pricing for a turn.

The file also defines the reverse path. The `downgrade` function removes the index and tables in the opposite order, so dependent tables are dropped before the tables they point to. The many constraints are important: they act like guardrails, stopping impossible or inconsistent data from being saved.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Creates the initial database structure for the application. Someone runs this when setting up or updating a database so the application has the tables and rules it expects.

**Data flow**: It takes no direct input from the application. It reads the migration instructions written in this file, then asks Alembic to create tables, columns, primary keys, foreign keys, uniqueness rules, check rules, and one index. After it runs, the database has the first usable schema for workspaces, agents, members, conversations, turns, and ledger entries.

**Call relations**: Alembic calls this function when moving the database forward to revision `0001`. Inside, it hands each table definition to Alembic operations such as table and index creation, using SQLAlchemy objects to describe column types and constraints in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Removes everything created by this migration. Someone would use it when rolling the database back before this first schema existed, usually during development or controlled rollback.

**Data flow**: It takes no direct input from the application. It tells Alembic to drop the ledger index first, then drops the tables in an order that respects dependencies. After it runs, the database no longer contains the schema introduced by this migration.

**Call relations**: Alembic calls this function when moving the database backward from revision `0001`. It uses Alembic’s drop operations to undo the work of `upgrade`, carefully removing dependent tables before the base `workspace` table they refer to.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration introduces the database storage for “proposals.” A proposal appears to be a suggested change or action made by an agent inside a workspace, with enough information to track what it changes, who approved it, and whether it is still pending, approved, or rejected. Without this file, the application would not have a dedicated place in the database to save proposal records.

The file is used by Alembic, the project’s database migration tool. A migration is like a set of building instructions for the database: when the application schema moves from version 0002 to version 0003, Alembic runs the forward instructions here.

The new `proposal` table has an `id` as its main identifier, links each proposal to a workspace and an agent, stores the extension involved, records a before-and-after digest, keeps the proposal body as JSON data, and tracks timestamps. It also includes an optional `approved_by` field that points to a member only when someone has approved the proposal. A check constraint limits the status to three allowed words: `pending`, `approved`, or `rejected`. That prevents accidental invalid statuses from being written into the database.

The rollback path is deliberately simple: if this migration is undone, the whole `proposal` table is dropped.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table in the database. This is used when the database schema is being upgraded to include proposal storage.

**Data flow**: Before this runs, the database has no `proposal` table from this migration. The function sends Alembic a full table definition: column names, data types, required fields, relationships to other tables, the primary key, and the allowed status values. After it runs, the database can store proposal rows linked to workspaces, agents, and optionally approving members.

**Call relations**: Alembic calls this function when applying revision `0003` after revision `0002`. Inside it, the function hands the table blueprint to `alembic.op.create_table`, using SQLAlchemy objects to describe each column, foreign key, primary key, and status rule.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table from the database. This is used when rolling the schema back from this migration.

**Data flow**: Before this runs, the database may contain the `proposal` table created by the upgrade. The function tells Alembic to drop that table. After it runs, the table and its stored proposal data are gone from the schema.

**Call relations**: Alembic calls this function when undoing revision `0003`. It delegates the actual removal to `alembic.op.drop_table`, which performs the database-level table deletion.

*Call graph*: 1 external calls (drop_table).
