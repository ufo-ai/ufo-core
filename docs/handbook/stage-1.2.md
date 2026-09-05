# Core and extension schema upgrades or rollbacks  `stage-1.2`

This stage is the database renovation path for the whole system. It runs during install, upgrade, or rollback, not during normal chat work. Alembic, the migration tool, applies the steps in order. env.py is the foreman: it connects to the database, checks the current table definitions, and runs changes safely. 0001_heartbeat.py lays the first foundation: workspaces, members, agents, conversations, turns, identities, and usage costs. 0006_ext_store.py adds a shared JSON storage drawer for extensions.

The core migration groups then reshape the main product shelves: turns and subagents, conversation metadata, scheduling, message delivery, sources and pages, shared artifacts, workspace membership, grants and credentials, agent settings, model and tool rewrites, billing, retired-provider cleanup, and old iMessage cleanup. Extension groups do the same for optional features: Daily Brief, memory, search and research, skill creation, notifications, code review, Sites, monitors, objectives, reports, scheduled pauses, test utilities, samples, and old web chat data. Together they let old installations keep their history while the current code gets the tables and fields it expects.

## Sub-stages

- [Core turn execution, subagent, and authority migrations](stage-1.2.1.md) `stage-1.2.1` — 20 files
- [Core conversation metadata migrations](stage-1.2.2.md) `stage-1.2.2` — 6 files
- [Core scheduling and runtime-instance migrations](stage-1.2.3.md) `stage-1.2.3` — 12 files
- [Core surface, inbound, and delivery migrations](stage-1.2.4.md) `stage-1.2.4` — 10 files
- [Core source, page, and knowledge-storage migrations](stage-1.2.5.md) `stage-1.2.5` — 13 files
- [Core shared artifact and object-history migrations](stage-1.2.6.md) `stage-1.2.6` — 9 files
- [Core workspace and member lifecycle migrations](stage-1.2.7.md) `stage-1.2.7` — 8 files
- [Core grants, connections, credentials, and access migrations](stage-1.2.8.md) `stage-1.2.8` — 10 files
- [Core agent configuration and ownership migrations](stage-1.2.9.md) `stage-1.2.9` — 13 files
- [Core agent app, model, and tool-policy rewrites](stage-1.2.10.md) `stage-1.2.10` — 14 files
- [Core ledger, billing, balance, and usage migrations](stage-1.2.11.md) `stage-1.2.11` — 19 files
- [Core retired provider and source cleanup migrations](stage-1.2.12.md) `stage-1.2.12` — 4 files
- [Core iMessage extension-store cleanup migrations](stage-1.2.13.md) `stage-1.2.13` — 3 files
- [Daily Brief and Sweep migration history](stage-1.2.14.md) `stage-1.2.14` — 4 files
- [Memory extension migrations](stage-1.2.15.md) `stage-1.2.15` — 16 files
- [Search, source-trigger, enrichment, and research extension migrations](stage-1.2.16.md) `stage-1.2.16` — 7 files
- [Skill-create extension migrations](stage-1.2.17.md) `stage-1.2.17` — 4 files
- [Notification and coding workflow extension migrations](stage-1.2.18.md) `stage-1.2.18` — 7 files
- [Sites extension migrations](stage-1.2.19.md) `stage-1.2.19` — 8 files
- [Monitor, objective, report, and scheduled-pause extension migrations](stage-1.2.20.md) `stage-1.2.20` — 6 files
- [Small utility and compatibility extension migrations](stage-1.2.21.md) `stage-1.2.21` — 4 files

## Files in this stage

### Migration orchestration
Alembic entry-point configuration connects to the project database, compares metadata, and runs schema changes safely.

### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `database migration startup`

When the project needs to upgrade or change its database layout, Alembic is the tool that runs those changes. This file is Alembic’s local instruction sheet. Without it, Alembic would not know which database tables belong to this project, how to open the database connection, or how to run migrations in an async SQLAlchemy setup.

The file imports the project’s shared table metadata, which is the map of what the database is supposed to look like. It then defines a small synchronous migration step, `run_migrations`, because Alembic itself expects to do the actual migration work on a normal database connection. Around that, `run` creates an asynchronous database engine from Alembic’s configuration, opens a connection, and asks SQLAlchemy to run the synchronous migration step safely inside that async connection.

One important detail is the SQLite setting: when the database is SQLite, migrations are rendered in “batch” mode. That is a compatibility trick Alembic uses because SQLite cannot directly perform some table-altering operations that larger databases support. At the bottom, the file immediately starts `run`, so simply loading this migration environment kicks off the migration process.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function tells Alembic exactly how to run the database migration once a database connection is available. It connects Alembic to the project’s table metadata and then runs the pending migration steps inside a transaction, which is a safe wrapper that can commit or roll back the work as one unit.

**Data flow**: It receives an open database connection. It gives Alembic that connection, the project’s expected table layout, and a SQLite-specific compatibility flag. Then it opens a migration transaction and asks Alembic to apply the migrations. It does not return a value; its effect is changing the database schema if migrations are pending.

