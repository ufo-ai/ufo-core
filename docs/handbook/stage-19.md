# Persistent schema, migrations, durable storage, and blob storage  `stage-19` (cross-cutting infrastructure)

This stage is the system’s long-term memory and storage foundation. It is used during startup and upgrades to prepare the database, and then quietly supports the main work loop whenever conversations, jobs, files, settings, or results must be saved.

The migration part is like a building inspector and renovation crew. It uses Alembic, a tool that runs ordered database change scripts, to create tables and update older installations without losing data. Core migrations maintain the main platform records, while extension migrations add storage for plug-in features such as memory, web, research, coding, reports, and scheduled tasks.

The durable records and binary objects part defines what gets saved and how. It describes shared records for agents, turns, transcripts, credentials, artifacts, and job state, then maps them into database tables. It also stores larger file-like data, called blobs, in local storage or cloud storage.

The `db.py` file is the safe doorway into all of this. It opens database connections, runs migrations, keeps each workspace’s data separate, wraps changes in transactions, and cleans up afterward.

## Sub-stages

- [Core and extension database migration commands](stage-19.1.md) `stage-19.1` — 192 files
- [Durable records and binary objects](stage-19.2.md) `stage-19.2` — 6 files

## Files in this stage

### Persistent schema, migrations, durable storage, and blob storage
### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This file solves a very practical problem: one running service may serve many customer workspaces, but every database transaction must see only the rows for the workspace it belongs to. It does that by keeping database engines private inside this module and making callers use `workspace_tx` for normal work. A transaction is like checking out a library book under a specific reader’s card: before queries run, the file pins the current workspace ID into PostgreSQL using a short-lived setting, so row-level security (database rules that filter rows automatically) can enforce the boundary. If no workspace was set, PostgreSQL fails closed instead of returning someone else’s data.

The file also has `owner_tx`, a carefully limited exception for background sweeps that must list work across all workspaces. Those callers are expected to re-enter each workspace before reading its details.

Connections are pooled per asyncio event loop, because async database connections belong to the loop that created them. The file builds pools lazily, verifies reachability at startup when asked, measures transaction wait time, copes with cancellation, and disposes engines on the correct loop during shutdown. It also runs Alembic migrations, which are the ordered database schema changes, and applies SQLite-specific settings so local databases behave predictably.

#### Function details

##### `_build_engine`  (lines 108–118)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 121–141)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 144–171)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 174–190)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 193–197)

```
def init_db(url: str) -> None
```


##### `init_owner_db`  (lines 200–214)

```
def init_owner_db(url: str) -> None
```


##### `verify_db_reachable`  (lines 217–237)

```
async def verify_db_reachable() -> None
```

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 240–263)

```
async def dispose_db() -> None
```

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 266–273)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 276–300)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

*Call graph*: 3 external calls (ensure_future, get_running_loop, dispose).


##### `_dispose_on_this_loop.finished`  (lines 295–298)

```
def finished(done: asyncio.Task[None]) -> None
```


##### `dispose_loop_engines`  (lines 303–313)

```
async def dispose_loop_engines() -> None
```

*Call graph*: 1 external calls (get_running_loop).


##### `_stopping`  (lines 316–323)

```
def _stopping() -> bool
```

*Call graph*: called by 1 (_opened); 1 external calls (current_task).


##### `_await_opening`  (lines 326–337)

```
async def _await_opening(opening: asyncio.Future[AsyncConnection]) -> tuple[AsyncConnection, asyncio.CancelledError | None]
```

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_await_close`  (lines 340–348)

```
async def _await_close(close: asyncio.Future[bool | None]) -> asyncio.CancelledError | None
```

*Call graph*: called by 1 (_opened); 1 external calls (shield).


##### `_opened`  (lines 352–416)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 3 internal fn (_await_close, _await_opening, _stopping); called by 2 (owner_tx, workspace_tx); 7 external calls (Lock, ensure_future, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 420–430)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `failed_statement`  (lines 433–453)

```
def failed_statement(error: BaseException) -> dict[str, str]
```


##### `owner_tx`  (lines 457–470)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 473–511)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 7 external calls (__init__, upgrade, from_config, Path, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 514–530)

```
def _seal_sqlite_journal(url: str) -> None
```

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `core_migration_head`  (lines 533–541)

```
def core_migration_head() -> str
```

*Call graph*: 2 external calls (__init__, from_config).


##### `_sqlite_on_connect`  (lines 544–550)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```


##### `_sqlite_begin_immediate`  (lines 553–555)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

*Call graph*: 1 external calls (exec_driver_sql).

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-access-subjects` — The shared visibility rules that say which members or audiences may read conversations, sources, and objects.
- `reg-egress-policy` — The network access rules that decide which outside hosts sandboxed or connector code may contact.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-turn-queue` — The durable queue of conversation turns waiting, running, parked, resumed, or blocked as duplicates.
- `reg-turn-runtime-config` — The per-turn saved runtime settings that must survive retries and keep a turn using the same execution choices.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-source-index-memory` — The saved external pages, search chunks, embeddings, memories, and recall indexes used as workspace knowledge.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-inbound-message-queue` — The durable inbox of external messages waiting to be admitted into conversations exactly once.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-object-system` — The common address book and audit trail for durable workspace objects such as agents, members, artifacts, and connectors.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-runtime-instances` — The shared record of which server processes are alive and which background or surface duties they have claimed.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-subagent-state` — The parent-child turn links, delegation contracts, spawn identities, and pending result deliveries for helper agents.
- `reg-agent-provisioning` — The saved provenance, setup needs, policies, and ownership for agents that are shipped by extensions or created in workspaces.
- `reg-blob-store` — The durable binary-object namespace and storage keys for large files, imported page bodies, previews, attachments, and other non-row data.
- `reg-source-sync-state` — The source-ingestion control state: source definitions, cursors/change-feed positions, error counters, backoff or parked status, and removal markers.
- `reg-surface-outbox` — The durable outbound delivery buffer and writeback markers for replies or mid-turn messages that must be sent to external surfaces exactly once.
- `reg-transcript-access-audit` — The durable audit trail recording privileged reads of private transcripts for later security review.
- `reg-conversation-change-snapshots` — Durable per-conversation workspace/file change snapshots saved after turns so later views and audits can show what changed.
- `reg-credential-fulfillment` — Durable one-time markers that a requested credential/setup slot has been fulfilled for a workspace.
- `reg-ledger-export-state` — Saved progress and options for exporting billing/ledger records, including BYOK-related export bookkeeping.
- `reg-action-proposal-state` — Durable proposal records for actions or changes that require later review, approval, rejection, or replay.
