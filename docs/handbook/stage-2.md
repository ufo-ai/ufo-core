# Database migration and install-time schema preparation  `stage-2`

This stage runs during install or upgrade, before the system serves users or starts workspace jobs. It prepares the database, which is the system’s long-term memory. Alembic is the tool that applies these changes in order, like following a stack of numbered renovation plans. The env.py file is the entry door: it connects to the database, loads the table definitions the code expects, and runs any missing migrations.

The core migrations build and reshape the main platform records: workspaces, users, agents, conversations, turns, sources, pages, permissions, runtimes, schedules, billing ledger entries, artifacts, audits, and app identities. Later core groups clean up old designs, repair model names, improve search speed with indexes, and retire unused extension data.

The extension migrations prepare optional feature areas. Memory stores traceable workspace knowledge. Sites stores hosted site deployments and sharing details. Skills, source triggers, scheduling, and web migrations keep custom actions, external starts, paused work, and chats usable. Coding, indexing, evaluation, monitors, objectives, reports, research, and sample migrations add their own specialized tables. Together they make old and new installations match the code that will run next.

## Sub-stages

- [Core foundation and initial platform schema migrations](stage-2.1.md) `stage-2.1` — 11 files
- [Core early runtime, source, ledger, and scheduling migrations](stage-2.2.md) `stage-2.2` — 16 files
- [Core surface, inbound message, source, and page migrations](stage-2.3.md) `stage-2.3` — 14 files
- [Core scheduling, access, workspace control, and fleet migrations](stage-2.4.md) `stage-2.4` — 15 files
- [Core turn, conversation, artifact, and transcript migrations](stage-2.5.md) `stage-2.5` — 13 files
- [Core agent, ledger, membership, and extension-retirement migrations](stage-2.6.md) `stage-2.6` — 15 files
- [Core agent provisioning, model identity, and member metadata migrations](stage-2.7.md) `stage-2.7` — 12 files
- [Core billing, BYOK, surface operations, and turn reference migrations](stage-2.8.md) `stage-2.8` — 18 files
- [Core late app identity, source cleanup, tool allowlist, and audit migrations](stage-2.9.md) `stage-2.9` — 16 files
- [Core legacy branch and Daily Brief history migrations](stage-2.10.md) `stage-2.10` — 5 files
- [Memory extension migrations](stage-2.11.md) `stage-2.11` — 16 files
- [Sites extension migrations](stage-2.12.md) `stage-2.12` — 8 files
- [Skills, source-trigger, scheduling, and web extension migrations](stage-2.13.md) `stage-2.13` — 9 files
- [Coding, indexing, evaluation, reporting, research, and sample extension migrations](stage-2.14.md) `stage-2.14` — 14 files

## Files in this stage

### Database migration and install-time schema preparation
### `core/src/ufo/schema/migrations/env.py`

`entrypoint` · `schema migration startup`

This file exists so the project can safely change its database layout over time. A database migration is a controlled step, such as adding a table or changing a column, that moves the database from one version of the schema to the next. Without this file, Alembic would not know how to connect to the project’s database or what schema metadata to compare against.

The file is run by Alembic when migrations are executed. First, it reads the database settings from Alembic’s configuration. Then it creates an asynchronous SQLAlchemy engine, which is the object responsible for opening database connections without blocking the rest of the program. Once connected, it switches into a synchronous section because Alembic’s migration operations expect a normal SQLAlchemy connection. Think of it like using an adapter plug: the outer tool is async, but the migration machinery needs a regular connection shape.

The migration work itself is done inside a transaction, which means the database changes are treated as one safe unit where possible. If something fails, the database can avoid being left half-changed. There is also a special SQLite setting: migrations are rendered in “batch” mode for SQLite because SQLite has stricter limits around altering existing tables.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function tells Alembic how to run migrations using an already-open database connection. It supplies the project’s table definitions as the target schema and then runs the migration steps inside a transaction.

**Data flow**: It receives a SQLAlchemy connection. It gives that connection and the project’s schema metadata to Alembic, also noting whether SQLite needs its special batch-style migration mode. Then it opens a migration transaction and asks Alembic to apply the migrations. It does not return a value; its effect is changing the database schema if migrations are pending.

**Call relations**: The async setup function calls this through SQLAlchemy’s bridge from async code to normal synchronous code. Inside, it hands control to Alembic: first to configure the migration context, then to begin a transaction, and finally to run the actual migration scripts.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function is the async main routine for the migration environment. It reads the database configuration, opens a connection, runs the migration work, and then closes the engine cleanly.

**Data flow**: It reads Alembic’s configured settings, especially the database connection values prefixed for SQLAlchemy. From those settings it builds an async database engine, opens a connection, passes that connection into the migration function, and finally disposes of the engine so resources are released. It returns nothing; its result is that migrations have been attempted against the configured database.

**Call relations**: The file starts this function immediately with asyncio.run when Alembic loads the script. It creates the database engine using SQLAlchemy’s async engine builder, then hands the live connection to run_migrations so Alembic can do the schema update work.

*Call graph*: 1 external calls (async_engine_from_config).

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-database-schema-version` — The database migration state that records which durable tables and columns the running code can rely on.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-inbound-message-log` — The saved incoming-message log that keeps outside chat, terminal, web, and scheduled events ordered, unique, and ready to become turns.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-subagent-tasks` — The parent-child delegation state that tracks spawned helper agents, their inputs, outputs, names, costs, and undelivered results.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-extension-data-store` — The per-workspace extension storage area where optional features save their own small durable JSON state.
- `reg-security-audit-log` — Durable audit records for sensitive reads and administrative/object changes, such as transcript access and object-change journaling.
- `reg-turn-created-reference-index` — Durable per-turn list of objects, artifacts, sites, files, or other references created during a turn for later transcript display, panels, delivery, and recovery.
