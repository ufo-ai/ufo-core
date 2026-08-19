# Core schema, durable records, and persistence contracts  `stage-18` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It defines the durable “paperwork” the rest of the system relies on: what records look like, where they are stored, and how they are safely read or changed. It is used during startup, normal work, background jobs, and rendering, because all those parts must agree on the same data shapes.

The database doorway is core/src/ufo/db.py. It creates connections to the database, starts and finishes transactions, keeps each workspace’s data separate, runs migrations that update the database layout, and closes connections cleanly. The main database blueprint is core/src/ufo/schema/tables.py, which describes the tables, columns, relationships, defaults, and safety rules for both local SQLite and deployed Postgres databases.

The shared vocabulary lives in core/src/ufo/schema/records.py. It defines the records for agents, conversation turns, terminal results, questions, credential requests, and queue state, so workers and user-facing parts understand the same messages. Conversation history is covered by core/src/ufo/transcript.py, which defines the saved transcript format, and core/src/ufo/loop/transcript.py, which safely reads and writes transcript snapshots without letting older copies overwrite newer ones.

## Files in this stage

### Database access and transcript persistence
Safe persistence entry points manage database connections, migrations, workspace isolation, and durable transcript snapshots.

### `core/src/ufo/db.py`

`io_transport` · `startup, request handling, background jobs, migrations, teardown`

This module is the tenancy boundary for the database. In plain terms, it makes sure each database transaction is tied to the right workspace, so one running server can safely serve many customers or work areas without mixing their rows. For PostgreSQL, it uses row-level security, meaning the database itself filters rows based on a setting called app.workspace_id. workspace_tx sets that value only for the current transaction, like writing a temporary room number on a visitor badge that disappears when the visit ends. If no workspace was set, the database fails closed instead of accidentally showing shared data.

The file also owns database connection pools. A pool is a small reusable supply of database connections, so the app does not have to open a fresh connection for every query. Because async database connections belong to the event loop that created them, this file keeps separate engines per event loop and database URL.

There are two transaction paths. workspace_tx is the normal scoped path for application work. owner_tx is the special cross-workspace path used by background sweeps to list work across all workspaces, after which callers must re-enter the proper workspace before reading details. The module also verifies database reachability at startup, runs Alembic migrations, tunes SQLite behavior, records connection-wait metrics, and disposes engines safely during teardown.

#### Function details

##### `_build_engine`  (lines 98–108)

```
def _build_engine(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Creates an asynchronous SQLAlchemy database engine, which is the object used to open pooled database connections. It also attaches SQLite-specific setup hooks when the database is SQLite.

**Data flow**: It receives a database URL and a pool description. It asks _pool_kwargs for the right pool and driver options, creates the engine, and, for SQLite, registers small callbacks that prepare each connection and start writes safely. It returns the ready-to-use engine.

**Call relations**: _engine_for calls this when a loop first needs an engine for a URL. verify_db_reachable also calls it to make a temporary engine just to test whether the database can be reached.

*Call graph*: calls 1 internal fn (_pool_kwargs); called by 2 (_engine_for, verify_db_reachable); 1 external calls (create_async_engine).


##### `_pool_kwargs`  (lines 111–131)

```
def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Builds the settings used when creating a connection pool. It chooses different settings for SQLite and networked databases because they behave differently.

**Data flow**: It receives a database URL and a pool description. It parses the URL to learn which database backend and driver are being used. For SQLite, it returns local-file-friendly pool settings; for other databases, it returns bounded pool settings plus driver-specific connection options from _driver_kwargs.

**Call relations**: _build_engine asks this function for the exact arguments to pass into SQLAlchemy’s engine creation. When the backend is not SQLite, this function hands off to _driver_kwargs so each database driver gets options in the format it understands.

*Call graph*: calls 1 internal fn (_driver_kwargs); called by 1 (_build_engine); 1 external calls (make_url).


##### `_driver_kwargs`  (lines 134–161)

```
def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]
```

**Purpose**: Chooses connection options for the specific PostgreSQL driver being used. This matters because asyncpg and psycopg use different option names for the same ideas, such as connection timeout and application name.

**Data flow**: It receives the driver name and the pool description. If the driver is asyncpg, it returns asyncpg-style options, including a timeout, an application name for database monitoring, and disabled prepared-statement caching. Otherwise it returns psycopg-style options with matching intent.

**Call relations**: _pool_kwargs calls this only for non-SQLite URLs. Its result becomes part of the engine setup used later by _build_engine.

