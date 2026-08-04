# Core Alembic Harness and Foundational Schema  `stage-1.2`

This stage is part of setting up and upgrading the database, before the main system can safely do its work. It uses Alembic, a tool that applies database changes step by step, like a careful renovation plan for stored data. The env.py file is the entry point: it connects to the database, loads the project’s table definitions, and tells Alembic to run any missing updates.

The earliest migrations then build the system’s basic storage. 0001_heartbeat.py creates the first core tables for workspaces, agents, members, conversations, conversation turns, identities, and usage costs. 0002_credentials.py adds encrypted credential storage for each workspace. 0003_proposal.py adds proposals, which track requested changes and whether they are pending, approved, or rejected. 0004_loop_depth.py expands conversation turns so subagents and parent-child turn links can be recorded. 0006_ext_store.py gives extensions a small per-workspace JSON storage area. Finally, knowledge_graph_0001_graph.py adds early knowledge graph tables for known things and the relationships between them.

## Files in this stage

### Migration Harness
Alembic starts here, configuring the database connection and running the ordered schema migrations.

### `core/src/ufo/schema/migrations/env.py`

`orchestration` · `database migration`

This file exists so the project can safely change its database layout over time. A database migration is like a set of renovation instructions: add this column, create that table, rename this field. Alembic is the tool that reads those instructions and applies them to the real database.

The file first imports the project’s table metadata, which is the in-code description of the expected database shape. When migrations run, `run_migrations` gives Alembic a live database connection and that metadata. It also turns on a special batch mode for SQLite, because SQLite has limits around changing existing tables and needs some changes done in a more careful way.

The `run` function is the async wrapper. It reads the database settings from Alembic’s configuration, creates an asynchronous SQLAlchemy engine, opens a connection, and then runs the synchronous migration work safely inside that connection. Finally, it disposes of the engine so connections are cleaned up.

At the bottom, the file immediately starts `run()` with `asyncio.run`. That means when Alembic loads this file, the migration process begins right away. Without this file, Alembic would not know how to connect to this project’s database or which schema definition to compare migrations against.

#### Function details

##### `run_migrations`  (lines 11–18)

```
def run_migrations(connection: Connection) -> None
```

**Purpose**: This function tells Alembic how to run migrations on one already-open database connection. It supplies the project’s table metadata and starts a migration transaction, so schema changes are applied as one controlled unit where the database supports that.

**Data flow**: It receives a live SQLAlchemy database connection. It gives that connection, the project’s metadata, and a SQLite-specific safety setting to Alembic. Alembic then begins a migration transaction and applies the migration steps; the function does not return data, but the database schema may be changed.

**Call relations**: The async setup function `run` opens the database connection and hands it to `run_migrations`. Inside, `run_migrations` calls Alembic’s configuration and migration functions so the external migration tool can do the actual schema update work.

*Call graph*: 3 external calls (begin_transaction, configure, run_migrations).


##### `run`  (lines 21–26)

```
async def run() -> None
```

**Purpose**: This function prepares the database connection needed for migrations. It reads the Alembic configuration, creates an asynchronous database engine, opens a connection, runs the migration function, and then cleans up the engine.

**Data flow**: It reads database settings from Alembic’s current configuration section. From those settings it builds an async SQLAlchemy engine, opens a connection, passes that connection into `run_migrations`, and finally disposes of the engine so resources are released. It returns nothing; its visible effect is that pending migrations may be applied to the database.

**Call relations**: The file starts this function immediately with `asyncio.run` when Alembic loads the environment script. `run` calls SQLAlchemy’s `async_engine_from_config` to create the database engine, then hands control to `run_migrations` for the actual Alembic migration work.

*Call graph*: 1 external calls (async_engine_from_config).


### Initial Core Schema
The first migrations establish the foundational workspace, agent, conversation, credential, and proposal storage.

### `core/src/ufo/schema/migrations/versions/0001_heartbeat.py`

`data_model` · `database migration`

This file is like the blueprint for the project’s first empty filing cabinet. When a new database is set up, it tells Alembic, the database migration tool, exactly which tables to create and what rules those tables must follow. Without this file, the application would not have a place to store its core records: who belongs to a workspace, which agents exist, what conversations happened, and how much model usage was recorded.

