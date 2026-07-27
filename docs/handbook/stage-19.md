# Data schema, migrations, and persistence models  `stage-19` (cross-cutting infrastructure)

This stage is the system’s long-term filing cabinet. It is shared behind-the-scenes support used during startup, normal requests, background jobs, and cleanup. It defines what can be stored, how storage changes over time, and how different parts of the product read the same records safely.

At the center, schema/tables.py is the main blueprint for database tables, columns, links, and lookup shortcuts. db.py is the safe doorway that opens database connections, runs transactions for the right workspace, applies migrations, and shuts access down cleanly. The package files simply make the schema and model folders importable.

The sub-stages fill in the cabinet. Control-plane setup creates gateway tables and workspace safety fences. Transcript and record formats preserve conversations and turn state. Core and feature migrations create and evolve tables for workspaces, members, agents, credentials, channels, sources, pages, inbound messages, scheduled tasks, runtimes, grants, billing, exports, seats, memories, search chunks, knowledge graphs, and extension-owned data. Together, they let the codebase upgrade its stored data without losing the history that later work depends on.

## Sub-stages

- [Control-plane database setup and row isolation](stage-19.1.md) `stage-19.1` — 2 files
- [Durable transcript and shared record formats](stage-19.2.md) `stage-19.2` — 4 files
- [Core migration runner and initial platform schema](stage-19.3.md) `stage-19.3` — 5 files
- [Surface, channel, and agent-binding migrations](stage-19.4.md) `stage-19.4` — 6 files
- [Source and page persistence migrations](stage-19.5.md) `stage-19.5` — 7 files
- [Turn execution, admission, and inbound-message migrations](stage-19.6.md) `stage-19.6` — 11 files
- [Runtime, grants, scheduling, and fleet migrations](stage-19.7.md) `stage-19.7` — 11 files
- [Ledger, spend, export, and seat migrations](stage-19.8.md) `stage-19.8` — 9 files
- [Memory, indexing, and knowledge extension migrations](stage-19.9.md) `stage-19.9` — 12 files
- [Special-purpose extension migrations](stage-19.10.md) `stage-19.10` — 3 files

## Files in this stage

### Database schema and access
Core persistence files that expose database access, package schema/model namespaces, and define the shared SQLAlchemy table blueprint.

### `core/src/ufo/db.py`

`io_transport` · `startup, migrations, request/job transactions, teardown`

The main problem this file solves is tenant safety: one running server may serve many workspaces, but data from one workspace must not accidentally be visible to another. It does this by making `workspace_tx` the normal way to talk to the database. A transaction is a short-lived database session where a group of reads and writes happen together. When the database is PostgreSQL, `workspace_tx` pins the current workspace ID into the transaction so row-level security, meaning database rules that filter rows automatically, can allow only that workspace’s rows.

The file keeps its database engines private, like keeping the master keys in a locked cabinet. Code elsewhere gets a transaction, not the raw engine. There is one special exception: `owner_tx`, used for background sweeps that must first find work across all workspaces. It deliberately does not set a workspace, so callers must only use it to find identifiers and then switch back into a normal workspace transaction.

The file also has startup and maintenance duties. It builds SQLAlchemy asynchronous engines, forces their first connection to happen safely before multiple event loops can use them, applies Alembic migrations so tables match the code, and sets SQLite options that make local database use safer and more predictable.

#### Function details

##### `_build_engine`  (lines 41–52)

```
def _build_engine(url: str) -> AsyncEngine
```

**Purpose**: Creates an asynchronous database engine from a database URL. It also adds SQLite-specific safety settings when the URL points to SQLite, then warms the engine up before the rest of the program can share it.

**Data flow**: It receives a database URL. It creates a SQLAlchemy async engine without a connection pool, adds SQLite connection hooks if needed, forces one open-and-close connection to complete setup, and returns the ready engine.

**Call relations**: This is the shared engine factory used during database startup. `init_db` uses it for the normal application database path, and `init_owner_db` uses it for the special cross-workspace owner path. Before it returns, it hands the engine to `_first_connect` so later code does not trip over first-use setup.

*Call graph*: calls 1 internal fn (_first_connect); called by 2 (init_db, init_owner_db); 1 external calls (create_async_engine).