**Call relations**: This is the inner migration worker. The async `run` function opens the database connection first, then hands that connection to `run_migrations`. Inside, `run_migrations` calls Alembic’s configuration, transaction, and migration-running steps in the order needed to apply schema changes safely.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function prepares the database connection used for migrations. It reads Alembic’s database settings, builds an asynchronous SQLAlchemy engine, opens a connection, runs the migration work, and then closes the engine cleanly.

**Data flow**: It reads the configured Alembic settings, especially the SQLAlchemy database connection options. From those settings it creates an async database engine with no long-lived connection pool. It opens one connection, runs `run_migrations` through SQLAlchemy’s bridge from async code to sync code, then disposes of the engine. Nothing is returned; the visible result is that migrations have been attempted against the configured database.

**Call relations**: This is the outer driver for the file. When the migration environment is loaded, the file starts `run` through Python’s async event loop. `run` uses SQLAlchemy’s async engine factory to get connected, then hands off the actual Alembic migration work to `run_migrations`.

*Call graph*: 1 external calls (async_engine_from_config).


### Versioned schema changes
Initial core tables are created first, followed by extension-owned storage for per-workspace JSON data.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration/setup`

This file is a database change script. It tells Alembic, the tool used to apply database migrations, how to build the project’s first database layout and how to undo it if needed. Think of it like the first blueprint for a filing system: it decides which filing cabinets exist, what labels each drawer must have, and which records are allowed to point to which other records.

The migration creates a workspace table first, because most other records belong to a workspace. It then adds agents, members, conversations, surface identities, turns, and a ledger. These names describe the core shape of the app: a workspace contains members and agents; members take part in conversations; conversations contain turns; turns can produce usage records in the ledger.

The file also adds rules that protect the data from becoming inconsistent. For example, agent names and member emails must be unique within a workspace. A conversation surface is currently limited to `cli`, meaning command-line use. A turn must have a valid status, a positive sequence number, and terminal result data only when it is no longer queued or running. The ledger records token usage and pricing, and it requires positive usage amounts.

Without this file, a fresh database would not know where to store the application’s main records, and later code that expects these tables would fail.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Builds the initial database schema. Someone runs this when setting up a new database or moving an empty database to the first version of the application’s expected structure.

**Data flow**: It starts with an empty or older database state. It sends table and index creation instructions to Alembic, using SQLAlchemy objects to describe columns, data types, primary keys, foreign keys, uniqueness rules, and safety checks. After it runs, the database contains the core tables: workspace, agent, member, conversation, surface_identity, turn, and ledger, plus an index that helps look up ledger rows by turn.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands each table definition to Alembic’s table-creation operation, and finally asks Alembic to create the ledger index. Later application code relies on the structure created here when it stores or reads workspaces, conversations, turns, and usage records.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the database objects created by `upgrade`. This is used if the database needs to be rolled back before this first schema version.

**Data flow**: It starts with a database that has the tables and index from this migration. It first removes the ledger index, then drops the tables in an order that respects their links to each other, so dependent tables are removed before the tables they point to. After it runs, the database no longer has this initial application schema.

**Call relations**: Alembic calls this function when rolling the migration back. It delegates the actual removal work to Alembic’s drop operations. It mirrors `upgrade` in reverse, acting like an undo button for the schema created in this file.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration during deploy or schema setup`

This migration changes the shape of the database. It creates an `ext_store` table, which is like a labeled storage box for each workspace. An extension can store a value under its own name and a key, and the value is saved as JSON, meaning it can hold structured data such as strings, numbers, lists, or small objects.

The table is tied to the existing `workspace` table through `workspace_id`, so every stored extension value belongs to a real workspace. Its primary key is made from three parts: the workspace, the extension name, and the key. That means the same extension can use the same key in different workspaces, and different extensions can use the same key without colliding. It also means there can only be one value for a given workspace-extension-key combination.

The timestamps `created_at` and `updated_at` record when a value was first made and last changed. Without this migration, any code expecting extensions to persist their own workspace-specific settings or state in `ext_store` would fail because the table would not exist.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Adds the `ext_store` table to the database. This is used when moving the database forward to this version of the application schema.

**Data flow**: The migration runner starts with a database that does not yet have `ext_store`. This function describes the new table: its workspace link, extension name, key, JSON value, timestamps, foreign key, and combined primary key. After it runs, the database has a new table ready for extension-owned workspace data.

**Call relations**: When Alembic, the database migration tool, applies this revision, it calls `upgrade`. Inside, the function hands the table definition to Alembic’s table-creation operation, using SQLAlchemy building blocks to describe each column and rule in a database-neutral way.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table from the database. This is used if the schema needs to be rolled back to the earlier version.

**Data flow**: The migration runner starts with a database that includes `ext_store`. This function tells the database to drop that table. After it runs, all data stored in `ext_store` is gone and the schema no longer includes that extension storage area.

**Call relations**: When Alembic rolls this revision back, it calls `downgrade`. The function delegates the work to Alembic’s table-dropping operation, which reverses the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).