*Call graph*: called by 1 (_pool_kwargs).


##### `_engine_for`  (lines 164–180)

```
def _engine_for(url: str, pool: _Pool) -> AsyncEngine
```

**Purpose**: Finds or creates the database engine for the current event loop and URL. This prevents sharing async database connections across event loops, which would be unsafe.

**Data flow**: It reads the currently running event loop and combines it with the database URL as a key. It removes registry entries for loops that have already closed, reuses an existing engine if one is present, or builds a new one with _build_engine. It returns the engine for this loop and URL.

**Call relations**: workspace_tx and owner_tx call this whenever they need to start a transaction. If there is no engine yet for the current loop, this function creates one before those transaction helpers continue.

*Call graph*: calls 1 internal fn (_build_engine); called by 2 (owner_tx, workspace_tx); 1 external calls (get_running_loop).


##### `init_db`  (lines 183–187)

```
def init_db(url: str) -> None
```

**Purpose**: Registers the main application database URL. This is a startup step that must happen before normal workspace transactions can run.

**Data flow**: It receives a database URL and stores it in the module’s private app URL slot. If the app database was already initialized, it raises an error instead of silently replacing it.

**Call relations**: This function is usually called by higher-level startup code before workspace_tx or owner_tx are used. It does not call other helpers itself; it simply records the URL that later transaction functions rely on.


##### `init_owner_db`  (lines 190–204)

```
def init_owner_db(url: str) -> None
```

**Purpose**: Registers the optional owner-role database URL used for cross-workspace reads. It also normalizes plain PostgreSQL URLs into the async driver form SQLAlchemy needs here.

**Data flow**: It receives an owner database URL. If an owner URL is already set, it raises an error. Otherwise it rewrites a leading postgresql:// prefix to postgresql+asyncpg:// and stores the result for later owner_tx calls.

**Call relations**: Startup code calls this when the process has a special owner database credential. owner_tx later checks whether this URL exists; if it does, owner_tx uses the owner pool, and if it does not, owner_tx falls back to the app database.


##### `verify_db_reachable`  (lines 207–227)

```
async def verify_db_reachable() -> None
```

**Purpose**: Checks at startup that the configured database or databases can actually be reached. This catches a broken database connection before the service starts accepting work.

**Data flow**: It reads the stored app and owner URLs. If no database has been initialized, it raises an error. For each configured URL, it builds a temporary engine, opens and closes one connection, and then disposes that temporary engine so no pooled connection remains.

**Call relations**: This function calls _build_engine directly instead of publishing the engine through _engine_for. It is meant for startup health checks, separate from the engines later used by workspace_tx and owner_tx.

*Call graph*: calls 1 internal fn (_build_engine).


##### `dispose_db`  (lines 230–253)

```
async def dispose_db() -> None
```

**Purpose**: Shuts down all known database engines and clears the stored database URLs. It is used during teardown, such as at the end of a command-line run or a test.

**Data flow**: It first clears the app and owner URLs so future initialization is not blocked. It then finds engines owned by the current event loop and disposes them directly. For engines owned by other still-running loops, it removes them from the registry and asks their own loop to dispose them through _hand_off.

**Call relations**: Teardown code calls this when the whole database layer should be reset. It uses _hand_off for engines that cannot be safely closed from the current loop.

*Call graph*: calls 1 internal fn (_hand_off); 1 external calls (get_running_loop).


##### `_hand_off`  (lines 256–263)

```
def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None
```

**Purpose**: Asks another event loop to dispose an engine that belongs to that loop. This is needed because async database connections must be closed on the loop that owns them.

**Data flow**: It receives an event loop and an engine. It tries to schedule _dispose_on_this_loop on that loop in a thread-safe way. If the loop has already closed, it quietly gives up because there is no safe place left to run the disposal.

**Call relations**: dispose_db calls this for engines owned by other loops. The handoff schedules the actual cleanup work to happen later on the proper loop.

*Call graph*: called by 1 (dispose_db); 1 external calls (call_soon_threadsafe).


##### `_dispose_on_this_loop`  (lines 266–273)

```
def _dispose_on_this_loop(engine: AsyncEngine) -> None
```

**Purpose**: Runs on the engine’s owning event loop and starts the actual engine disposal task. It keeps track of the task so it is not garbage-collected before finishing.

**Data flow**: It receives an engine. It creates an asynchronous task for engine.dispose(), stores that task in the module-level _disposing set, and arranges for the task to remove itself from the set when done.