##### `init_db`  (lines 55–59)

```
def init_db(url: str) -> None
```

**Purpose**: Starts the normal database connection path for the process. It is meant to run once near startup, before code tries to open workspace transactions.

**Data flow**: It receives a database URL. If the normal engine already exists, it stops with an error; otherwise it builds the engine and stores it privately for later transaction helpers to use.

**Call relations**: This is the setup step that makes `workspace_tx` possible. It delegates the actual engine creation to `_build_engine`, then later request, job, or command code can use the stored engine indirectly through transaction helpers.

*Call graph*: calls 1 internal fn (_build_engine).


##### `init_owner_db`  (lines 62–71)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Starts the special owner database connection path used for the rare case where code must enumerate work across all workspaces. This is separate from normal workspace-scoped access so the exception is explicit.

**Data flow**: It receives an owner database URL. If an owner engine already exists, it raises an error; otherwise it builds and stores the owner engine for `owner_tx` to use later.

**Call relations**: This is called during setups that need cross-workspace background scans. It uses `_build_engine` just like the normal initializer, but the resulting engine is only exposed through `owner_tx`, not through ordinary workspace transactions.

*Call graph*: calls 1 internal fn (_build_engine).


##### `_first_connect`  (lines 74–94)

```
def _first_connect(engine: AsyncEngine) -> None
```

**Purpose**: Forces the engine’s one-time database setup to happen immediately and safely. This avoids a subtle deadlock risk when different event loops in the same process try to use the engine for the first time at once.

**Data flow**: It receives an async engine. It starts a fresh thread, opens and closes one connection there, waits for that thread to finish, and re-raises any error that happened in the thread.

**Call relations**: This is called by `_build_engine` before the engine is published for general use. Its nested `run` function does the actual thread work and calls `_open_and_close` to trigger the database driver’s first-connection setup.

*Call graph*: called by 1 (_build_engine); 1 external calls (Thread).


##### `_first_connect.run`  (lines 81–88)

```
def run() -> None
```

**Purpose**: Runs the first database connection attempt inside a new event loop on a separate thread. This gives the warm-up code a clean place to run even if the caller is already inside an asynchronous loop.

**Data flow**: It creates a new event loop, uses it to run `_open_and_close` on the engine, records any exception into a shared error list, and closes the loop afterward.

**Call relations**: This is the worker body launched by `_first_connect`. It hands the engine to `_open_and_close`, then `_first_connect` waits for this worker to finish and reports any failure back to the original caller.

*Call graph*: calls 1 internal fn (_open_and_close); 1 external calls (new_event_loop).


##### `_open_and_close`  (lines 97–99)

```
async def _open_and_close(engine: AsyncEngine) -> None
```

**Purpose**: Opens one database connection and immediately closes it. Its job is not to do useful database work, but to trigger the engine’s first-use initialization.

**Data flow**: It receives an async engine. It enters a connection context, does nothing inside it, and exits, which closes or releases the connection.

**Call relations**: This is called only by `_first_connect.run` during engine warm-up. It is the small action that makes SQLAlchemy and the database driver finish their initial setup before the engine is used elsewhere.

*Call graph*: called by 1 (run); 1 external calls (connect).


##### `dispose_db`  (lines 102–109)

```
async def dispose_db() -> None
```

**Purpose**: Cleanly shuts down any database engines this module created. This is used when the process, test, or command is done with database access.

**Data flow**: It reads the stored normal and owner engines. For each one that exists, it asks SQLAlchemy to dispose of it and then clears the stored reference so the module no longer considers the database initialized.

**Call relations**: This is the teardown counterpart to `init_db` and `init_owner_db`. After other code has finished using transactions, this function releases the database resources held by the module.


##### `workspace_tx`  (lines 113–123)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the normal safe database transaction for the current workspace. This is the main path application code should use when reading or writing workspace-owned data.

**Data flow**: It checks that the normal engine exists, opens a transaction, reads the current workspace ID from the ambient context, and, for PostgreSQL, stores that workspace ID in the transaction setting used by row-level security. It yields the database connection to the caller, then commits or rolls back when the context ends.