The migration starts with a workspace table, then builds the other tables around it. Agents and members belong to a workspace. Conversations belong to a workspace and a member. Surface identities connect an outside identity, currently only for the command-line interface surface called "cli", back to a member. Turns record individual steps in a conversation, including their order, status, incoming text, and final result when finished. The ledger table records billable usage, currently token counts and their price.

The file also adds safety rules directly in the database. For example, turn sequence numbers must be at least 1, turn statuses must be one of a known set, and ledger amounts must be positive. These rules help stop bad or inconsistent data from being saved even if a bug appears elsewhere in the code.

#### Function details

##### `upgrade`  (lines 12–114)

```
def upgrade() -> None
```

**Purpose**: Creates the initial database structure for the application. Someone uses this when moving a fresh database forward to revision 0001 so the app has all of its required core tables.

**Data flow**: It takes no regular application input. It reads the migration instructions written in the function, then asks Alembic to create tables, columns, keys, uniqueness rules, foreign-key links, check rules, and one index. The result is a database that now has the initial schema needed to store workspaces, agents, members, conversations, turns, surface identities, and ledger entries.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands each table definition to Alembic operations such as create_table and create_index, while SQLAlchemy objects describe the column types and database rules in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint (+3 more)).


##### `downgrade`  (lines 117–125)

```
def downgrade() -> None
```

**Purpose**: Removes everything created by this migration. Someone uses this when rolling the database back before revision 0001, usually during development, testing, or an emergency rollback.

**Data flow**: It takes no regular application input. It tells Alembic to remove the ledger index first, then drops the tables in reverse dependency order so linked tables are removed before the tables they depend on. The result is a database with this migration’s schema removed.

**Call relations**: Alembic calls this function when reversing this migration. It uses Alembic drop operations to undo the work done by upgrade, carefully removing the index and then each table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0002_credentials.py`

`data_model` · `database migration during setup or rollback`

This migration teaches the database about credentials. A migration is a small, ordered change to the database structure, like adding a new shelf to a filing cabinet before the app starts putting papers there. Without this file, the application would have no official database table for storing workspace-specific secrets, so any feature that needs saved credentials would fail or have nowhere safe and consistent to write them.

The migration creates a table named `credential`. Each credential belongs to a workspace, identified by `workspace_id`, and has a `slot`, which is a text label for which credential it is. The actual secret is stored as `ciphertext`, meaning encrypted bytes rather than readable text. The table also records when the credential was created and last updated.

Two important rules are built into the table. First, `workspace_id` must point to an existing workspace, so credentials cannot float around without an owner. Second, the pair of `workspace_id` and `slot` is the primary key, meaning one workspace can have many credential slots, but cannot have two credentials with the same slot name. The `downgrade` function reverses the change by dropping the table, which is useful when rolling the database back to an earlier version.

#### Function details

##### `upgrade`  (lines 12–22)

```
def upgrade() -> None
```

**Purpose**: Creates the `credential` table so the application can store encrypted credentials tied to workspaces. This is used when moving the database forward from the previous schema version.

**Data flow**: Before this runs, the database has no `credential` table from this migration. The function describes the table columns, the required fields, the link back to the `workspace` table, and the rule that each workspace-and-slot pair must be unique. After it runs, the database has a new table ready to hold encrypted credential records.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0002`. Inside, it hands the table definition to Alembic’s table-creation operation, using SQLAlchemy building blocks to describe the column types and constraints in a database-independent way.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, LargeBinary, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Removes the `credential` table when rolling the database back to the earlier schema version. This is the undo path for the migration.

**Data flow**: Before this runs, the database may contain the `credential` table created by `upgrade`. The function asks Alembic to drop that table. After it runs, the table and any credential data stored in it are gone.

