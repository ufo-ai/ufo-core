# Core migration runner and initial platform schema  `stage-19.3`

This stage is part of setting up and evolving the project’s database. It is the foundation that later application code relies on when it saves workspaces, users, conversations, credentials, and related records. The migration runner, env.py, is the control booth. It configures Alembic, a tool that applies database structure changes step by step, so it can connect to the right database, compare the database with the project’s table definitions, and run upgrades safely.

The first migration, 0001_heartbeat.py, lays the main floor of the system. It creates the earliest tables for workspaces, members, agents, conversations, message turns, and usage costs. The next migrations add rooms to that floor. 0002_credentials.py adds encrypted credential storage for each workspace. 0003_proposal.py adds a place to store proposals. 0004_loop_depth.py updates conversation records so they can represent nested activity, such as a main agent calling a subagent, and expands the allowed conversation types. Together, these files create the first usable platform schema and a path for changing it over time.

## Files in this stage

### Migration runner
Alembic environment setup connects the migration system to project metadata and executes schema changes safely.

### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `schema migration`

Database migrations are the step-by-step changes that keep a real database in line with the application's expected table layout. This file is the bridge between Alembic, the migration tool, and the project's own schema definition in `ufo.schema.tables.metadata`.

When Alembic runs, this file creates an asynchronous database engine from the settings in Alembic's configuration. An engine is the object SQLAlchemy uses to open database connections. It uses `NullPool`, meaning connections are not kept around for reuse; for a short-lived migration command, that keeps things simple and avoids stale connections.

Once connected, the file switches into SQLAlchemy's synchronous migration mode because Alembic's core migration work expects a normal blocking connection. It then configures Alembic with two key pieces of information: the active database connection and the project's table metadata, which is the blueprint of what the schema should look like. For SQLite, it enables “batch” rendering, a special style of table changes needed because SQLite cannot perform some schema edits directly.

Finally, it opens a migration transaction, runs the pending migrations, closes the connection, and disposes of the engine. Without this file, Alembic would not know how to reach the database or what schema it should migrate toward.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function performs the actual migration work once a database connection already exists. It prepares Alembic with the connection and the project's table blueprint, then runs the migrations inside a transaction so the database changes are applied as one controlled operation where supported.

**Data flow**: It receives an open SQLAlchemy `Connection`, which represents a live link to the database. It gives that connection and the project's `metadata` to Alembic, notes whether SQLite needs special batch-style migration output, starts a migration transaction, and asks Alembic to run the pending migration steps. It returns nothing, but the database schema may be changed.

**Call relations**: The asynchronous `run` function opens the database connection and hands this function to SQLAlchemy's `run_sync` bridge, because Alembic's migration code works with a synchronous connection. Inside that moment, `run_migrations` calls Alembic's configure, transaction, and run routines to do the real schema update.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This is the top-level asynchronous migration setup. It reads Alembic's database settings, opens a database connection, runs the migration function through that connection, and then cleans up the engine.

**Data flow**: It starts with Alembic's configuration section, especially settings whose names begin with `sqlalchemy.`. It turns those settings into an asynchronous SQLAlchemy engine, opens a connection, uses that connection to run `run_migrations`, then disposes of the engine so no database resources are left open. It returns nothing; its effect is to drive the migration process.

**Call relations**: The file starts this function immediately with `asyncio.run(run())` when Alembic loads the environment file. `run` creates the connection layer, then hands control to `run_migrations` for the actual Alembic work, and finally performs cleanup after the migration finishes.

*Call graph*: 1 external calls (async_engine_from_config).