**Call relations**: _hand_off schedules this function onto the target event loop. It is the final step that actually closes the engine’s pooled database connections on the correct loop.

*Call graph*: 2 external calls (ensure_future, dispose).


##### `dispose_loop_engines`  (lines 276–286)

```
async def dispose_loop_engines() -> None
```

**Purpose**: Disposes only the engines that belong to the currently running event loop, while leaving the configured database URLs in place. This is useful for temporary loops that are about to close.

**Data flow**: It reads the current event loop, scans both the app and owner engine registries, removes entries for this loop, and awaits disposal of each matching engine. Other loops’ engines are left alone.

**Call relations**: Higher-level serve or setup code can call this before closing a short-lived event loop. Unlike dispose_db, it does not clear initialization, so persistent parts of the process can keep using the database.

*Call graph*: 1 external calls (get_running_loop).


##### `_stopping`  (lines 289–296)

```
def _stopping() -> bool
```

**Purpose**: Detects whether the current async task has been asked to stop through cancellation. This helps distinguish a real database error from shutdown being disguised as a database failure.

**Data flow**: It looks up the current asyncio task. If there is a task and its cancellation count is nonzero, it returns true; otherwise it returns false.

**Call relations**: _opened uses this after connection or transaction errors. If the task is stopping, _opened turns certain database-looking failures back into cancellation so shutdown can proceed cleanly.

*Call graph*: called by 1 (_opened); 1 external calls (current_task).


##### `_opened`  (lines 300–353)

```
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a database transaction and wraps it with timing, availability metrics, cancellation handling, and careful cleanup. It is the shared transaction core used by both normal workspace access and owner access.

**Data flow**: It receives an engine and a path label such as workspace or owner. It measures how long it takes to begin a transaction, emits metrics if acquisition fails, yields the open connection to the caller, and then commits or rolls back by closing the transaction context. If cancellation happens during cleanup, it still lets cleanup finish before re-raising the cancellation.

**Call relations**: workspace_tx and owner_tx both call this to get a transaction. Inside, it calls _stopping to decide whether an apparent database error is really part of task shutdown, and it emits observability metrics for connection wait and failure cases.

*Call graph*: calls 1 internal fn (_stopping); called by 2 (owner_tx, workspace_tx); 7 external calls (ensure_future, shield, AsyncExitStack, begin, monotonic, emit_histogram, emit_metric).


##### `workspace_tx`  (lines 357–367)

```
async def workspace_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the normal database transaction for work that must be limited to the current workspace. This is the main safe entry point for application queries.

**Data flow**: It checks that init_db has registered an app database URL. It gets the engine for the current loop through _engine_for, opens a transaction through _opened, reads current_workspace, and, on PostgreSQL, sets the app.workspace_id value for this transaction only. It yields the connection to the caller with that workspace filter in place.

**Call relations**: Application code calls this when it needs to read or write workspace-scoped data. It depends on _engine_for for the right pooled engine and _opened for transaction lifetime; before yielding, it uses SQL text to pin the workspace setting in the database.

*Call graph*: calls 2 internal fn (_engine_for, _opened); 1 external calls (text).


##### `owner_tx`  (lines 371–384)