**Call relations**: Alembic calls this function when reversing revision `0002`. It does not rebuild individual pieces itself; it simply delegates to Alembic’s drop-table operation to remove the table created by the upgrade step.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/0003_proposal.py`

`data_model` · `database migration`

This is a database migration: a small, ordered recipe for changing the shape of the database. It belongs to Alembic, the tool that applies database changes step by step so every installation can move from one version of the schema to the next safely.

Here, the change is called revision 0003 and it comes after revision 0002. When applied, it creates a new table named proposal. Think of the table like a ledger for suggested changes. Each row has an id, links back to the workspace and agent involved, stores the old and new digests, keeps the proposal body as JSON data, and records timestamps for when it was created and last updated.

The table also protects the data from common mistakes. It only allows the status to be one of pending, approved, or rejected. It requires each proposal to belong to an existing workspace and agent. If someone approved it, that approver must be an existing member. Without this migration, the application would have nowhere reliable to save proposal records, and any feature depending on proposal review or approval would fail at the database level.

#### Function details

##### `upgrade`  (lines 12–31)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the proposal table. It is used when the database is moving forward from revision 0002 to revision 0003.

**Data flow**: It takes no direct input from the application. Alembic calls it during a migration run, and it tells the database to add a proposal table with specific columns, required fields, allowed status values, links to other tables, and a primary key. After it runs successfully, the database can store proposal records.

**Call relations**: Alembic calls this function when upgrading the schema. Inside it, the function hands the table design to alembic.op.create_table, using SQLAlchemy building blocks such as columns, text fields, timestamps, JSON storage, check constraints, foreign keys, and a primary key to describe exactly what should be created.

*Call graph*: 9 external calls (create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 34–35)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the proposal table. It is used if the database needs to roll back from revision 0003 to revision 0002.

**Data flow**: It takes no direct input from the application. Alembic calls it during a rollback, and it tells the database to drop the proposal table. After it runs, proposal records and the table structure are gone.

**Call relations**: Alembic calls this function when downgrading the schema. It delegates the actual removal to alembic.op.drop_table, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### Conversation Extensions
These migrations extend the conversation model for nested turns and add per-workspace extension JSON storage.

### `core/src/ufo/schema/migrations/versions/0004_loop_depth.py`

`data_model` · `database migration`

This migration changes the shape of the database, like adding new labeled drawers to a filing cabinet. Before this change, a conversation could only have the surface value `cli`, meaning it came from the command-line interface. This file expands that rule so conversations can also be marked as `subagent`, which lets the system distinguish work done by a secondary agent from work done directly in the main command-line flow.

It also adds two optional fields to the `turn` table. `parent_turn_id` stores the identifier of another turn, allowing one turn to point back to the turn that caused it. This is how the database can represent nested or loop-like activity instead of only a flat list. `subagent_profile` stores text describing the subagent profile used for that turn, if there was one.

The `upgrade` function applies these changes. The `downgrade` function reverses them: it removes the new columns and tightens the conversation rule back to only allowing `cli`. This matters because migrations must be reversible when possible, so developers can move the database forward or backward to match the version of the application they are running.

#### Function details

##### `upgrade`  (lines 12–17)

```
def upgrade() -> None
```

**Purpose**: Applies this migration to move the database from revision `0003` to `0004`. It adds storage for parent-child turn links and subagent profile text, then updates the allowed conversation surface values to include `subagent`.

**Data flow**: It starts with the existing database schema. It adds two nullable columns to the `turn` table: `parent_turn_id`, a UUID value that can point to another turn, and `subagent_profile`, free text for the subagent profile. It then changes the `conversation` table’s check constraint, which is a database rule that rejects invalid values, so `surface` may be either `cli` or `subagent`. The result is a database that can store nested subagent-related turn data.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0004`. Inside the function, it hands the actual database changes to Alembic operations such as adding columns and altering the `conversation` table, while SQLAlchemy is used to describe the new column types.

*Call graph*: 5 external calls (add_column, batch_alter_table, Column, Text, Uuid).


##### `downgrade`  (lines 20–25)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration and returns the database schema to the previous revision’s shape. It removes subagent-related turn storage and restores the older rule that conversations may only use the `cli` surface.

**Data flow**: It starts with a database that has the `0004` changes applied. First it changes the `conversation` table’s check constraint back so only `cli` is allowed as a `surface` value. Then it drops the `subagent_profile` and `parent_turn_id` columns from the `turn` table. The result is a schema matching the earlier version, though any data stored only in those removed columns would be lost.

**Call relations**: Alembic calls this function when rolling the database back from revision `0004` to `0003`. The function delegates the table edits to Alembic’s batch table alteration and column removal operations so the rollback happens in the database itself.

*Call graph*: 2 external calls (batch_alter_table, drop_column).


### `core/src/ufo/schema/migrations/versions/0006_ext_store.py`

`data_model` · `database migration`

This migration is like adding a new labeled storage cabinet to the database. Each drawer belongs to one workspace, one extension, and one key, and the drawer can hold a JSON value. JSON is a flexible data format for storing objects, lists, strings, numbers, and similar values.

