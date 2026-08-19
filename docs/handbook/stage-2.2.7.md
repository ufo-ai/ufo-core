# User-created skill and sample note migrations  `stage-2.2.7`

This stage is part of the system’s behind-the-scenes setup and upgrade work. It defines small database changes, called migrations, which are step-by-step instructions for creating or changing stored data structures. These migrations belong to extensions, so they let optional parts of the system keep their own data without mixing it into the core tables.

The sample extension migration creates a simple note table. It stores one text note for each workspace, like a small scratchpad attached to that workspace. It can also remove the table if the extension is rolled back.

The first skill creation migration adds a table for user-created skills. These are custom abilities or instructions that users add to the system. The table gives those skills a stable place to live.

The second skill migration refines that design. It changes skills so they are tied to a specific agent, not only to a workspace. It updates the database links and keys so the database can safely enforce that ownership. Together, these files prepare storage for user-extensible features.

## Files in this stage

### Sample note storage
Defines the sample extension table for storing one workspace note and its rollback behavior.

### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration`

This migration teaches the database about a new table called `sample_ext_note`. A database migration is like a step-by-step renovation plan for the database: it says what to add when moving forward, and what to remove if rolling back. Without this file, the sample extension would not have a place in the database to save its notes.

The file uses Alembic, a tool that applies database changes in order. Its revision information says this is the first migration for the `sample_ext` branch, and that it depends on the main migration named `0001` already being applied.

When the migration runs forward, it creates a table with two fields. `workspace_id` identifies which workspace the note belongs to, and `note` stores the note text. The `workspace_id` is also the table’s primary key, meaning each workspace can have at most one note in this table. A foreign key links `workspace_id` back to the main `workspace` table. The `ondelete="CASCADE"` rule means that if a workspace is deleted, its sample extension note is automatically deleted too, like removing a folder also removes the paper inside it.

When rolling back, the migration simply drops the table.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the `sample_ext_note` database table used by the sample extension to store a note for each workspace. This is used when the system is being upgraded to include this extension’s database structure.

**Data flow**: It takes no direct input from the caller. It defines the table name, two columns, a link back to the `workspace` table, and a primary key rule, then passes that plan to Alembic so the database is changed. After it runs, the database has a new `sample_ext_note` table ready to store notes.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the table description using SQLAlchemy pieces such as columns, text and UUID types, a foreign key, and a primary key, then hands the finished description to Alembic’s `create_table` operation.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the `sample_ext_note` table if this migration is rolled back. This is the undo step for the table created by `upgrade`.

**Data flow**: It takes no direct input from the caller. It tells Alembic to drop the table named `sample_ext_note`. After it runs, that table and its stored notes are gone from the database.

**Call relations**: Alembic calls this function when reversing this migration. Unlike `upgrade`, it does not rebuild the table details; it simply hands the table name to Alembic’s `drop_table` operation so the database can remove it.

*Call graph*: 1 external calls (drop_table).


### User-created skill storage
Creates the user skill table and then tightens ownership so skills are associated with specific agents.

### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration`

This migration adds a new database table called `user_skill`. A database migration is a planned change to the database shape, like adding a new shelf to a filing cabinet so the application has somewhere reliable to put a new kind of record.

The table stores skills that belong to a workspace. Each skill is identified by two pieces together: the workspace it belongs to and its name. That means two different workspaces can have skills with the same name, but one workspace cannot have two skills with the same name. Each row also stores a digest, which is usually a compact fingerprint of the content, the skill content itself, and timestamps for when the skill was created and last updated.

The table is connected to the existing `workspace` table through `workspace_id`. The foreign key uses `ondelete="CASCADE"`, which means if a workspace is deleted, its saved skills are automatically deleted too. Without this migration, the skill creation extension would not have a place in the database to persist user skills, so saved skills could not survive across runs or be tied cleanly to workspaces.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` table when this migration is applied. This gives the application a permanent database place to store user-created skills for each workspace.

**Data flow**: The function does not take input from the caller. It describes the new table to Alembic, the database migration tool: columns for workspace ID, skill name, digest, content, and timestamps; a link back to the `workspace` table; and a combined primary key made from workspace ID and name. The result is a new table in the database schema.

**Call relations**: When the migration system moves the database forward to this revision, it calls `upgrade`. Inside, this function hands the table definition to Alembic's `create_table`, using SQLAlchemy building blocks to describe the column types and constraints.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when this migration is rolled back. This undoes the schema change made by `upgrade`.

**Data flow**: The function does not take input from the caller. It tells Alembic to drop the `user_skill` table from the database. After it runs, the database no longer has that table, and any data stored there would be gone.

**Call relations**: When the migration system is asked to move the database backward past this revision, it calls `downgrade`. This function delegates the actual removal to Alembic's `drop_table` operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0002_agent_skills.py`

`data_model` · `database migration`

This file is a database migration, which is a scripted step for changing the shape of stored data as the application evolves. Before this migration, a row in the `user_skill` table was identified by a workspace and a skill name. After this migration, the same skill name can exist separately for different agents inside the same workspace, so the table must also record `agent_id`.

The upgrade path adds a new `agent_id` column to `user_skill`. Existing rows do not already know which agent owns them, so the migration fills them in with the earliest-created agent in the same workspace. That is a practical default so old data can keep working instead of being left ownerless. Once every row has an agent, the column becomes required, the table’s primary key is changed to include `agent_id`, and a foreign key is added so each skill must point to a real row in the `agent` table.

The file has separate paths for PostgreSQL and SQLite. PostgreSQL can run direct `alter table` statements. SQLite has more limits around changing existing tables, so Alembic’s batch mode is used; this is like rebuilding the table carefully behind the scenes. The downgrade reverses the change, removing the agent ownership from skills and restoring the older workspace-and-name identity.

#### Function details

##### `upgrade`  (lines 18–35)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new design where each user skill belongs to an agent. It adds the `agent_id` field, fills it for old rows, makes it required, and updates the table rules so the database enforces this new ownership model.

**Data flow**: It reads the current database connection to see which database engine is being used. It changes the `user_skill` table by adding `agent_id`, then updates existing skill rows by looking up the earliest agent in the same workspace. After that, it changes the table’s primary key from workspace plus name to workspace plus agent plus name, and adds a link requiring `agent_id` to match an existing `agent` row. The output is not a returned value; the database schema and existing rows are changed in place.

**Call relations**: This function is run by Alembic when applying this migration during an upgrade. It uses Alembic operations to alter tables and execute raw SQL, and uses SQLAlchemy to describe the new column type. It chooses a PostgreSQL-specific route when possible, otherwise it uses Alembic batch table changes for SQLite-style databases.

*Call graph*: 5 external calls (batch_alter_table, execute, get_bind, Column, Uuid).


##### `downgrade`  (lines 38–49)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration so the database goes back to the older design where user skills are tied only to a workspace and name. It removes the agent ownership column and restores the previous primary key.

**Data flow**: It checks which database engine is connected. It drops the foreign key that links skills to agents, replaces the newer primary key with the older one, and removes the `agent_id` column. Nothing is returned; the database table is changed back in place.

**Call relations**: This function is run by Alembic when rolling the migration back. Like `upgrade`, it uses direct SQL for PostgreSQL and batch table alteration for SQLite-style databases, because different database engines allow different kinds of schema changes.

*Call graph*: 3 external calls (batch_alter_table, execute, get_bind).
