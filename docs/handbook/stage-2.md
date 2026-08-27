# Database Migration and Schema Upgrade/Rollback  `stage-2`

This stage is the database upgrade checkpoint, usually run during install or deployment before the main application does its work. It uses Alembic, a tool that runs ordered database change scripts called migrations. These scripts add, reshape, or remove tables while keeping existing data usable, and many include rollback steps to undo changes if a deployment must be reversed.

The env.py file is the runner. It connects to the database, compares it with the expected layout, and applies missing migrations. The first migration creates the basic filing cabinets: workspaces, users, agents, conversations, turns, and usage costs. Later direct migrations add proposals and a shared extension data store.

The sub-stages then fill out the rest of the system’s durable memory. Core migrations cover conversations, routing, runtime workers, sources, tasks, billing, members, permissions, agents, visibility, and cleanup of retired features. Memory and indexing migrations support search and stored knowledge. Extension migrations add long-term tables for coding, evaluations, sites, skills, monitors, reports, research, sample data, schedules, and web metadata. Together they keep old data moving safely into each new product shape.

## Sub-stages

- [Core Conversation, Turn, Inbound Message, and Artifact Migrations](stage-2.1.md) `stage-2.1` — 27 files
- [Core Runtime, Surface Routing, Listener, and Turn Delivery Migrations](stage-2.2.md) `stage-2.2` — 16 files
- [Core Source, Page, and Scheduled Task Migrations](stage-2.3.md) `stage-2.3` — 16 files
- [Core Legacy Extension and Daily Brief Cleanup Migrations](stage-2.4.md) `stage-2.4` — 8 files
- [Core Billing, Ledger, Seats, and Balance Migrations](stage-2.5.md) `stage-2.5` — 21 files
- [Core Agents, Members, Grants, and Connections Migrations](stage-2.6.md) `stage-2.6` — 24 files
- [Core Agent Visibility, Icons, Archiving, and Built-In App Migrations](stage-2.7.md) `stage-2.7` — 13 files
- [Memory, Knowledge Graph, and Indexing Migrations](stage-2.8.md) `stage-2.8` — 21 files
- [Extension Workflow, Integration, Trigger, and Web Migrations](stage-2.9.md) `stage-2.9` — 17 files
- [Sites and Skill-Creation Extension Migrations](stage-2.10.md) `stage-2.10` — 12 files

## Files in this stage

### Migration Entrypoint
The Alembic environment configures the async database connection and runs pending schema migrations.

### `core/src/ufo/schema/migrations/env.py`

`entrypoint` · `schema migration`

This file is used when the project needs to update its database structure, for example when a new table or column has been added. Alembic is the tool that compares and applies database migration scripts. Think of it like a building inspector with a checklist: this file gives Alembic access to the building, shows it the blueprint, and tells it to carry out the approved changes.

The file imports the project’s database metadata, which is the Python description of the expected tables. When migrations run, it creates a database engine from Alembic’s configuration. The engine is asynchronous, meaning it can work with modern async database drivers, but Alembic’s migration operations themselves run in a synchronous style. The file bridges that gap by opening an async connection and then running the migration work through that connection in the format Alembic expects.

One important detail is the SQLite setting. SQLite has limited support for directly changing tables, so the file enables Alembic’s “batch” mode for SQLite. In plain terms, that lets Alembic rebuild tables safely when a normal alteration would not work. Without this file, the project would not have the glue needed to run schema migrations against its configured database.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function tells Alembic how to run migrations using an already-open database connection. It gives Alembic the project’s table blueprint and starts a migration transaction so changes are applied as one safe unit.

**Data flow**: It receives a live SQLAlchemy database connection. It passes that connection and the project table metadata into Alembic, turns on SQLite-friendly batch behavior when the database is SQLite, then starts a transaction and asks Alembic to run the pending migrations. It does not return a value; its effect is that database schema changes may be applied.

**Call relations**: This is the actual migration step. The async setup function opens the connection first, then this function is run inside that connection. Inside it, Alembic is configured, a migration transaction is begun, and Alembic is told to execute the migration scripts.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This asynchronous function sets up the database connection needed for migrations. It reads Alembic’s database settings, creates an async database engine, runs the migration work, and then closes the engine cleanly.

**Data flow**: It reads the current Alembic configuration section, especially settings prefixed with `sqlalchemy.`. From those settings it builds an asynchronous SQLAlchemy engine, opens a connection, uses that connection to run the migration function, and finally disposes of the engine so database resources are released. It returns nothing; its result is a completed migration run or an error if migration fails.

**Call relations**: This is the top-level flow for the file. The module starts it with `asyncio.run`, so when Alembic loads this environment file, `run` creates the engine using SQLAlchemy’s async engine builder, hands the open connection to `run_migrations`, and then performs cleanup.

*Call graph*: 1 external calls (async_engine_from_config).


