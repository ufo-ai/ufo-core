# Schema definitions and Alembic wiring  `stage-1.1.1`

This stage is the system’s shared blueprint for stored data. It sits behind the scenes, especially during setup and upgrades, so every part of the project agrees on what a “turn” is and how turns are saved in the database.

The records.py file defines the in-memory shape of a turn, meaning the standard package of information passed between parts of the system when a user or system asks the assistant to do work. It gives turns stable identifiers, status values, and safety checks so one part of the code does not misread what another part produced.

The tables.py file describes the database version of that same world. It uses SQLAlchemy, a Python tool for describing databases in code, to list tables, columns, and rules for valid stored data.

The env.py file connects those table definitions to Alembic, the migration tool. When the database needs to be created or changed, it opens the connection and tells Alembic what schema to apply.

## Files in this stage

### Schema and migration wiring
Defines shared turn records, central SQLAlchemy table metadata, and the Alembic entrypoint that applies schema changes.

### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `schema migration`

Database migrations are the project’s way of safely changing the shape of the database over time, such as adding a table or changing a column. This file is the small startup script Alembic uses when a migration command runs. Without it, Alembic would not know how to connect to the database or what the project’s current table definitions look like.

The file imports the project’s SQLAlchemy metadata, which is the in-code description of the database tables. Think of that metadata as the blueprint for the database. When migrations run, Alembic compares or applies changes using that blueprint.

The work happens in two stages. First, `run` reads the database settings from Alembic’s configuration and creates an asynchronous database engine. An engine is the object SQLAlchemy uses to make database connections. It uses `NullPool`, meaning connections are not kept around for reuse; that is a sensible choice for one-off migration commands.

Then `run` opens a connection and hands it to `run_migrations`. That function configures Alembic with the live connection and the project’s table metadata, starts a migration transaction, and runs the migration steps. For SQLite, it enables “batch” mode, a safer workaround for SQLite’s limited ability to alter tables directly.

At the bottom, the file immediately starts the async migration run, so simply loading this Alembic environment file kicks off the migration process.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function tells Alembic how to run migrations using an already-open database connection. It supplies the project’s table blueprint and starts a transaction so the schema changes are applied in an orderly way.

**Data flow**: It receives a live SQLAlchemy `Connection`, which represents an open path to the database. It gives that connection and the project’s metadata to Alembic, chooses SQLite-friendly batch behavior when the database is SQLite, then runs the migration commands inside a transaction. It returns nothing, but the database schema may be changed by the migrations.

**Call relations**: This function is called from `run` after `run` has opened an asynchronous database connection. Once invoked, it hands control to Alembic’s migration machinery: it configures the migration context, opens a transaction, and asks Alembic to apply the migration scripts.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This is the asynchronous driver for the migration process. It reads the Alembic database configuration, creates a database engine, opens a connection, runs the migration work, and then closes the engine cleanly.

**Data flow**: It reads configuration values from Alembic, especially settings whose names begin with `sqlalchemy.`. From those settings it builds an asynchronous SQLAlchemy engine, uses that engine to open a connection, passes the connection into `run_migrations`, and finally disposes of the engine so resources are released.

**Call relations**: This function is started at the bottom of the file with `asyncio.run`, so it is the main path Alembic follows when this environment file is loaded. It creates the database connection setup using SQLAlchemy’s async engine builder, then hands the actual migration step to `run_migrations`.