### Initial platform schema
The first migration establishes the core workspace, member, agent, conversation, turn, and cost-tracking tables.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database setup and migration`

This file is like the project’s first blueprint for its database. A database migration is a recorded change to the database structure, so every environment can build the same tables in the same order. Without this file, a fresh database would not know where to store the core records the system needs.

The migration creates a workspace table first, then tables that belong to a workspace: agents, members, conversations, surface identities, turns, and a ledger. The relationships are enforced with foreign keys, which are database rules saying, for example, “this conversation must point to a real member” or “this turn must belong to a real conversation.” It also adds uniqueness rules, such as preventing two agents in the same workspace from having the same name.

Several safety checks are built into the database itself. Conversations and surface identities currently only allow the `cli` surface. Turns must have a valid status, a sequence number of at least 1, and terminal data only when the turn is no longer queued or running. The ledger records billable usage, currently token usage, and requires positive amounts and non-negative prices.

The downgrade function reverses all of this, removing the index and tables in the opposite order so dependencies do not get in the way.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Builds the initial database structure for the application. It creates all core tables, their columns, relationship rules, uniqueness rules, validation checks, and one lookup index for ledger entries by turn.

**Data flow**: It takes no direct input from application code. When Alembic, the migration tool, runs it, the function sends table-building instructions to the database: create workspaces first, then related records such as agents, members, conversations, turns, and ledger entries. After it finishes, the database has the schema needed to store the system’s main data safely.

**Call relations**: Alembic calls this function when moving the database forward to revision 0001. Inside, it hands each table definition to Alembic’s `create_table` operation, using SQLAlchemy objects to describe columns and rules in a database-neutral way. At the end, it asks Alembic to create an index so ledger rows can be found efficiently by their related turn.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Undo the initial database structure created by `upgrade`. This is used when rolling the database back before this migration.

**Data flow**: It takes no direct input from application code. When run, it tells the database to remove the ledger index, then drop the tables one by one in reverse dependency order. After it finishes, the database no longer contains the tables introduced by this migration.

**Call relations**: Alembic calls this function when moving the database backward from revision 0001. It uses Alembic’s `drop_index` and `drop_table` operations, removing dependent tables before the tables they point to so foreign-key relationship rules do not block the rollback.

*Call graph*: 2 external calls (drop_index, drop_table).


### Early schema extensions
Follow-up migrations add encrypted credentials, proposal storage, and nested conversation turn support.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `schema migration`

This migration is like a renovation instruction for the database. When the project moves from schema version 0001 to 0002, it creates a new table named credential. That table stores secret data for each workspace, but it stores the secret as ciphertext, meaning encrypted bytes rather than readable text. Each credential belongs to a workspace, has a slot name that identifies what kind of credential it is, and records when it was created and last updated.

The table uses a combined primary key made from workspace_id and slot. In plain terms, this means a workspace can have many credential slots, but it cannot have two credentials with the same slot name. The workspace_id is also linked back to the workspace table, so the database can enforce that credentials do not point to a workspace that does not exist.

Without this file, newer code that expects to save or read workspace credentials would not have a database table to use. The downgrade path removes the table, which lets operators roll the database back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the credential table. It is used when the database is being moved forward to version 0002.

**Data flow**: It starts with an existing database that has the earlier workspace table. It sends Alembic, the database migration tool, instructions to create a new credential table with workspace links, slot names, encrypted credential bytes, and timestamps. After it runs, the database can store one encrypted credential per workspace and slot.

**Call relations**: Alembic calls this function during an upgrade. The function hands the actual table-building work to Alembic and SQLAlchemy, which are the libraries that translate these Python instructions into database changes.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the credential table. It is used when rolling the database schema back from version 0002 to the previous version.

**Data flow**: It starts with a database that contains the credential table. It tells Alembic to drop that table. After it runs, the table and any credential records inside it are gone.

**Call relations**: Alembic calls this function during a downgrade. It delegates the removal to Alembic's table-dropping operation so the database is returned to the older schema shape.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration changes the shape of the database so the application can remember “proposals”: suggested changes made by an agent inside a workspace. Without this file being run, any code that tries to save or read proposals would fail because the database would have no place to store them.

The file is used by Alembic, a database migration tool that applies schema changes in order. It declares that this is revision 0003 and that it comes after revision 0002, so Alembic knows where it fits in the database history.

On upgrade, it creates a table named proposal. Each row has an id, links to the workspace and agent it belongs to, the extension involved, the old and new digest values, a JSON body for structured proposal details, and a status. The status is limited by a database rule to only three allowed values: pending, approved, or rejected. It can also record which member approved it, if any, plus creation and update timestamps.

The table includes foreign keys, which are database-level links to other tables. These act like labeled references: a proposal must point to real workspace and agent records, and an approval must point to a real member if one is present.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the proposal table when the database is moved forward to this revision. This gives the application a durable place to store proposed changes and their approval state.

**Data flow**: The function takes no direct input from application code. When Alembic runs it, it sends a table definition to the database: column names, data types, required fields, allowed status values, links to other tables, and the primary key. After it finishes, the database contains a new proposal table ready for use.

**Call relations**: Alembic calls this function while applying revision 0003. Inside it, the function asks Alembic to create the table and uses SQLAlchemy building blocks to describe each column, constraint, and table relationship.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the proposal table when the database is rolled back from this revision. This is the undo step for the migration.

**Data flow**: The function takes no direct input. When Alembic runs it during a rollback, it tells the database to drop the proposal table. After it finishes, the table and the data stored in it are gone.

**Call relations**: Alembic calls this function when moving the database backward past revision 0003. It hands off the actual table removal to Alembic’s drop-table operation.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration is like a careful renovation plan for the project’s database. The database already has tables for conversations and turns. This file teaches the migration tool, Alembic, how to move from schema version 0003 to version 0004, and how to undo that move if needed.

The main change is to the `turn` table. A turn can now point to a `parent_turn_id`, which lets the system show that one turn happened underneath another turn, like a reply inside a thread. It also adds `subagent_profile`, a text field that can store information about which subagent was involved in that turn.

The file also changes a rule on the `conversation` table. Before this migration, the `surface` value was only allowed to be `cli`, meaning command-line interface. After the migration, it may also be `subagent`. That check constraint is a database rule that rejects invalid values before they are saved.

The `upgrade` function applies these changes. The `downgrade` function reverses them, restoring the older rule and removing the new columns. Without this file, code that tries to save subagent conversations or nested turn relationships would not have a matching place in the database to store that information.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: This function applies the new version of the database schema. It adds storage for parent-child turn relationships and subagent profile text, then widens the allowed conversation surface values to include `subagent`.

**Data flow**: It starts with the existing database schema. It adds two nullable columns to the `turn` table, meaning old rows do not need immediate values. Then it opens a safe table-alteration block for `conversation`, removes the old rule that only allowed `cli`, and creates a new rule that allows either `cli` or `subagent`. The result is a database ready to store nested turns and subagent conversations.

**Call relations**: Alembic calls this function when moving the database forward to revision `0004`. Inside it, the function asks SQLAlchemy to describe the new columns and asks Alembic to apply those column additions and constraint changes to the database.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: This function undoes the migration and returns the database schema to the previous version. It removes support for subagent conversation surfaces and deletes the two columns added by `upgrade`.

**Data flow**: It starts with a database at revision `0004`. First it changes the `conversation` table rule back so only `cli` is accepted as a surface value. Then it drops `subagent_profile` and `parent_turn_id` from the `turn` table. The result is a schema shaped like revision `0003`, though any data stored only in those removed columns would be lost.

**Call relations**: Alembic calls this function when rolling the database backward from revision `0004`. It uses Alembic’s table alteration and column removal operations to reverse the exact structural changes made by `upgrade`.

*Call graph*: 2 external calls (batch_alter_table, drop_column).