```
async def owner_tx() -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens the special transaction path that does not pin a workspace. It is meant for narrow cross-workspace enumeration, such as background sweeps finding IDs to process.

**Data flow**: It chooses the owner database URL and owner pool if one was initialized; otherwise it falls back to the app database URL and app pool. It raises an error if no URL is available. Then it gets the correct engine with _engine_for, opens a transaction with _opened, and yields the connection without setting app.workspace_id.

**Call relations**: Background or control-plane code uses this to enumerate work across workspaces. It shares the same engine lookup and transaction wrapper as workspace_tx, but deliberately skips the workspace-setting step so callers must re-scope each returned item themselves.

*Call graph*: calls 2 internal fn (_engine_for, _opened).


##### `apply_migrations`  (lines 387–414)

```
def apply_migrations(url: str, pack: str | None=None) -> None
```

**Purpose**: Runs database schema migrations, which are versioned changes that create or update tables and indexes. It combines the core migrations with any active extension migrations so the database reaches all required heads.

**Data flow**: It receives a database URL and optionally an extension pack name. It builds an Alembic configuration, asks the extension loader for extra migration locations, validates that migration revision IDs are not duplicated, and runs Alembic upgrade to all heads. If the database is SQLite, it then calls _seal_sqlite_journal to leave the file in the expected journal mode.

**Call relations**: Startup, command-line, or test setup code calls this before the application uses the database schema. It calls into the extension loader for migration paths, Alembic for the actual schema work, and _seal_sqlite_journal for SQLite cleanup after migrations.

*Call graph*: calls 1 internal fn (_seal_sqlite_journal); 6 external calls (__init__, upgrade, from_config, migration_locations, catch_warnings, simplefilter).


##### `_seal_sqlite_journal`  (lines 417–433)

```
def _seal_sqlite_journal(url: str) -> None
```

**Purpose**: Finishes preparing a migrated SQLite file so later application connections do not have to change its journal mode under load. This avoids SQLite lock errors on first use.

**Data flow**: It receives a SQLite URL, parses out the database file path, and raises an error if there is no file. It opens the file with sqlite3, sets journal_mode to WAL, and closes the connection.

**Call relations**: apply_migrations calls this after Alembic migrations when the URL points to SQLite. It complements the SQLite connection hook by doing the one-time file conversion before normal engines start using the file.

*Call graph*: called by 1 (apply_migrations); 2 external calls (make_url, connect).


##### `_sqlite_on_connect`  (lines 436–442)

```
def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None
```

**Purpose**: Prepares each new SQLite connection with settings the application expects. These settings enable write-ahead logging, enforce foreign keys, and make SQLite wait briefly instead of immediately failing when the file is busy.

**Data flow**: It receives a low-level SQLite connection from SQLAlchemy. It switches SQLAlchemy out of SQLite’s default transaction mode, runs PRAGMA commands to set journal mode, foreign-key enforcement, and busy timeout, then closes the cursor used for setup.

**Call relations**: _build_engine registers this as a SQLite connect listener. After that, SQLAlchemy runs it automatically whenever a new SQLite connection is opened for an engine.


##### `_sqlite_begin_immediate`  (lines 445–447)

```
def _sqlite_begin_immediate(connection: sa.Connection) -> None
```

**Purpose**: Starts SQLite transactions with an immediate write lock. This turns possible write-lock deadlocks into orderly waiting, like forming a queue before entering a one-person room.

**Data flow**: It receives a SQLAlchemy connection and sends the raw SQL command begin immediate. The result is that SQLite claims the writer slot at the start of the transaction rather than discovering later that it cannot upgrade the lock.

**Call relations**: _build_engine registers this as a SQLite begin listener. SQLAlchemy invokes it when beginning a SQLite transaction, so callers of workspace_tx or owner_tx get safer SQLite write behavior automatically.

*Call graph*: 1 external calls (exec_driver_sql).


### `core/src/ufo/loop/transcript.py`

`io_transport` · `main loop and repair flow transcript persistence`

A conversation transcript is the durable record of what has happened so far. This file wraps the lower-level blob store, which is a storage area for named chunks of data, and gives the rest of the loop a simple way to read or publish the transcript for one conversation.

The important rule here is ordering. Each conversation has a sequence number, called `seq`, that rises as the conversation moves forward. Before writing a transcript, this file reads the currently stored one. If the stored transcript is already at the same or a later sequence number, the new write is ignored. This is like a notice board where only newer updates may replace older ones; someone arriving late with an old copy is not allowed to pin it over the latest version.

That rule matters because different parts of the system may try to finish or repair a turn. The first valid write for a sequence is treated as authoritative, and stale writes cannot roll the conversation backward. The file also hides the storage details: it turns a conversation ID into the correct storage key, decodes stored bytes into a `Conversation`, and encodes a `Conversation` back into bytes when saving.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: Reads the saved transcript for this conversation, if one exists. It gives callers either a decoded `Conversation` object or `None` when nothing has been stored yet.

**Data flow**: It starts with the `Transcript` object's conversation ID and blob store. It builds the storage key for that conversation, asks the blob store for the saved bytes, and turns those bytes back into a conversation. If the blob store says the item is missing, it returns `None` instead of treating that as an error.

**Call relations**: This is the lookup step used before saving. `Transcript.write` calls it first so it can compare the stored sequence number with the one it is about to publish. The function relies on the shared transcript helpers to choose the right key and decode the stored body.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: Saves a conversation transcript only if it is newer than the one already stored. This protects the durable transcript from being overwritten by an older or duplicate update.

**Data flow**: It receives a `Conversation` to publish. First it reads the currently stored transcript. If there is already a transcript with a sequence number greater than or equal to the incoming one, it stops without changing storage. Otherwise, it encodes the incoming conversation into bytes and writes those bytes to the blob store under this conversation's transcript key.

**Call relations**: This is the publishing step used when a turn is completed or when a repair flow republishes a committed final state. It depends on `Transcript.read` to decide whether the write is still valid, then hands the conversation to the shared encoder and blob store only when the update is allowed to become the durable transcript.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### Shared durable schemas
Common record, table, and transcript formats define the durable vocabulary used across surfaces, workers, tools, and renderers.

### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting: used when admitting turns, running workers, recording results, retries, and surface rendering`