*Call graph*: 1 external calls (async_engine_from_config).


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting: used when turns are admitted, queued, run, completed, retried, and displayed`

This file is the common dictionary used by the user-facing surfaces, the work queue, and the background workers. A “turn” is like a ticket in a help desk queue: it has who asked, what they said, where it belongs, whether it is waiting or finished, and what final answer or handoff came back.

The file names the allowed states for a turn, such as queued, running, done, failed, or cancelled. It also defines the shapes of special terminal outcomes: the assistant may finish with text, ask the user a structured question, request private credentials, or ask the user to connect an account. Keeping these as strict records matters because the surface, such as Slack or another chat UI, must know exactly what to render without guessing.

The two ID helper functions make repeatable UUIDs, meaning the same workspace, conversation, and sequence always produce the same turn ID. That is important for safe retries: if work is replayed, the system recognizes it as the same work instead of creating duplicates.

The Pydantic models, which are data classes with validation, also protect the system at the boundary. Sender names are cleaned so they cannot fake markup, timezones are checked early, timestamps are treated as UTC, and a turn is only allowed to carry a final result when its status is final. Without this file, each part of the system would need to invent its own version of these rules, and subtle mismatches would cause duplicate work, bad billing, or confusing user-facing results.

#### Function details

##### `turn_id_for`  (lines 42–44)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable ID for a turn from its workspace, conversation, and sequence number. This lets the system retry or replay work without accidentally creating a second, different turn for the same input.

**Data flow**: It receives a workspace ID, a conversation ID, and a turn sequence number. It combines those values into one text key and turns that key into a UUID using a deterministic UUID method, so the same inputs always produce the same output. It returns that UUID and does not change anything else.

**Call relations**: When a new turn is admitted into the queue, other parts of the system can call this helper to give the turn its identity. It hands off to the standard UUID generation function that makes a repeatable UUID from a namespace and name.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 47–52)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing ledger ID for one kind of usage on one turn attempt. This prevents duplicate billing records when the same work attempt is replayed, while still allowing resumed attempts to be counted separately.

**Data flow**: It receives a workspace ID, a turn ID, a billing dimension such as the kind of usage being recorded, and optionally an attempt identifier. It combines these into one text key and turns that key into a deterministic UUID. The result is returned as the unique ID for that billing row.

**Call relations**: Billing or usage-recording code can call this when it needs to write a charge for a turn. It delegates the actual repeatable UUID creation to the standard UUID function, so callers get the same ledger ID whenever they describe the same billing event.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 164–168)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans the sender name before it is stored in the turn context. This stops a free-text sender name from pretending to be structured markup when it is later placed into the assistant’s context.

**Data flow**: It receives the sender value, which may be missing. If there is no value, it leaves it missing. If there is text, it removes angle brackets, collapses all whitespace into a single-line form, and returns either the cleaned text or no value if nothing meaningful remains.

**Call relations**: Pydantic calls this automatically when a TurnContext is built or validated. It runs before the turn context is later rendered for the assistant, acting like a small safety filter at the system boundary.


##### `TurnContext._known_zone`  (lines 172–179)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that the supplied timezone name is real before the turn is accepted. This makes timezone problems fail early at the user-facing boundary instead of later while the assistant is already working.

**Data flow**: It receives a timezone string, or no value. If no timezone is provided, it returns no value unchanged. If a string is provided, it asks the system timezone database whether that name exists; valid names are returned, and unknown names become a clear validation error.

**Call relations**: Pydantic calls this automatically while building a TurnContext. It uses the standard ZoneInfo lookup as the authority on valid IANA timezone names, such as Europe/London or America/New_York.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn._aware_utc`  (lines 203–208)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Makes sure turn timestamps are treated as UTC even if a database driver returns them without timezone information. This avoids accidentally interpreting stored UTC times as local machine time.

**Data flow**: It receives a created_at or updated_at timestamp, or no timestamp. Missing values pass through unchanged. If the timestamp already has timezone information, it is kept as-is; if it has none, the function adds UTC as its timezone marker and returns the corrected timestamp.

**Call relations**: Pydantic calls this automatically when a Turn is built or loaded. It uses the datetime replace operation to add the UTC marker when needed, protecting later code that compares or displays turn times.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 211–216)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a turn’s final result agrees with its status. A turn that is still queued or running must not have a final frame, and a completed, failed, or cancelled turn must have one with the matching status.

**Data flow**: It reads the whole Turn after its fields have been parsed. It compares the turn status with whether a terminal frame is present, then checks that the terminal frame’s own status matches the turn’s status. If the combination is valid, it returns the same Turn; if not, it raises a validation error.

**Call relations**: Pydantic calls this after creating a Turn, so invalid turn records are rejected before they can move through the queue or be shown to users. It ties together the status constants and the TerminalFrame model so the rest of the system can trust that a turn’s state is internally consistent.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and all database-backed runtime flows`

This file is the map of the application’s database. Instead of writing separate table definitions for SQLite, used in development, and Postgres, used in deployment, it builds one shared SQLAlchemy metadata object that can describe both. Without this file, the rest of the system would not have a single trusted definition of where workspaces, members, conversations, turns, billing records, credentials, scheduled tasks, synced pages, and related records live.

Think of it like the floor plan for a large office building. Each table is a room with a clear purpose. The columns say what can be stored in that room. Foreign keys are hallways that connect rooms, such as a member belonging to a workspace or a turn belonging to a conversation. Constraints are safety rules, such as “a turn status must be one of these known values” or “a spending limit must be positive.” Indexes are shortcuts that help the database quickly find common things, such as pending writebacks, due scheduled tasks, parked turns, or pages in revision order.

A small default helper also sets the audience for new conversations. If a conversation has a member, it becomes member-specific; otherwise it defaults to shared. This matters because many later features depend on knowing who a conversation or synced record is meant for.

#### Function details

##### `_conversation_audience`  (lines 9–10)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function chooses the default audience label for a new conversation row. It keeps conversation privacy consistent by deriving the audience from the member ID being inserted, instead of requiring every caller to remember the rule.

**Data flow**: It receives a SQLAlchemy execution context, which is the database toolkit’s object containing the values currently being inserted. It reads the pending row’s member_id, passes that value to ufo.audience.conversation_audience, and turns the result into text. The returned string becomes the conversation row’s audience value when no explicit audience was provided.

**Call relations**: SQLAlchemy calls this helper while inserting a conversation row that needs a default audience. The helper asks the shared audience utility to apply the project’s audience-naming rule, then hands the finished text back to SQLAlchemy so it can be written into the database.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).