The file exists so the database structure can evolve in a controlled way. When the project is upgraded, Alembic, the database migration tool, runs `upgrade()` to create the new `ext_store` table. The table records which workspace the data belongs to, which extension owns it, the key name, the stored value, and timestamps for when the record was created and last changed.

A key detail is the primary key: `workspace_id`, `extension`, and `key` together must be unique. In plain terms, one extension cannot store two different values under the same key in the same workspace. The table also has a foreign key to `workspace.id`, which means every stored extension value must belong to a real workspace.

If the migration needs to be reversed, `downgrade()` removes the table. Without this file, extensions would not have this shared database-backed place to store per-workspace settings or state.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `ext_store` database table during an upgrade. This gives extensions a structured place to store per-workspace values.

**Data flow**: It starts with the migration tool being told to move the database forward. The function describes the new table: workspace ID, extension name, key, optional JSON value, creation time, update time, a link back to the workspace table, and a uniqueness rule across workspace, extension, and key. The result is a new table in the database.

**Call relations**: Alembic calls this when applying this migration. Inside, it asks SQLAlchemy to describe the table columns and constraints, then hands that description to Alembic's table-creation operation so the actual database structure is changed.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `ext_store` table when rolling this migration back. This undoes the schema change made by `upgrade()`.

**Data flow**: It starts with the migration tool being told to move the database backward. The function names the table to remove. The result is that the `ext_store` table, including any data inside it, is dropped from the database.

**Call relations**: Alembic calls this during a rollback of this migration. It hands the table name to Alembic's drop-table operation, which performs the database change.

*Call graph*: 1 external calls (drop_table).


### Legacy Knowledge Graph
The final foundational migration adds the legacy graph tables for entities and relationships.

### `core/src/ufo/schema/migrations/versions/knowledge_graph_0001_graph.py`

`data_model` · `database migration`

This file is part of the database history for the project. A database migration is like a renovation instruction sheet: it says exactly what new rooms, doors, and labels must be added to the database so newer code has the storage it expects.

Here, the new “rooms” are two tables. `graph_entity` stores named items in a workspace, such as a person, company, organization, or topic. Each entity belongs to a workspace, has a subject scope such as `shared` or a specific `member:...`, and keeps both its display name and a normalized name used for lookup. The table also records whether the entity is only a stub, meaning a placeholder rather than a fully known item.

`graph_edge` stores connections between entities. For example, one entity might work at another, mention another, or have been derived from another. Each edge points from one entity to another, links back to the source page where it came from, includes a confidence score, and can be marked as a tombstone, which means it is treated as removed without necessarily forgetting its history.

The file also adds indexes, which are shortcuts that help the database find common lookups quickly. Without this migration, the knowledge graph feature would have nowhere reliable to store its entities and relationships.

#### Function details

##### `upgrade`  (lines 12–66)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the knowledge graph tables and their lookup shortcuts to the database. It is used when moving the database forward to a version of the application that expects graph entities and graph edges to exist.

**Data flow**: It starts with an existing database schema. It asks Alembic, the database migration tool, to create the `graph_entity` table with columns, required fields, foreign-key links to workspaces, and checks that limit allowed entity types and subject formats. It then creates an index for finding entities by workspace, subject, and normalized name. Next it creates the `graph_edge` table with links to workspaces, source and target entities, a source page, confidence data, deletion state, and checks for allowed edge types and subject formats. Finally, it adds indexes that make common edge searches faster. The result is a database that can store and query the knowledge graph.

**Call relations**: When the migration runner moves the database forward, it calls `upgrade`. This function hands the detailed table, column, constraint, and index instructions to Alembic and SQLAlchemy. SQLAlchemy describes the pieces of the tables, while Alembic carries out the actual database changes.

*Call graph*: 11 external calls (create_index, create_table, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 69–75)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the knowledge graph indexes and tables. It is used if the database must be rolled back to a version before the knowledge graph schema existed.

**Data flow**: It starts with a database that contains `graph_entity`, `graph_edge`, and their indexes. It first removes the indexes on graph edges, then removes the edge table itself. After that it removes the entity lookup index and then the entity table. The result is a database schema returned to its earlier state, without the knowledge graph storage.

**Call relations**: When the migration runner rolls the database backward, it calls `downgrade`. This function delegates the actual removal work to Alembic’s drop operations. It removes the edge table before the entity table because edges depend on entities, much like taking down connecting wires before removing the posts they attach to.

*Call graph*: 2 external calls (drop_index, drop_table).