This file is the contract at the boundary between the user-facing parts of the product and the background workers that run agent turns. A “turn” is one unit of agent work, like one message being processed from start to finish. Without these shared record shapes, different parts of the system could disagree about what a queued turn looks like, when it is finished, how to bill it, or how to ask the user for more information.

Most of the file is made of Pydantic models. Pydantic is a validation library: it checks that incoming data has the expected shape before the rest of the system trusts it. The file defines allowed status words, default settings, agent icons, prepared tool intents, usage counters, structured questions, credential and account-connection requests, final turn results, agent settings, and the Turn record itself.

A few helper functions create stable UUIDs, which are unique identifiers. They are deterministic: the same workspace, conversation, and sequence number always produce the same turn id. This matters when background work is retried, because the retry should refer to the same real-world turn instead of accidentally creating a duplicate.

The validators are guardrails. They clean unsafe user-provided context text, reject unknown time zones, normalize timestamps, treat missing created-object lists as empty, and make sure a turn only has a terminal result when its status is truly finished.

#### Function details

##### `auto_agent_icon`  (lines 180–199)

```
def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon
```

**Purpose**: Chooses a starting icon for a newly created agent. It tries to pick an icon that matches words in the agent name, avoids icons already used in the workspace when possible, and otherwise picks a repeatable fallback from the agent name.

**Data flow**: It receives an agent name and a collection of icons already taken. It lowercases and splits the name into simple word tokens, looks for a matching keyword such as “support” or “finance,” and also hashes the name to get a stable number. It returns one valid icon name: first an unused keyword match if available, otherwise an unused icon chosen by the hash, and if all icons are used, a repeatable reused icon.

**Call relations**: This helper is used wherever a new agent needs a default visual identity. It calls the standard SHA-256 hash function so the choice is stable: the same name tends to land on the same icon, like assigning a locker number from a name rather than from the current time.

*Call graph*: 1 external calls (sha256).


##### `turn_id_for`  (lines 246–248)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Builds the permanent identifier for a turn from its workspace, conversation, and sequence number. This makes the turn id predictable, which helps retries and workflow replay point back to the same turn.

**Data flow**: It receives a workspace id, a conversation id, and a turn sequence number. It combines them into one string and feeds that into a namespace-based UUID generator. It returns a UUID that will be the same every time those same inputs are used.

**Call relations**: This function is part of turn admission and workflow setup. Instead of asking for a random id, callers use it so the database row and the background workflow can share one identity and avoid duplicate work after a retry.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 251–256)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing-ledger id for one kind of usage within one turn attempt. This lets the system record token or cost usage once per attempt, even if the same workflow is replayed.

**Data flow**: It receives a workspace id, a turn id, a billing dimension such as a category of usage, and optionally an attempt id. It joins those pieces into a string and turns that string into a deterministic UUID. The result identifies one billing write for that exact turn, dimension, and attempt.

**Call relations**: Workers use this when recording billing data. It hands off to the UUID generator so repeated execution of the same attempt collapses onto the same ledger row, while a later resumed attempt gets a different row and can be counted separately.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 259–270)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Creates a stable id for a reply sent before a turn fully ends. This prevents the same mid-turn message from being delivered twice when a workflow is replayed.

**Data flow**: It receives the turn id, the round number, the reply span position within that round, and optionally the attempt id. It combines those values into a deterministic UUID. The returned id names exactly one mid-turn reply in one attempt.

**Call relations**: This fits into the flow where an agent can speak partial results before its final answer. Delivery records use this id so replayed work recognizes the same reply, while a resumed run can create new replies even if its round numbering starts over.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 430–434)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans surface-provided text before it is embedded into the turn context. It stops sender names, question text, or source text from pretending to be markup by removing angle brackets and flattening the value to one line.