**Call relations**: Request, turn, or job code is expected to set the current workspace before entering this helper. `workspace_tx` then gives the caller a scoped connection. For PostgreSQL it uses a SQL text statement to set the workspace value that database policies read.

*Call graph*: 1 external calls (text).


##### `owner_tx`  (lines 127–140)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the one deliberately unscoped transaction path for cross-workspace enumeration. It exists for background sweeps that need to find work across workspaces, not for reading tenant data in detail.

**Data flow**: It chooses the owner engine if one was initialized, otherwise it falls back to the normal engine. It opens a transaction and yields the connection without setting any workspace value.

**Call relations**: Background code can use this to find rows or identifiers that point to different workspaces. The important next step happens outside this function: callers are expected to re-enter the correct workspace context and use `workspace_tx` before doing scoped work on each item.


##### `apply_migrations`  (lines 143–168)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Updates the database schema to match the code by running Alembic migrations. A migration is a recorded database change, such as creating or changing tables.

**Data flow**: It receives a database URL and optionally a pack name. It builds an Alembic configuration, combines the core migration folder with active extension migration folders, checks for duplicate revision identifiers, and upgrades the database to all current migration heads.

**Call relations**: This is used during command-line startup, tests, or deployment setup rather than during normal request handling. It asks the extension loader for migration locations, validates Alembic’s migration graph, and then hands control to Alembic to perform the actual schema changes.

*Call graph*: 6 external calls (__init__, upgrade, from_config, migration_locations, catch_warnings, simplefilter).


##### `_sqlite_on_connect`  (lines 171–177)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Applies SQLite settings whenever a SQLite connection is opened. These settings make local SQLite use behave more like a reliable application database.

**Data flow**: It receives a raw SQLite connection from the database driver. It switches SQLAlchemy-controlled transaction behavior off for that connection, enables write-ahead logging, turns on foreign key checks, sets a busy timeout, and closes the temporary cursor it used.

**Call relations**: This function is registered by `_build_engine` only for SQLite engines. SQLAlchemy calls it automatically on each new SQLite connection so callers using `workspace_tx` or `owner_tx` get the safer settings without doing anything extra.


##### `_sqlite_begin_immediate`  (lines 180–182)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions in a way that claims the single writer slot up front. This turns some lock conflicts into waiting in line instead of failing later during a write.

**Data flow**: It receives a SQLAlchemy connection and sends the raw SQL command `begin immediate` to SQLite. That changes the start of the transaction so write access is reserved early.

**Call relations**: This function is registered by `_build_engine` for SQLite transaction begins. SQLAlchemy calls it when a SQLite transaction starts, so the rest of the database code can use the same transaction helpers while SQLite gets behavior suited to its locking model.

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/models/__init__.py`

`data_model` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language that the folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful files, but the label itself does not do the work. Here, the drawer is `ufo.models`, which likely holds data model definitions elsewhere in the directory. Without this file, depending on the Python version and packaging setup, imports that expect `ufo.models` to be a normal package could fail or behave differently. Since the file contains no code, it does not create objects, run setup logic, or change program state beyond enabling the package structure.


### `core/src/ufo/schema/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` is used to say, “the files in this folder belong together as an importable package.” Here, that package is `ufo.schema`, which likely contains code elsewhere for describing or validating structured data. Think of it like a label on a folder in a filing cabinet: the label does not contain the documents, but it tells people and tools what kind of documents belong inside. Because this file is empty, it does not run setup code, expose shortcuts, or change behavior when the package is imported. Its value is mostly organizational: it helps keep schema-related code grouped under one clear namespace.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and cross-cutting database access`

This file is like the floor plan for the project’s database. It does not run business logic itself. Instead, it names every kind of record the system stores and sets the rules that keep those records consistent.

The central object is `metadata`, a SQLAlchemy `MetaData` collection. SQLAlchemy is a Python library that lets code describe database tables in Python instead of writing separate SQL by hand. Each `sa.Table` added to `metadata` becomes part of the shared schema.

The tables describe the main world of the app: workspaces, members, agents, conversations, turns in those conversations, incoming messages, billing ledger entries, spend caps, credentials, authorization grants, proposals, writebacks, shared files, extension storage, runtime heartbeats, synced sources, scheduled tasks, and indexed pages.

