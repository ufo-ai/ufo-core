# Alembic runner and core schema baseline  `stage-1.1`

This stage is part of setting up and evolving the database, which is where the system keeps its long-term records. It uses Alembic, a tool that applies database changes in a safe order, like following numbered renovation plans for a house.

The runner file, env.py, is the bridge between Alembic and the project. It reads the project’s database setup and table definitions, then tells Alembic how to connect and apply changes.

The first migration, 0001_heartbeat.py, lays the foundation. It creates the earliest tables for workspaces, members, agents, conversations, conversation turns, identities, and usage costs. These are the basic records the rest of the system builds on.

The proposal migration, 0003_proposal.py, adds a place to store proposals, and also describes how to remove it during a rollback.

The loop-depth migration, 0004_loop_depth.py, expands conversations so one turn can contain delegated or nested turns, including work done by subagents as well as command-line users.

## Files in this stage

### Migration runner and baseline schema
Alembic is wired into the project configuration and then applies the initial core tables plus early proposal and nested-turn schema extensions.

### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `schema migration`

This file is used when the project needs to create or update its database tables. Think of it like a renovation supervisor: Alembic has the list of renovation steps, SQLAlchemy knows what the building should look like, and this file brings them together at the right address.

The file reads the database connection settings from Alembic’s configuration. It then builds an asynchronous database engine, which means it can talk to the database using Python’s async style without blocking the whole program. Once connected, it switches into a normal synchronous migration function because Alembic’s migration work expects a regular database connection.

The important project-specific piece is `metadata` from `ufo.schema.tables`. Metadata is SQLAlchemy’s map of the tables, columns, and constraints the application expects. Alembic uses it to compare or apply schema changes.

There is also a special SQLite behavior: when the database is SQLite, migrations are rendered in “batch” mode. That matters because SQLite cannot alter tables as flexibly as larger databases, so Alembic sometimes has to rebuild a table behind the scenes instead of changing it directly.

At the bottom, the file immediately runs the async migration setup. Without this file, Alembic would not know how to connect to the project’s database or which table definitions to use during migrations.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function tells Alembic how to run migrations using an already-open database connection. It also points Alembic at the project’s table metadata so schema changes are applied against the right model of the database.

**Data flow**: It receives a live SQLAlchemy `Connection`, which is an open path to the database. It gives that connection and the project’s table metadata to Alembic, chooses SQLite-friendly batch mode when needed, starts a migration transaction, and then asks Alembic to apply the pending migration steps. It does not return a value; its effect is changing the database schema if migrations are due.

**Call relations**: This function is reached from `run`, which opens the async database connection and then runs this synchronous migration step inside it. Inside the function, control is handed to Alembic through `context.configure`, `context.begin_transaction`, and `context.run_migrations`, because Alembic is the tool that actually performs the schema update work.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This async function prepares the database connection needed for migrations. It reads Alembic’s configured database settings, opens a temporary engine, runs the migration work, and then closes the engine cleanly.

**Data flow**: It starts with Alembic’s configuration section, pulls out settings such as the database URL, and uses them to create an async SQLAlchemy engine. It opens a connection, passes that connection into `run_migrations` in the form Alembic expects, then disposes of the engine so no database resources are left open. Nothing is returned; the result is that migrations have been attempted against the configured database.

**Call relations**: This function is launched when the file is executed, through the final `asyncio.run(run())` call. Its main job is setup and cleanup: it creates the engine with `async_engine_from_config`, opens the connection, hands the real migration work to `run_migrations`, and then shuts the engine down afterward.

*Call graph*: 1 external calls (async_engine_from_config).


### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration / setup`

A database migration is a repeatable recipe for changing the shape of the database. This one is the foundation: without it, the application would have nowhere to store its core records. It creates a workspace as the top-level container, then adds agents and members inside each workspace. It also records conversations, links outside identities to members, stores each message-processing “turn,” and keeps a ledger of usage costs such as token counts.

The file uses Alembic, a tool that applies database changes in order, and SQLAlchemy, a Python library for describing database tables and columns. The `upgrade` function builds the schema. It also adds safety rules, like making sure a conversation surface is currently only `cli`, a turn status is one of the allowed values, and a turn sequence number starts at 1 or higher. These rules are like guardrails: they stop bad or inconsistent data from entering the database.

The `downgrade` function is the reverse recipe. If this migration must be rolled back, it removes the index and tables in an order that respects their links to each other. This matters because some tables depend on others through foreign keys, which are database links that say one record must point to an existing record elsewhere.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Creates the first version of the application database schema. It gives the system tables for workspaces, agents, members, conversations, identity mapping, conversation turns, and usage accounting.

