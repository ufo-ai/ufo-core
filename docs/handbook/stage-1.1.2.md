# Foundational platform tables and early extensions  `stage-1.1.2`

This stage is part of the system’s first startup setup. It builds the earliest database tables, which are the shared filing cabinets the rest of the platform depends on. Each migration is a small reversible database change, so the system can move forward or roll back safely.

The first migration lays the foundation: workspaces, members, agents, conversations, turns in those conversations, and usage cost records. This gives the platform a basic place to record who is working, what agent is involved, and what happened. The credentials migration adds a secure place to store workspace-level secrets or access details. The proposal migration adds a table for suggested changes, including who proposed them, what they would change, and whether they are still pending, approved, or rejected. The loop-depth migration lets one turn belong inside another turn, and lets conversations be run by subagents, not just from the command line. Finally, the extension store gives add-ons a simple per-workspace place to save small JSON-shaped data. Together, these tables form the platform’s first working skeleton.

## Files in this stage

### Base platform schema
Establishes the initial workspace, member, agent, conversation, turn, and usage-cost tables that the rest of the platform builds on.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database setup and schema migration`

This file is a database migration, which is a recorded step for changing the shape of the database in a controlled way. Think of it like the first blueprint for the project’s filing cabinet: it says which drawers exist, what labels they have, and which papers are allowed to refer to each other.

The migration creates the starting schema for the application. A workspace is the top-level container. Inside it, there can be agents with names, prompts, and models; members with email addresses; conversations tied to members; and turns, which are individual pieces of work inside a conversation. It also creates a ledger table for recording usage, such as token counts and their price in very small units of US dollars.

The file also protects the database from bad or inconsistent data. For example, an agent name must be unique inside a workspace, conversation surfaces are limited to the command-line interface value `cli`, turn sequence numbers must start at 1, and completed or failed turns must carry terminal result information while queued or running turns must not. Without this migration, the application would have nowhere reliable to store its core records, and later code would not have the database structure it expects.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Creates the initial database tables and rules that the application depends on. This is used when moving a database forward to this version of the schema.

**Data flow**: The function receives no ordinary input from application code; instead, the migration tool runs it against the current database connection. It tells the database to create tables for workspaces, agents, members, conversations, surface identities, turns, and ledger entries, along with primary keys, foreign keys, uniqueness rules, indexes, and checks that reject invalid values. After it finishes, the database has the project’s first usable structure.

**Call relations**: An Alembic migration runner calls this function when applying revision `0001`. Inside the function, it hands table and column definitions to Alembic and SQLAlchemy, which are the libraries that translate these Python instructions into database changes.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Removes everything created by this migration. This is used when rolling the database backward before this schema version.

**Data flow**: The function starts with a database that has the tables and index from `upgrade`. It drops the ledger index first, then removes the tables in an order that avoids breaking links between tables, such as deleting child tables before the workspace table they point to. After it finishes, the database no longer contains this initial schema.

**Call relations**: An Alembic migration runner calls this function when reverting revision `0001`. It delegates the actual removal work to Alembic’s drop operations, which issue the database commands.

*Call graph*: 2 external calls (drop_index, drop_table).


### Operational records
Adds tables for credentials and proposals, enabling secure workspace configuration and tracked suggested changes.

### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration/setup`

This migration exists so the application can save encrypted credentials for each workspace. A migration is like a set of building instructions for the database: it changes the database shape in a controlled, repeatable way.

When applied, this file creates a new table named credential. Each credential belongs to a workspace, identified by workspace_id. The slot field names which credential position or purpose it is for, such as a named place where a secret is stored. The ciphertext field holds the encrypted secret itself, not the plain readable value. The created_at and updated_at fields record when the row was made and last changed.

The table uses a combined primary key made from workspace_id and slot. That means one workspace can have many credential slots, but cannot have two credentials with the same slot name. It also has a foreign key, which means the database checks that every credential points to a real workspace. Without this migration, later code that expects to save or read encrypted workspace credentials would fail because the table would not exist.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the credential table when the database is upgraded to this migration. This gives the system a dedicated place to store encrypted credentials tied to workspaces.

**Data flow**: It starts with the migration tool's database connection and a description of the new table. It defines the table name, its columns, the rule linking credentials to existing workspaces, and the rule that makes each workspace-and-slot pair unique. The result is a new credential table in the database.

**Call relations**: The migration runner calls this function when applying revision 0002 after revision 0001. Inside, it hands the table definition to Alembic's create_table operation, using SQLAlchemy column and constraint objects to describe exactly what should be built.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the credential table when rolling the database back before this migration. This undoes the schema change made by upgrade.

**Data flow**: It receives control from the migration tool during a rollback. It names the credential table and asks the database migration layer to drop it. Afterward, the table and any data in it are gone.

**Call relations**: The migration runner calls this function when reversing revision 0002. It delegates the actual database change to Alembic's drop_table operation, which performs the removal.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This migration teaches the database about proposals. A proposal is a recorded suggestion for changing something: it belongs to a workspace, comes from an agent, points from one digest to another, carries a JSON body with the proposal details, and has a review status. Without this file, newer code that tries to save or read proposals would fail because the database would not have a place to store them.

The file follows Alembic's migration pattern. Alembic is the tool that applies database changes in order, like a careful renovation checklist. The `revision` and `down_revision` values say that this is migration `0003` and it should run after `0002`.