**Data flow**: It receives a string or nothing. If there is no value, it leaves it as missing. If there is text, it removes “<” and “>”, collapses whitespace into single spaces, and returns the cleaned line, or returns missing if nothing useful remains.

**Call relations**: Pydantic calls this automatically when building a TurnContext for the sender, question, and source fields. It acts as a small safety filter before the engine later renders that context into a structured prompt.


##### `TurnContext._known_zone`  (lines 438–445)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a provided time zone name is real. This catches mistakes at the edge of the system instead of letting a turn fail later while it is running.

**Data flow**: It receives a time zone string or nothing. If the value is missing, it stays missing. If a name is present, the function asks the system time zone database to load it; if the name is unknown, it raises a clear validation error. A valid name is returned unchanged.

**Call relations**: Pydantic runs this while creating TurnContext. It calls the standard ZoneInfo lookup, so surfaces must provide a valid IANA time zone name such as “America/New_York” before the turn record is accepted.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 475–478)

```
def spawned(self) -> bool
```

**Purpose**: Tells whether this turn was created as a child of another turn. A spawned turn may be a subagent task or another agent task launched by a parent turn.

**Data flow**: It reads the turn’s parent_turn_id field. If that field has a value, it returns true; if it is empty, it returns false. It does not change the turn.

**Call relations**: Other code can use this property when it needs to treat child turns differently from ordinary user-admitted turns. It is a simple label derived from the parent link that the spawn path writes.


##### `Turn._nothing_created`  (lines 482–485)

```
def _nothing_created(cls, value: object) -> object
```

**Purpose**: Turns a missing created-object list into an empty tuple. This lets callers work with “no created objects” as an empty collection instead of having to special-case a database null.

**Data flow**: It receives the raw value for created_refs before normal validation. If the value is null, it returns an empty tuple. Otherwise it passes the value through for normal parsing.

**Call relations**: Pydantic calls this when loading or constructing a Turn. It smooths over the database representation, where a turn that has created nothing may store SQL NULL, so later code can simply iterate over an empty list-like value.


##### `Turn._aware_utc`  (lines 489–494)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Makes sure turn timestamps know they are in UTC. UTC is the common world clock used by the system, and marking it explicitly prevents local-time mix-ups.

**Data flow**: It receives a datetime value or nothing for created_at or updated_at. If there is no timestamp, it stays missing. If the timestamp already has time zone information, it is returned as-is. If it is missing that marker, the function adds UTC as the time zone without changing the clock reading.

**Call relations**: Pydantic applies this to Turn timestamps. It exists because some database drivers can return UTC timestamps without the UTC marker; this validator repairs that before other code compares or displays times.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 497–502)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that the turn’s status and final result agree with each other. A turn may only carry a terminal frame when it is actually finished, failed, or cancelled, and the frame’s status must match the turn’s status.

**Data flow**: It receives the fully built Turn model. It compares the turn status with whether a terminal result is present. If the combination is inconsistent, or if the terminal frame says a different final status, it raises a validation error. Otherwise it returns the turn unchanged.

**Call relations**: Pydantic runs this after the Turn fields have been parsed. It protects the larger worker and surface flow from impossible records, such as a queued turn that already has a final answer or a failed turn whose terminal frame says it was done.


### `core/src/ufo/schema/tables.py`

`data_model` · `startup, migrations, and any database access that needs the shared schema`

Think of this file as the building blueprint for the app’s database. The rest of the system stores workspaces, members, agents, conversations, messages, billing records, credentials, connected accounts, synced sources, pages, and delivery jobs in a database. Without this file, the code would not have a shared map of what can be stored, how records relate to each other, or which bad states the database should reject.

It uses SQLAlchemy, a Python library that describes database tables as Python objects. The central object is `metadata`, which is like a folder holding all table definitions. Each `sa.Table(...)` entry adds one table to that folder. Columns describe individual pieces of data, such as an agent name or a conversation title. Foreign keys describe links between tables, such as a conversation belonging to a workspace. Unique constraints stop duplicate records, such as two members with the same email in one workspace. Check constraints are guardrails: they reject impossible or unsupported values, such as a turn status outside the allowed set.

A notable design choice is that this file is “dialect-neutral”: it avoids tying the schema to only one database engine. It also adds targeted indexes, which are like book indexes for the database, so common lookups such as pending writebacks, active conversations, or billing entries can be found quickly.

#### Function details

