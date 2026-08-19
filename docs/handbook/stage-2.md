# Database schema upgrade, rollback, and extension migrations  `stage-2`

This stage is the database changeover area used during setup, deployment, and rollback. The database schema is the shape of the stored data: which tables exist, what columns they have, and how records link together. Alembic, the migration tool, applies small ordered changes so new code and old data still match.

The env.py file is the launch script for these changes. It connects Alembic to the project’s database and sets the rules for running migrations safely. From there, the core schema migrations update the main shared storage: workspaces, users, agents, conversations, messages, scheduling, credentials, billing, sources, files, memory, and agent settings. They are the main track every installation depends on.

Extension-owned migrations are separate tracks for optional features. They create and evolve tables for code reviews, evaluation data, search indexes, memories, monitors, objectives, research sources, pauses, hosted sites, skills, triggers, briefs, notes, and web chats. Together, these parts act like a careful renovation crew: the core work updates the main building, while extensions update only the rooms they own.

## Sub-stages

- [Core schema migrations](stage-2.1.md) `stage-2.1` — 108 files
- [Extension-owned schema migrations](stage-2.2.md) `stage-2.2` — 36 files

## Files in this stage

### Database schema upgrade, rollback, and extension migrations
### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `schema migration run`

When the project needs to create or update its database tables, Alembic runs this file. Think of it like a stage manager for a renovation crew: it does not design the changes itself, but it makes sure the crew has the right building plans, the right database connection, and a safe transaction to work inside.

The file imports the project’s table metadata, which is the SQLAlchemy description of what the database should look like. That metadata lets Alembic compare or apply changes with knowledge of the project’s tables.

The file uses SQLAlchemy’s asynchronous database engine. “Asynchronous” means it can wait for database work without blocking the whole program. Alembic’s migration operations themselves are run through a normal synchronous connection, so the file bridges the async database connection into Alembic’s expected style.

It also has a special SQLite setting: when the database is SQLite, migrations are rendered in “batch” mode. This matters because SQLite cannot alter tables as flexibly as larger database systems, so Alembic often has to rebuild tables behind the scenes. Without this file, migrations would not know how to connect, what schema metadata to use, or how to run correctly across supported databases.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function gives Alembic the live database connection and the project’s table definitions, then tells it to run the pending database migrations. It is the point where the migration tool is actually configured and started.

**Data flow**: It receives an open SQLAlchemy database connection. It passes that connection, the project schema metadata, and a SQLite-specific batch-mode setting into Alembic. Then it opens a migration transaction, runs the migration steps inside it, and returns nothing; the database schema may be changed as a result.

**Call relations**: The async setup function `run` calls this through SQLAlchemy’s bridge for running synchronous work on an async connection. Inside, it hands control to Alembic by configuring the migration context, beginning a transaction, and asking Alembic to run the migrations.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function prepares the database connection that Alembic will use. It reads the migration configuration, builds an asynchronous database engine, opens a connection, runs the migration work, and then closes the engine cleanly.

**Data flow**: It reads database settings from Alembic’s configuration section. From those settings it creates an async SQLAlchemy engine with no connection pool, opens a database connection, passes that connection into `run_migrations`, and finally disposes of the engine so resources are released.

**Call relations**: This is the main async workflow for the file and is started at the bottom with `asyncio.run(run())`. It calls SQLAlchemy’s `async_engine_from_config` to create the engine, then delegates the actual migration steps to `run_migrations` once a connection is available.

*Call graph*: 1 external calls (async_engine_from_config).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-inbound-message-queue` — The saved holding area for incoming chat messages before they are admitted into a running or queued turn.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-midturn-replies` — The durable outbox for replies sent before a turn is fully complete, so they can be delivered once even after retries.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-source-sync-catalog` — The saved catalog of external sources, pages, sync cursors, deletion marks, retry backoff, and indexing needs.
- `reg-memory-store` — The durable store of remembered facts and memory-search results that can be written, deduplicated, recalled, and shown later.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-workspace-change-log` — The saved record of file changes made during a conversation, used to explain later what the agent changed in the workspace.
- `reg-schema-migration-state` — The applied core and extension migration revisions, rollback position, and schema-version bookkeeping that determine which durable records are valid.
- `reg-extension-kv-store` — Per-workspace extension key/value JSON and setup marker state saved outside core schemas, used by extension setup, runtime behavior, jobs, and cleanup migrations.