When moving forward, `upgrade` creates the `proposal` table with columns for identifiers, text fields, timestamps, and structured JSON data. It also adds safety rules: the status must be one of `pending`, `approved`, or `rejected`, and links such as `workspace_id`, `agent_id`, and `approved_by` must point to real rows in their related tables. These links are called foreign keys, meaning they prevent orphaned references.

When rolling backward, `downgrade` removes the table. This lets developers or deployments undo the schema change if they need to return to the previous database shape.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: Creates the `proposal` table in the database. This is used when applying the migration so the application can start storing proposal records safely.

**Data flow**: It takes no direct input from application code. Alembic calls it during migration, and it sends a table definition to the database: columns for IDs, workspace and agent links, proposal content, status, approval information, and timestamps. After it runs, the database has a new `proposal` table with rules that protect valid statuses and valid references to related records.

**Call relations**: During a forward migration, Alembic calls `upgrade`. Inside it, the function hands the full table blueprint to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, JSON storage, date-time fields, check constraints, foreign keys, and a primary key so the database can create the table correctly.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: Removes the `proposal` table from the database. This is used when rolling the migration back to the previous schema version.

**Data flow**: It takes no direct input. Alembic calls it during rollback, and it tells the database to drop the `proposal` table. After it runs, proposal records and the table structure are gone, returning the database to the shape it had before this migration.

**Call relations**: During a rollback, Alembic calls `downgrade`. The function delegates the actual removal to `alembic.op.drop_table`, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### Hierarchy and extension storage
Extends the early schema with nested turn and subagent conversation support, then adds persistent JSON storage for extensions.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration during deploy or upgrade`

This migration teaches the database about nested or delegated work. Before this change, a conversation surface could only be `cli`, meaning it came from the command-line interface, and each row in the `turn` table stood on its own. After this change, a turn can record a `parent_turn_id`, which lets the system connect a child turn back to the turn that caused it. It can also store a `subagent_profile`, which is text describing the subagent involved in that turn.

The file also widens a database rule on the `conversation` table. A database check constraint is like a bouncer at the door: it refuses values that are not on the approved list. This migration replaces the old rule, which only allowed `cli`, with a new rule that allows both `cli` and `subagent`.

Like most migrations, it has two directions. `upgrade` applies the new schema when moving forward. `downgrade` undoes the same change if the project needs to roll back to the previous database version. Without this file, newer code that expects subagent conversations or parent-child turn links could fail when reading from or writing to the database.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database from version 0003 to 0004. It adds two optional fields to the `turn` table and updates the allowed conversation surfaces so `subagent` conversations can be stored.

**Data flow**: It starts with the existing database schema. It adds `parent_turn_id` as an optional UUID value, adds `subagent_profile` as optional text, then opens the `conversation` table for alteration and replaces its surface rule. The result is a database that can represent nested turns and both `cli` and `subagent` conversation types.

**Call relations**: Alembic, the database migration tool, calls `upgrade` when applying this revision. Inside it, the function asks Alembic to add columns, uses SQLAlchemy to describe those columns and their data types, and then uses Alembic's batch table alteration helper to safely replace the `conversation_surface` check constraint.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and restores the database shape expected by version 0003. It removes the subagent-related turn fields and narrows the conversation surface rule back to command-line conversations only.

**Data flow**: It starts with the version 0004 database schema. It first changes the `conversation` table rule so only `cli` is allowed again, then removes `subagent_profile` and `parent_turn_id` from the `turn` table. The result is the older schema, with no stored parent-turn link or subagent profile.

**Call relations**: Alembic calls `downgrade` when rolling this revision back. The function uses Alembic's batch table alteration helper to replace the check constraint, then asks Alembic to drop the two columns that `upgrade` added.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration changes the database structure. Its job is to create a shared storage table for extensions, so an extension can keep named values inside a specific workspace. Without this table, extension-specific settings or saved state would have no standard place to live in the database.

The new table is called `ext_store`. Each row belongs to one workspace, names the extension that owns the data, gives the data a key, and stores the value as JSON. JSON means flexible structured data, like lists, objects, strings, or numbers, rather than one fixed column layout. The table also records when the row was created and last updated.

The primary key is the combination of `workspace_id`, `extension`, and `key`. In plain terms, this means one extension can store one value for a given key inside a given workspace, and the database will stop duplicate entries for the same exact spot. The `workspace_id` is tied to the existing `workspace` table with a foreign key, which is a database rule saying the referenced workspace must really exist.

The file also includes the reverse operation: if the migration is undone, it drops the `ext_store` table.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` database table. This is used when moving the database forward to version `0006`, so extensions can store per-workspace JSON values.

**Data flow**: It takes no direct input from the application. When the migration runner calls it, it tells Alembic, the database migration tool, to create a table with workspace, extension, key, value, and timestamp columns. After it runs, the database has a new `ext_store` table with rules that link rows to valid workspaces and prevent duplicate keys for the same extension in the same workspace.

**Call relations**: This function is called by the migration system during an upgrade. It hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, JSON storage, text fields, a foreign key, and a primary key so the database can enforce the intended shape.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` database table. This is used if the database migration is rolled back from version `0006`.

**Data flow**: It takes no direct input from the application. When called, it asks Alembic to drop the `ext_store` table. After it runs, the table and any data stored in it are gone from the database.

**Call relations**: This function is called by the migration system during a rollback. It delegates the actual database change to `alembic.op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).