**Data flow**: It starts with an empty or not-yet-initialized database at this migration version. It sends table, column, key, index, and rule definitions to Alembic, which applies them to the database. After it runs, the database can store the main objects the application needs, with constraints that protect relationships and allowed values.

**Call relations**: Alembic calls this function when moving the database forward to revision `0001`. Inside it, the function asks Alembic to create each table and an index, using SQLAlchemy objects to describe column types and database rules. Later application code can rely on these tables existing.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the schema created by `upgrade`. It is used when rolling the database back before this first revision.

**Data flow**: It starts with a database that has the tables and index from this migration. It tells Alembic to drop the ledger index first, then remove the tables in dependency-safe order. After it runs, those core application tables no longer exist.

**Call relations**: Alembic calls this function when moving the database backward from revision `0001`. It hands off the actual deletion work to Alembic’s drop operations, undoing what `upgrade` created.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration teaches the database about a new kind of record called a proposal. In plain terms, a proposal seems to describe a suggested change made by an agent inside a workspace: it records what extension it concerns, what content digest it changes from and to, the proposal body itself, and whether it is still pending, approved, or rejected.

Without this file, the application could not reliably store those proposal records in the database. Other parts of the system might try to save or read proposals, but the table would not exist.

The file follows Alembic’s migration pattern. Alembic is a tool that applies database changes in order, like numbered instructions in a renovation plan. The revision is marked as `0003`, and it comes after `0002`.

The `upgrade` function creates the `proposal` table. It gives the table an ID, links each proposal to a workspace and an agent, optionally links it to the member who approved it, stores timestamps, and adds a rule that the status can only be one of three allowed words. The `downgrade` function does the reverse: it drops the table. That rollback path matters when developers need to undo this database change safely during testing or deployment.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table in the database. This is used when moving the database forward to revision `0003` so the application can store proposal records.

**Data flow**: Before this runs, the database has no `proposal` table from this migration. The function sends Alembic a table definition: column names, data types, required fields, links to other tables, a primary key, and a rule limiting proposal status values. After it runs, the database has a new `proposal` table ready to hold pending, approved, and rejected proposals.

**Call relations**: Alembic calls this function when applying this migration during an upgrade. Inside it, the function hands the full table blueprint to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, foreign keys, and constraints to describe the table clearly.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table from the database. This is used when rolling the database back from revision `0003` to the previous revision.

**Data flow**: Before this runs, the database may contain the `proposal` table created by `upgrade`. The function tells Alembic to drop that table. After it runs, the table and any data in it are gone, returning the database shape to what it was before this migration.

**Call relations**: Alembic calls this function when a rollback is requested. It delegates the actual removal to `alembic.op.drop_table`, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration changes the shape of the database so the application can record more detailed conversation history. Before this change, a turn was just a turn. After this change, a turn can point back to a parent turn, which lets the system represent nested work, like a main assistant asking a subagent to do part of a task. It also adds a text field for the subagent profile, so the database can remember what kind of subagent was involved.

The file also updates a safety rule on the conversation table. A database check constraint is like a bouncer at the door: it only lets approved values in. Previously, the conversation surface could only be 'cli', meaning command-line interface. This migration changes that rule so 'subagent' is also accepted.

There are two directions. The upgrade function applies the new schema. The downgrade function reverses it, restoring the old rule and removing the new turn fields. This matters because migrations must be reversible: developers and deployments sometimes need to roll a database backward safely.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this database change. It adds fields needed to connect a turn to a parent turn and to store subagent information, then expands the allowed conversation surface values to include subagents.

**Data flow**: It starts with the existing database schema. It adds a nullable parent_turn_id column to the turn table, adds a nullable subagent_profile text column to the same table, then replaces the conversation table's old surface rule with a new one that accepts both 'cli' and 'subagent'. The result is a database that can store nested subagent-related conversation turns.

**Call relations**: Alembic, the database migration tool, calls this when moving the database from revision 0003 to revision 0004. Inside the function, it hands the actual database changes to Alembic operations such as adding columns and altering the conversation table, while SQLAlchemy supplies the column type descriptions.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration. It removes the subagent-related database fields and restores the older rule that only allows command-line conversations.

**Data flow**: It starts with the upgraded schema. It changes the conversation surface check constraint back so only 'cli' is valid, then drops the subagent_profile and parent_turn_id columns from the turn table. The result is a database shaped like it was before this migration was applied.

**Call relations**: Alembic calls this when rolling the database back from revision 0004 to revision 0003. It uses Alembic's table-altering and column-dropping operations to undo the work done by upgrade in the safest ordered way.

*Call graph*: 2 external calls (batch_alter_table, drop_column).
