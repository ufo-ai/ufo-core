# Core and extension database migration commands  `stage-19.1` (cross-cutting infrastructure)

This stage is the database change control room. It runs during setup, upgrades, and sometimes rollback, before the main application can safely use the database. The tool involved is Alembic, a migration runner: it applies small ordered scripts that add, change, fill, or remove database tables and columns.

The core migration groups are the main assembly line. They start with the first platform tables for workspaces, members, agents, conversations, turns, sources, permissions, and costs. Later groups add billing, scheduling, queues, artifacts, transcripts, media usage, app visibility, listener claims, auditing, and cleanup of retired integrations. Timestamped and branch migrations keep older installations moving forward, including side histories such as Daily Brief, Sweep, and knowledge graph tables.

The extension migrations are plug-in lanes beside the core line. They create and update storage for memory, sites, skills, web chats, coding reviews, enrichment, indexing, reports, research, tests, and sample extensions.

The env.py file is the conductor. It connects Alembic to the project database and table definitions, then tells it which migration steps to run up or down.

## Sub-stages

- [Core migrations 0001-0019: foundational schema and early surfaces](stage-19.1.1.md) `stage-19.1.1` — 16 files
- [Core migrations 0020-0039: ledger, turns, scheduling, inbound queues, and seats](stage-19.1.2.md) `stage-19.1.2` — 20 files
- [Core migrations 0040-0059: permissions, pages, agents, and source grants](stage-19.1.3.md) `stage-19.1.3` — 20 files
- [Core migrations 0060-0079: admissions, artifacts, transcripts, media usage, and connections](stage-19.1.4.md) `stage-19.1.4` — 18 files
- [Core migrations 0080-0099: billing, balances, agent provisioning, and conversation metadata](stage-19.1.5.md) `stage-19.1.5` — 20 files
- [Core migrations 0100-0113: integration cleanup, ledger usage, listener claims, icons, and refs](stage-19.1.6.md) `stage-19.1.6` — 14 files
- [Core timestamped and branch migrations through 2026-08-24](stage-19.1.7.md) `stage-19.1.7` — 17 files
- [Core timestamped migrations from 2026-08-25 onward](stage-19.1.8.md) `stage-19.1.8` — 17 files
- [Memory extension migrations](stage-19.1.9.md) `stage-19.1.9` — 16 files
- [Workspace app, site, skill, source trigger, and web extension migrations](stage-19.1.10.md) `stage-19.1.10` — 20 files
- [Specialized extension storage migrations](stage-19.1.11.md) `stage-19.1.11` — 13 files

## Files in this stage

### Core and extension database migration commands
### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `database migration`

This file is used when the project needs to update its database structure, for example when a new table or column has been added. Alembic is the tool that applies these database changes, called migrations. This file is its local instruction sheet.

First, it imports the project’s database metadata, which is the master description of what tables and columns should exist. Then it defines how Alembic should run migrations once it has a database connection. It gives Alembic the live connection, points it at the project metadata, and starts a transaction, which is like putting the changes in a safe envelope so they either complete together or fail together.

The file also supports asynchronous database connections. That matters because the rest of the project may use async database access, where work can pause while waiting for the database instead of blocking everything. The `run` function builds an async database engine from Alembic’s configuration, opens a connection, runs the migration work in the synchronous style Alembic expects, and then closes the engine cleanly.

At the bottom, `asyncio.run(run())` starts the whole process when Alembic loads this file. Without this file, Alembic would not know how to connect to this project’s database or which schema definition to compare migrations against.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function performs the actual migration work once a database connection is available. It tells Alembic which connection to use, which table definitions represent the project schema, and then runs the pending migration steps safely inside a transaction.

**Data flow**: A database connection goes in. The function gives that connection and the project metadata to Alembic, turns on a SQLite-specific compatibility mode when the database is SQLite, starts a transaction, and asks Alembic to apply the migrations. Nothing is returned; the database schema may be changed.

**Call relations**: This is called by the async setup in `run` after a database connection has been opened. Inside it, Alembic’s `configure`, `begin_transaction`, and `run_migrations` functions do the concrete migration work.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function prepares the database connection needed for migrations. It reads Alembic’s database settings, creates an asynchronous SQLAlchemy engine, opens a connection, runs the migration function, and then shuts the engine down.

**Data flow**: Alembic’s configuration is read to find settings such as the database URL. Those settings are used to create an async database engine. The function opens a connection, passes it to `run_migrations` through SQLAlchemy’s sync bridge, then closes and disposes of the engine. It returns nothing.

**Call relations**: This is the top-level async migration routine, started at the bottom of the file with `asyncio.run(run())`. It calls SQLAlchemy’s `async_engine_from_config` to build the connection machinery, then hands the live connection to `run_migrations` so Alembic can apply schema changes.

*Call graph*: 1 external calls (async_engine_from_config).