The file also defines safety rails. Foreign keys connect records that must belong together, such as a member belonging to a workspace or a turn belonging to a conversation. Unique constraints stop duplicate names, emails, or queue keys where duplicates would confuse the system. Check constraints enforce simple rules, such as positive seat limits, valid turn statuses, and valid billing amounts. Indexes help the database quickly find common sets of records, such as parked turns, pending messages, due writebacks, due sync sources, and scheduled tasks ready to run.

Without this file, the rest of the system would not have one reliable definition of what can be stored and how records relate to each other.

## 📊 State Registers Touched

- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-onboarding-state` — The invite codes, email claim codes, onboarding records, and first-workspace setup state for new hosted users.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-connection-grants` — The saved approvals and safe account handles for connected external accounts such as Slack, GitHub, Composio, and Pipedream.
- `reg-seat-entitlements` — The shared seat and access-limit state that decides which members may use the agent in a workspace.
- `reg-surface-installations` — The stored links between outside surfaces, workspaces, channels, conversations, and agents.
- `reg-inbound-message-queue` — The durable queue of incoming messages and uploads before they are admitted into conversation turns.
- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-turn-state` — The durable status of each unit of agent work, including whether it is waiting, running, paused, finished, failed, or cancelled.
- `reg-runtime-fleet` — The shared heartbeat and ownership records that show which server processes are alive and which work they are responsible for.
- `reg-cancellation-state` — The shared stop signal state used to cancel active turns, child tasks, tools, and abandoned work safely.
- `reg-prompt-state` — The agent instructions, rendered prompt templates, fingerprints, and governed prompt-change proposals.
- `reg-compaction-state` — The saved summaries and reduced conversation versions used when a conversation is too large for a model call.
- `reg-sandbox-session` — The sandbox handle and lifecycle state for the safe workspace where code, files, browsers, and commands run.
- `reg-workspace-storage` — The shared file, blob, artifact, and mount state that stores workspace bytes and files shared back to users.
- `reg-skill-inventory` — The built-in and user-created skill folders, metadata, dependencies, and workspace-specific skill records.
- `reg-subagent-tree` — The shared parent-child work structure for delegated agents, including child turns, messages, waits, and cancellations.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-memory-store` — The durable memories, memory pages, recall events, and consolidation state used for long-term recall.
- `reg-knowledge-graph` — The stored entities and relationships extracted from pages so the system can look up connected facts.
- `reg-scheduled-jobs` — The background job and scheduled-task state that records what should run later, what is claimed, and what repeats.
- `reg-accounting-ledger` — The usage, price, spend-cap, billing, export, and cost records used to track and limit money spent by workspaces and turns.
- `reg-extension-store` — The per-workspace extension-owned storage where plugins keep their own durable records without private tables.
- `reg-observability-context` — The shared tracing, metrics, structured logs, and trace-parent links used to understand work across processes and turns.
- `reg-db-schema-version` — The applied core, control-plane, and extension migration revisions that determine which persisted schema the runtime may safely use.
- `reg-db-connection-pool` — The process-global database engine, sessions, transactions, and connection pool shared by requests, workers, jobs, and shutdown cleanup.
- `reg-todo-checklist` — The per-conversation persistent checklist or task-progress state maintained by the todo extension across agent turns.
- `reg-source-sync-backoff` — Per-source sync error counters, retry/backoff state, and last-result throttling used to decide when background imports should run again.
- `reg-slack-connect-provisioning` — Durable Slack Connect customer-channel and invitation provisioning state, including retry/idempotency progress for admin background jobs.
- `reg-eval-environment-fixtures` — Workspace-scoped fake email and calendar records used by the evaluation environment connectors and tools.
- `reg-turn-admission-context` — Durable per-turn requester/speaker/on-behalf-of, timezone, surface context, and authorization-link metadata used to attribute, resume, and safely handle work.
- `reg-agent-runtime-settings` — Persistent non-prompt agent configuration such as selected runtime profile, workflow/tool policy, internet-access setting, and conversation or surface agent bindings.
- `reg-sample-extension-note` — Workspace-scoped note stored by the sample extension to prove extension migrations and SDK storage contracts work end to end.
