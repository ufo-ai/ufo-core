# Installed extension schema migrations  `stage-1.9`

This stage is behind-the-scenes setup for extensions that add their own database needs. A database migration is a small, ordered change to the database structure, like adding or removing a drawer in a filing cabinet. These migrations are shipped with installed extensions, so when an extension is enabled, the main system can prepare the right storage for it.

The evaluation environment migration creates tables for test fixtures: a fake email inbox and a calendar. These let the system run evaluations without using real user email or calendar data. The sample extension migration adds a simple table that stores one text note for each workspace, showing how an extension can keep its own data. The skill creation migration adds a table for skills made by users, so those custom skills can be saved and reused.

Each file also includes a rollback path. That means the same migration can undo its change by removing the table it created, keeping extension setup and cleanup predictable.

## Files in this stage

### Extension storage migrations
Alembic-style migrations that add and remove database tables required by installed extensions.

### `extensions/eval_env/ufo_ext_eval_env/migrations/0001_eval_env.py`

`data_model` · `database migration`

This is a database migration: a small script that changes the shape of the database in a controlled way. Here, it gives the evaluation environment a place to store two kinds of information: emails and calendar events. Think of it like adding two new filing cabinets to an office, one labeled “email” and one labeled “events.”

The email table stores each message's workspace, folder, sender, recipients, subject, body, and send time. The event table stores each calendar item's workspace, title, start and end time, attendees, and status. Both tables are tied to a workspace, so every stored email or event belongs to a particular working area. The database relationship is set up so that if a workspace is deleted, its related emails and events are deleted too. That prevents orphaned records from being left behind.

The file also adds indexes on the workspace fields. An index is like a table of contents: it helps the database quickly find all emails or events for one workspace instead of scanning everything.

Without this migration, the evaluation environment would not have a reliable place to save mailbox and calendar state, so features that depend on simulated email or scheduling data would fail or have nowhere durable to store their results.

#### Function details

##### `upgrade`  (lines 12–39)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by adding the email and calendar event tables to the database. It is used when the system is being set up or updated to include this extension's storage needs.

**Data flow**: Before it runs, the database does not have the evaluation email and event tables. The function describes the columns each table needs, links both tables to the existing workspace table, and adds lookup indexes for workspace-based searches. After it runs, the database can store evaluation emails and calendar events, grouped by workspace.

**Call relations**: A migration runner calls this when moving the database forward to this revision. Inside, it hands the table and index instructions to Alembic, the tool that actually issues the database changes, while SQLAlchemy supplies the column and constraint descriptions.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 42–46)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the email and calendar event storage added by upgrade. It is used if the database needs to be rolled back to a state before this extension schema existed.

**Data flow**: Before it runs, the database contains the evaluation email and event tables and their workspace indexes. The function first removes the indexes, then removes the tables themselves. After it runs, those stored emails and events no longer have database tables.

**Call relations**: A migration runner calls this when moving the database backward from this revision. It hands the removal steps to Alembic, which performs the actual index and table deletion in the database.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/sample/migrations/0001_sample_ext_note.py`

`data_model` · `database migration/setup`

This migration teaches the database about a new piece of data used by the sample extension: a workspace note. A database migration is like an instruction card for changing the shape of the database in a controlled way, so every installation can be updated consistently.

When the migration runs forward, it creates a table named `sample_ext_note`. That table has a `workspace_id`, which identifies the workspace the note belongs to, and a `note`, which stores the note text. The `workspace_id` is also the table's primary key, meaning each workspace can have only one row in this table. The table is linked to the main `workspace` table with a foreign key, which is a database rule saying "this note must belong to a real workspace." The `ondelete="CASCADE"` rule means that if a workspace is deleted, its sample extension note is automatically deleted too, preventing leftover orphan data.

The file also includes the reverse instruction. If the migration is rolled back, it drops the `sample_ext_note` table. Without this file, the extension would not have a reliable place to store its note data, and deployments would not know how to create or remove that storage safely.

#### Function details

##### `upgrade`  (lines 12–19)

```
def upgrade() -> None
```

**Purpose**: Creates the database table used by the sample extension to store a note for each workspace. This is used when applying the migration and moving the database schema forward.

**Data flow**: Before it runs, the database has no `sample_ext_note` table. The function describes the table name, its two columns, the rule linking notes to existing workspaces, and the primary key rule that limits the table to one note per workspace. After it runs, the database contains the new table and enforces those rules.

**Call relations**: The migration system calls `upgrade` when this revision is applied. Inside, it hands the table definition to Alembic's `op.create_table`, using SQLAlchemy building blocks such as columns, text and UUID types, a foreign key rule, and a primary key rule to describe exactly what should be created.

*Call graph*: 6 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 22–23)

```
def downgrade() -> None
```

**Purpose**: Removes the sample extension note table when this migration is rolled back. This lets the database return to the shape it had before the extension's table was added.

**Data flow**: Before it runs, the database may contain the `sample_ext_note` table and any notes stored in it. The function tells the migration tool to drop that table. After it runs, the table and its stored note data are gone.

**Call relations**: The migration system calls `downgrade` when this revision is undone. It delegates the actual database change to Alembic's `op.drop_table`, which performs the table removal.

*Call graph*: 1 external calls (drop_table).


### `extensions/skill_create/ufo_ext_skill_create/migrations/skill_create_0001_user_skill.py`

`data_model` · `database migration during setup or upgrade`

This migration gives the application a new place in the database to save skills that users create. A database migration is like a recorded renovation plan: it says exactly what structure to add now, and how to remove it later if the system is rolled back.

The new table is called `user_skill`. Each saved skill belongs to a workspace, has a name, a digest, its full content, and timestamps for when it was created and last updated. The table uses `workspace_id` plus `name` as its combined primary key, meaning a workspace cannot have two skills with the same name, but different workspaces can use the same skill name.

The table is tied to the existing `workspace` table through a foreign key. A foreign key is a rule that says “this value must point to a real row over there.” Here, every skill must belong to a real workspace. The `ondelete="CASCADE"` part means that if a workspace is deleted, its skills are deleted too, like removing a folder and all files inside it.

Without this migration, the skill creation feature would have nowhere reliable to store user skills.

#### Function details

##### `upgrade`  (lines 12–23)

```
def upgrade() -> None
```

**Purpose**: Creates the `user_skill` database table so the application can store user-created skills. It defines the table’s columns, uniqueness rule, and link back to the workspace that owns each skill.

**Data flow**: Before this runs, the database has no `user_skill` table. The function tells Alembic, the database migration tool, to create the table with workspace ID, skill name, digest, content, and timestamp columns. After it runs, the database can store skill records, and each record must belong to an existing workspace.

**Call relations**: This is called by Alembic when applying this migration during an upgrade. It hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks for columns, data types, the foreign key, and the combined primary key.

*Call graph*: 7 external calls (create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 26–27)

```
def downgrade() -> None
```

**Purpose**: Removes the `user_skill` table when this migration is rolled back. This is the undo step for the schema change made by `upgrade`.

**Data flow**: Before this runs, the database may contain the `user_skill` table and any saved skill records inside it. The function tells Alembic to drop that table. After it runs, the table and its stored skill data are gone.

**Call relations**: This is called by Alembic when reversing this migration. It delegates the actual database change to `alembic.op.drop_table`, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).
