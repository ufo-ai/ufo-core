# Database Migration and First-Run Bootstrapping  `stage-3`

This stage happens before the system can safely do normal work. Its job is to make sure the database, which is the system’s long-term memory, has the right shape for the current version of the code. It also fills in the first useful records so a fresh installation is not empty.

The migration runner, env.py, is the switchboard for Alembic, the tool that applies database changes in order. It connects Alembic to the application’s database and runs any missing upgrade steps. The Core and Extension Schema Changes are those ordered steps. They create and adjust tables for workspaces, users, agents, conversations, billing, permissions, jobs, content sources, and feature-specific extensions, like adding labeled drawers to a filing cabinet without losing what is already inside.

After the storage is ready, Workspace Onboarding and Seating creates the first workspace, first administrator, and initial assistant. It also manages who gets a “seat,” meaning permission to use the assistant, and can add demo conversation data so the product has something realistic to show and test.

## Sub-stages

- [Core and Extension Schema Changes](stage-3.1.md) `stage-3.1` — 186 files
- [Workspace Onboarding and Seating](stage-3.2.md) `stage-3.2` — 5 files

## Files in this stage

### Database Migration and First-Run Bootstrapping
### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `schema migration`

When the project needs to create or update database tables, this file is the bridge between Alembic and the project’s actual database setup. Without it, migration scripts would exist, but Alembic would not know what database connection to use or what table definitions to compare against.

The file starts by importing the project’s database metadata, which is the central description of the tables the application expects. It then defines two steps. First, it creates an asynchronous database engine from Alembic’s configuration. An engine is the object SQLAlchemy uses as the doorway to a database. Second, once connected, it hands a normal database connection to Alembic so migrations can run inside a transaction, meaning the changes are grouped together safely.

One important detail is the SQLite setting. SQLite cannot perform some table changes in the same way larger databases can, so the file turns on Alembic’s “batch” mode for SQLite. In plain terms, this lets Alembic rebuild a table behind the scenes when direct alteration is not possible.

At the bottom, the file immediately runs the async migration setup, so loading this environment file starts the migration process.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function tells Alembic how to run migrations using an already-open database connection. It also gives Alembic the project’s table metadata so schema changes can be interpreted correctly.

**Data flow**: It receives a database connection. It uses that connection to configure Alembic, including the project’s table definitions and a special SQLite-safe mode when needed. It then opens a migration transaction, runs the pending migrations, and returns nothing; the database schema is the thing that changes.

**Call relations**: This is the synchronous migration step that the async setup hands off to once a database connection exists. Inside it, Alembic is configured, a transaction is begun, and Alembic’s migration runner is asked to apply the schema changes.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function performs the outer setup needed before migrations can run. It reads Alembic’s database configuration, opens an asynchronous SQLAlchemy connection, runs the migration work through that connection, and then closes the engine.

**Data flow**: It reads the database settings from Alembic’s configuration. From those settings it builds an async database engine, opens a connection, passes that connection into the migration step, and finally disposes of the engine so resources are cleaned up.

**Call relations**: This is the top-level async workflow for the file. It uses SQLAlchemy’s async engine builder to create the database doorway, then hands the live connection to run_migrations so Alembic can do the actual schema update work.

*Call graph*: 1 external calls (async_engine_from_config).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-access-permissions-audience` — The shared rules for who may read, use, share, or act on workspace content and conversations.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-runtime-fleet-heartbeats` — The fleet-wide record of which runtime processes are alive and what work they may be responsible for.
- `reg-schema-migration-version` — The Alembic/database schema version state that records which migrations have been applied and gates safe startup against the expected database shape.
- `reg-proposal-approval-state` — Persisted proposed changes with before/after payloads, authoring information, and pending/approved/rejected status used for review and application workflows.