##### `_conversation_audience`  (lines 12–13)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function chooses the default audience for a new conversation when the caller has not supplied one directly. In plain terms, it decides whether a conversation should be shared or tied to a specific member, based on the `member_id` being inserted.

**Data flow**: It receives SQLAlchemy’s current database execution context, which contains the values being inserted into the new row. It reads the pending `member_id`, passes that value to `conversation_audience`, turns the result into plain text, and returns that text as the conversation’s stored `audience` value.

**Call relations**: This function is plugged into the `conversation` table as the Python-side default for the `audience` column. When code creates a conversation without explicitly setting `audience`, SQLAlchemy calls this helper during the insert. The helper asks `ufo.audience.conversation_audience` to apply the project’s audience rules, so the table default stays consistent with the rest of the application.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).


### `core/src/ufo/transcript.py`

`data_model` · `cross-cutting transcript persistence and compaction reads`

This file is the contract for durable conversation history. A conversation is not just a chat log; it can also include the system instruction used for a completed turn and extra context injected into the model prompt. Other parts of the project write these records, while debug tools and evaluation tools read them, so the format has to live in one neutral place. Without this file, those parts could quietly disagree about where records are stored or what shape they have.

The file defines typed records using Pydantic, a validation library that checks incoming data has the expected fields and types. `Conversation` is the saved transcript. `CompactionSummary`, `CompactionVerification`, and related small models describe a compressed version of older conversation history. Compaction is like replacing the first chapters of a notebook with a careful summary while keeping the latest pages unchanged.

For storage, the records are turned into compact JSON and then compressed with LZ4, a fast compression format. The key-building functions decide the exact paths used in the blob store, which is a byte-storage service. The read functions fetch compaction pieces back, decompress them, validate them, and return typed Python objects. If bytes are corrupt or no longer match the expected schema, the code raises `TranscriptDecodeError` so readers get a clear failure instead of using bad history.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the main saved transcript of one conversation. It gives writers and readers the same address for the conversation’s compressed message file.

**Data flow**: It takes a conversation UUID, which is a unique identifier, and inserts it into a fixed path pattern. The result is a string like a filing-cabinet label pointing to that conversation’s transcript blob.

**Call relations**: This is the shared naming rule for transcript storage. No specific caller is shown in the provided call facts, but it exists so separate write and read code can meet at the same blob-store key.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated `Conversation` object into compressed bytes ready to store. Someone uses it when they want the durable transcript to be small and consistently formatted.

**Data flow**: It receives a `Conversation`, asks it for its plain data form, converts that data to compact JSON text, encodes the text as bytes, and compresses those bytes with LZ4. The output is the byte blob that can be written to storage.

**Call relations**: This is the writing-side partner to `decode`. It calls the conversation model’s dump method to get serializable data, then uses JSON encoding before compression so all readers see the same stored format.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored transcript bytes back into a validated `Conversation`. It protects readers from corrupt or outdated transcript data by raising a clear transcript-specific error.

**Data flow**: It receives compressed bytes from storage, decompresses them, and asks `Conversation` to validate the JSON inside. If that succeeds, a `Conversation` object comes out; if decompression or validation fails, it raises `TranscriptDecodeError` instead of returning unsafe data.

**Call relations**: This is the reading-side partner to `encode`. It wraps lower-level decompression or validation failures in `TranscriptDecodeError`, giving callers one meaningful error type for transcript decode problems.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 136–137)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one piece of one compaction record. A compaction has separate stored parts: the window before compression, the window after compression, and the summary.

**Data flow**: It takes a conversation UUID, a compaction index number, and which part is wanted: `before`, `after`, or `summary`. It combines them into the exact blob-store key where that piece should live.

**Call relations**: `read_compaction_after` and `read_compaction_record` call this before fetching bytes. It is the single naming rule that keeps all compaction readers pointed at the same storage layout.

*Call graph*: called by 2 (read_compaction_after, read_compaction_record).


##### `decode_compaction`  (lines 140–149)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds a complete `CompactionRecord` from its three stored byte blobs. It is used when a reader needs the full before-and-after story of a compaction, not just one piece.

**Data flow**: It receives the compaction index plus compressed bytes for the `before`, `after`, and `summary` parts. It decompresses and validates each part, pulls the message lists out of the two window records, and returns one `CompactionRecord` containing the index, both message windows, and the structured summary. If any part is unreadable or has the wrong shape, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` calls this after it has fetched all three blobs from storage. This function does the decoding and validation work, then hands back the typed record that higher-level readers can use.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_after`  (lines 152–165)