### Core Schema Revisions
The versioned migrations create the initial application schema and add durable tables for proposals and extension-specific stored data.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database setup and schema migration`

This file is like the first blueprint for the system’s database. A database migration is a scripted change to the database structure, so every environment can build the same tables in the same order. Without this file, the application would not have a place to store its basic records: which workspace exists, which members belong to it, which agents can answer, which conversations are happening, and how much usage each turn costs.

The migration starts with a workspace table, then builds outward from there. Agents and members belong to a workspace. Conversations also belong to a workspace and are tied to a member. Surface identities connect an outside identity, currently only from the command-line interface surface called "cli", back to a member. Turns record each step in a conversation: the incoming text, the agent used, the order number, and whether the turn is queued, running, finished, failed, or cancelled. The ledger table records billable usage for a turn, currently token usage priced in micro-dollars.

The file also adds rules directly in the database. For example, turn sequence numbers must be positive, ledger amounts must be greater than zero, and completed turns must have terminal result data while queued or running turns must not. These rules act like guardrails, stopping bad data before it can be saved.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Builds the initial database structure for the application. It creates all core tables, links them with foreign keys, and adds rules that keep stored data consistent.

**Data flow**: It takes no direct application input. When Alembic, the database migration tool, runs this function, it sends table-creation commands to the database: first workspace, then related tables such as agent, member, conversation, surface_identity, turn, and ledger. The result is a database that now has the project’s starting schema, including constraints, uniqueness rules, and an index for quickly finding ledger rows by turn.

**Call relations**: Alembic calls this function when moving the database forward to revision 0001. Inside it, the function hands each table definition to Alembic’s operation helpers, which translate the Python schema description into real database changes. Later application code depends on these tables existing before it can store conversations, agents, members, turns, or usage records.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the tables and index created by upgrade. Someone would use it when rolling the database back before revision 0001, usually during development or recovery.

**Data flow**: It takes no direct application input. When run, it first drops the ledger index, then removes the tables in reverse dependency order so that linked tables are deleted before the tables they point to. The result is a database with this initial schema removed.

**Call relations**: Alembic calls this function when moving the database backward from revision 0001. It uses Alembic’s drop helpers to undo what upgrade created, carefully starting with dependent data like ledger and turn before removing foundational data like workspace.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration is part of the project’s database history. A database migration is like a written instruction sheet for changing the shape of the database in a safe, repeatable way. Here, the change is to create a `proposal` table.

The table records proposals connected to a workspace and an agent. Each proposal stores an `extension`, a starting digest and ending digest, a JSON `body` for the proposal contents, and a `status`. The status is limited by a database rule to only three allowed values: `pending`, `approved`, or `rejected`. This matters because it prevents accidental or inconsistent states, such as saving a proposal with a misspelled status.

The table also tracks who approved a proposal, if anyone, plus creation and update timestamps. Foreign key links connect each proposal back to existing `workspace`, `agent`, and `member` records. A foreign key is a database rule that says “this value must point to a real row over there,” which helps keep data from becoming orphaned or meaningless.

Without this file, newer versions of the application would not have the database table they expect for proposal data, and code that stores or reads proposals would fail.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table when the database is moved forward to this migration. This is used during deployment or setup so the application has the storage it needs for proposal records.

**Data flow**: Before this runs, the database has no `proposal` table from this migration. The function sends Alembic, the database migration tool, a full table definition: columns, required fields, allowed status values, links to other tables, and the primary key. After it runs successfully, the database contains a `proposal` table ready to store proposal data.

**Call relations**: Alembic calls this function when applying revision `0003` after revision `0002`. Inside it, the function hands the table blueprint to `alembic.op.create_table`, using SQLAlchemy objects to describe each column and rule in a database-independent way.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table when rolling the database back before this migration. This lets developers or deployment tools undo the schema change if they need to return to the previous database version.

**Data flow**: Before this runs, the database may contain the `proposal` table created by `upgrade`. The function tells Alembic to drop that table. After it runs, the table and its stored proposal rows are gone from the database.

**Call relations**: Alembic calls this function when reversing revision `0003`. It delegates the actual removal to `alembic.op.drop_table`, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration changes the database structure. Its job is to create a new table called `ext_store`, which acts like a labeled storage shelf for extensions. An extension can save a value for a particular workspace using a text key, and that value can be flexible JSON data, meaning it can hold structured information such as objects, lists, strings, or numbers.

The table is tied to the existing `workspace` table through `workspace_id`, so stored extension data belongs to a real workspace. The combination of `workspace_id`, `extension`, and `key` is the table’s primary key. In plain terms, that means one extension cannot accidentally create two different rows for the same key inside the same workspace. It is like saying: in this workspace, for this extension, this label points to exactly one saved value.

The table also records when each entry was created and last updated. Without this migration, the application would have no standard database place for extensions to persist their own per-workspace data. The `downgrade` function reverses the change by removing the table, which is useful if the database needs to be rolled back to an earlier version.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `ext_store` table. It is used when moving the database forward to a version that supports saved extension data.

**Data flow**: Before it runs, the database has no `ext_store` table. The function defines the table’s columns, including the workspace it belongs to, the extension name, the storage key, the JSON value, and timestamps. After it runs, the database has a new table where extensions can store one value per workspace, extension, and key combination.

**Call relations**: Alembic, the database migration tool, calls this function when upgrading to this revision. Inside the function, it asks Alembic to create the table and uses SQLAlchemy building blocks to describe the table’s columns, link to the workspace table, and uniqueness rule.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `ext_store` table. It is used when rolling the database schema back to an earlier version.

**Data flow**: Before it runs, the database may contain the `ext_store` table and any data stored in it. The function tells the migration system to drop that table. After it runs, the table and its contents are gone, so extension storage from this table is no longer available.

**Call relations**: Alembic calls this function during a rollback from this revision. It hands the work directly to Alembic’s table-dropping operation, because the rollback is simply the opposite of creating the table.

*Call graph*: 1 external calls (drop_table).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-database-schema-version` — The record of which database upgrades have already been applied and what storage shape the system expects.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-extension-data-store` — Durable extension-scoped key/value or JSON state used by installed extensions beyond their manifest capabilities and lockfile selection.
- `reg-proposal-state` — Durable proposed-change records, including pending, approved, or rejected prompt/config/self-improvement proposals and their before/after payloads.
- `reg-evaluation-run-store` — Durable evaluation test cases, replay runs, comparison results, and self-improvement validation state used to accept or reject changes.
