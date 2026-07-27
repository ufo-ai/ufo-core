# Special-purpose extension migrations  `stage-19.10`

This stage is part of the system’s setup and upgrade path. It contains small database migrations, which are step-by-step changes that create or remove tables where extensions keep their data. Each migration belongs to a specific extension, so these features can bring their own storage without changing the core system by hand.

The evaluation environment migration creates the database space for fake email and calendar information. This lets test workspaces have mailbox messages and calendar events that behave like real data, but are meant for controlled evaluation.

The sample extension migration creates a simple table for one text note per workspace. It is a small example of how an extension can store its own workspace-specific information, and it can also remove that table if the change is undone.

The skill creation migration adds a user_skill table. This gives the system a place to save skills created by users, and it can drop that storage during rollback. Together, these migrations prepare extension-owned storage before those extensions run.

## Files in this stage

### Extension storage migrations
Small extension-owned migrations create rollback-safe database tables for evaluation fixtures, sample notes, and user-created skills.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. It belongs to Alembic, a tool that applies database changes step by step so every running system can reach the same structure.

The migration adds two tables. The first table, eval_env_email, stores email-like records: which workspace they belong to, what folder they are in, who sent them, who received them, their subject, body, and send time. The second table, eval_env_event, stores calendar-like records: title, start and end time, attendees, and status. Both tables are tied to the main workspace table. That means each email or event belongs to one workspace, and if that workspace is deleted, its related email and calendar data is automatically deleted too. This is like clearing out a desk drawer when the desk itself is removed.

The file also adds indexes on workspace_id. An index is like a lookup tab in a binder: it helps the database quickly find all email or events for a given workspace. The downgrade function reverses the change, removing the calendar table and email table if the migration needs to be rolled back.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the email and calendar tables for the evaluation environment. It also adds lookup indexes so records can be found efficiently by workspace.

**Data flow**: It takes no direct input from application code, but it runs inside Alembic's migration process with access to the database connection. It defines the columns, primary keys, workspace links, and indexes, then sends those instructions to the database. After it finishes, the database can store evaluation emails and calendar events tied to workspaces.

**Call relations**: Alembic calls this when moving the database forward to this migration version. Inside the function, it hands table and index definitions to Alembic operations, using SQLAlchemy building blocks to describe column types, keys, and foreign-key links.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the evaluation email and calendar storage. It is used if the database needs to be rolled back to the state before this extension's tables existed.

**Data flow**: It takes no direct input from application code and runs under Alembic's control. It first removes the indexes, then drops the event and email tables. After it finishes, the database no longer has the structures created by the upgrade function.

**Call relations**: Alembic calls this when rolling the database backward from this migration version. It uses Alembic's drop operations to undo what upgrade created, in an order that avoids leaving indexes pointing at removed tables.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration during setup, upgrade, or rollback`

This migration teaches the database about a new piece of information used by the sample extension: a note attached to a workspace. A database migration is like a written instruction card for changing the shape of the database in a controlled way, so every installation can make the same change safely.

When the migration is applied, it creates a table named `sample_ext_note`. Each row belongs to one workspace, identified by `workspace_id`, and contains the note text in `note`. The `workspace_id` is also the table’s primary key, which means there can be only one note row per workspace. The table points back to the main `workspace` table through a foreign key, which is a database rule saying “this note must belong to a real workspace.” The `ondelete="CASCADE"` rule means that if a workspace is deleted, its sample extension note is automatically deleted too, avoiding leftover orphan data.

The file also includes the reverse instruction. If this migration is rolled back, the note table is dropped. Without this file, the extension would have code expecting a place to store notes, but the database would not have that place.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by creating the database table used by the sample extension to store workspace notes. It is used when moving the database forward to a version that supports this extension data.

**Data flow**: Before it runs, the database has the main `workspace` table but no `sample_ext_note` table. The function defines the new table’s columns and rules: a required workspace ID, required note text, a link back to `workspace`, and a rule that each workspace can have only one note. After it runs, the database has a new table ready to store those notes.

**Call relations**: The migration system calls this function when applying this revision. Inside it, the function hands the table definition to Alembic, the database migration tool, which then issues the actual database commands to create the table with the requested columns and constraints.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the sample extension’s note table. It is used when rolling the database back to a version before this extension table existed.

**Data flow**: Before it runs, the database may contain the `sample_ext_note` table and any notes stored in it. The function asks Alembic to drop that table. After it runs, the table and its stored note data are gone.

**Call relations**: The migration system calls this function when rolling back this revision. It delegates the actual removal to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration during install, upgrade, or rollback`

This file teaches the database about a new kind of saved information: a skill created by a user inside a workspace. A database migration is like a careful renovation plan for a building: it says exactly what new room to add, and also how to undo that change if needed.

When the project is upgraded, the migration creates a `user_skill` table. Each row represents one named skill in one workspace. It stores the workspace it belongs to, the skill name, a digest value that can identify or compare the content, the skill content itself, and timestamps for when it was created and last updated.

The table uses both `workspace_id` and `name` as its primary key, meaning the same workspace cannot have two skills with the same name, but different workspaces can reuse names. It also links `workspace_id` to the existing `workspace` table. The `ondelete="CASCADE"` rule means that if a workspace is deleted, its saved skills are automatically deleted too, so the database does not keep orphaned skills with nowhere to belong.

If this migration is undone, the file drops the whole `user_skill` table. Without this migration, the skill creation extension would not have a proper database place to persist user-created skills.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table used to store skills made by users. This is run when applying the migration so the application has a permanent place to save those skills.

**Data flow**: Before it runs, the database has no `user_skill` table from this migration. The function gives Alembic, the database migration tool, the table name, its columns, its primary key, and its link to the `workspace` table. After it runs, the database can store skill records with workspace ownership, names, content, digests, and timestamps.

**Call relations**: During an upgrade, Alembic calls this function as part of moving the database schema forward. The function hands the actual table-building work to Alembic and SQLAlchemy, which translate the Python table description into database operations.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when this migration is rolled back. This is the undo step for the table created by `upgrade`.

**Data flow**: Before it runs, the database may contain the `user_skill` table and any saved skill rows inside it. The function tells Alembic to drop that table. After it runs, the table and its stored skill data are gone.

**Call relations**: During a rollback, Alembic calls this function to move the database schema backward. It delegates the removal to Alembic's table-dropping operation, mirroring the table creation done by `upgrade`.

*Call graph*: 1 external calls (drop_table).