```
async def read_compaction_after(blob: BlobStore, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Fetches only the `after` window for one compaction. This is useful when a reader only needs the replacement window, avoiding the heavier work of loading the full pre-compaction history.

**Data flow**: It receives a blob store, a conversation UUID, and a compaction index. It builds the `after` key, asks the blob store for those bytes, and returns `None` if the blob is missing. If bytes are found, it decompresses and validates them, then returns the tuple of messages from the saved `after` window. Bad bytes become `TranscriptDecodeError`.

**Call relations**: It calls `compaction_key` to get the correct storage path and `BlobStore.get` to fetch the bytes. Unlike `read_compaction_record`, it stops after the small `after` piece because some callers only need to compare or inspect the installed replacement window.

*Call graph*: calls 2 internal fn (get, compaction_key); 1 external calls (__init__).


##### `read_compaction_record`  (lines 168–179)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches and decodes one complete compaction record for a conversation. It returns `None` when that numbered compaction does not exist.

**Data flow**: It receives a blob store, a conversation UUID, and an index. It builds keys for the `before`, `after`, and `summary` blobs and fetches each one. If any blob is missing, it returns `None`; otherwise it passes all three byte blobs to `decode_compaction` and returns the resulting `CompactionRecord`.

**Call relations**: `read_compaction_records` calls this repeatedly, one index at a time. Inside, this function uses `compaction_key` for the storage addresses, `BlobStore.get` for the actual reads, and `decode_compaction` to turn raw bytes into a safe typed object.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 182–192)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads every saved compaction for a conversation, oldest first. It gives debug or evaluation code the full sequence of compaction events without needing to know how they are numbered.

**Data flow**: It starts at compaction index 1 with an empty list. For each index, it asks `read_compaction_record` for that record. Found records are appended; the first missing index means there are no more records, so it returns all collected records as an immutable tuple.

**Call relations**: This function is the simple walking loop over compaction history. It depends on `read_compaction_record` to know whether each numbered record exists and to decode it, then stops naturally when that helper returns `None`.

*Call graph*: calls 1 internal fn (read_compaction_record).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-inbound-message-queue` — The saved holding area for incoming chat messages before they are admitted into a running or queued turn.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-transcript-history` — The saved conversation transcript and compaction snapshots that all surfaces, workers, prompts, and recovery logic read and update.
- `reg-midturn-replies` — The durable outbox for replies sent before a turn is fully complete, so they can be delivered once even after retries.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-source-sync-catalog` — The saved catalog of external sources, pages, sync cursors, deletion marks, retry backoff, and indexing needs.
- `reg-search-index` — The shared keyword and embedding indexes that let conversations, tools, and background jobs find relevant stored documents.
- `reg-memory-store` — The durable store of remembered facts and memory-search results that can be written, deduplicated, recalled, and shown later.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-workspace-change-log` — The saved record of file changes made during a conversation, used to explain later what the agent changed in the workspace.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-schema-migration-state` — The applied core and extension migration revisions, rollback position, and schema-version bookkeeping that determine which durable records are valid.
- `reg-prompt-change-proposals` — The durable proposal and governance state for suggested agent prompt or behavior changes, including approval and offline-improvement outcomes before agent settings are rewritten.
- `reg-pending-human-interactions` — The durable pending questions, credential-collection prompts, setup requests, and checklist-style waits that tools create and surfaces later resolve.
- `reg-surface-delivery-state` — The outbound reply/writeback bookkeeping for external chat surfaces, including delivery targets, external message identifiers, and exactly-once final reply status.
- `reg-extension-workflow-state` — Extension-owned durable workflow records that are not just UI slots, such as code-review inboxes, evaluation runs, objectives, pauses, briefs, notes, monitors, triggers, and web-chat state.
- `reg-seat-entitlements` — Workspace seat limits, included-seat counts, and seated-member marks that gate access and billing entitlement decisions.
- `reg-turn-created-references` — Saved references or citations created by a turn so final replies, source panels, transcripts, and later turns can resolve cited material consistently.
- `reg-turn-surface-context` — Durable per-turn inbound context such as speaker, on-behalf-of member, timezone, original surface metadata, and connection-authorization status carried from admission into prompting, execution, and delivery.
- `reg-admission-ordering-locks` — Conversation-level admission and serialization locks/cursors that prevent concurrent messages, starts, stops, or queued turns from racing before durable turn execution begins.
