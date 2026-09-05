# Daily Brief and Sweep migration history  `stage-1.2.14`

This stage is part of behind-the-scenes database upkeep. It records how the old Daily Brief “Sweep” feature changed the database over time, so installs and deployments can move forward safely or undo changes if needed. A database migration is a saved step that changes the shape of stored data, like adding or removing shelves in a filing room.

The first file, sweep_0001_sweep.py, creates the original storage for Sweep editions of the Daily Brief and describes how to remove it on rollback. The second, sweep_0002_application_editions.py, reshapes that storage: it removes older Daily Brief agent data, changes the edition table, and adds a table for Daily Brief applications. The close_the_daily_brief_branch migration is a bookkeeping step. It does not alter tables; it reconnects the old Sweep migration branch to the main migration path so later updates have one clear route. Finally, drop_daily_brief_tables.py removes two unused Daily Brief tables, while keeping instructions to rebuild them if necessary.

## Files in this stage

### Sweep branch schema
Creates and reshapes the storage used by the Sweep Daily Brief branch.

### `core/src/ufo/schema/migrations/versions/sweep_0001_sweep.py`

`data_model` · `database migration`

This migration creates a new database table called `sweep_edition`. In plain terms, that table is a filing cabinet for one daily brief per workspace member per local day. It records who the brief belongs to, what date and timezone it is for, whether it is still pending, failed, or completed, and what conversation or turn it may be connected to. It also keeps temporary candidate data, such as input and finding keys, so a brief can be prepared first and committed later.

The table is tied to existing workspace, member, conversation, and turn records. Those links help keep the database tidy. For example, if a workspace or member is deleted, their sweep editions are deleted too. If a referenced turn is deleted, the edition can remain, but its turn link is cleared.

The main safeguard is the primary key made from workspace, member, and local date. That means the database will not allow two daily brief editions for the same person in the same workspace on the same day. An index is also added for finding pending editions within a workspace quickly, which matters when a background job needs to pick up unfinished work.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: Adds the new `sweep_edition` table and a lookup index used to find pending sweep work quickly. This is run when the database is moved forward to this migration version.

**Data flow**: Before this runs, the database has no dedicated place to store daily sweep editions. The function asks Alembic, the database migration tool, to create the table with its columns, safety rules, links to existing tables, and an index for pending items. After it runs, the application can save and query daily brief edition state.

**Call relations**: This function is called by Alembic during an upgrade. It hands the actual table and index creation to Alembic and SQLAlchemy, which translate the Python table description into database changes.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: Removes the `sweep_edition` table if this migration is rolled back. This is the undo path for the schema change.

**Data flow**: Before this runs, the database may contain the sweep edition table and its data. The function tells Alembic to drop that table. After it runs, that storage is gone, including any sweep edition records stored there.

**Call relations**: This function is called by Alembic during a downgrade. It delegates the actual removal to Alembic, which performs the database operation.

*Call graph*: 1 external calls (drop_table).


### `core/src/ufo/schema/migrations/versions/sweep_0002_application_editions.py`

`data_model` · `database migration`

This file is meant to be run by Alembic, the tool this project uses to change the database over time. Its job is to move the Sweep Daily Brief data model from an older design to a newer one. Before it changes the table structure, it carefully finds the old Daily Brief agent, its conversations, its turns, and related records spread across many tables. It then clears or deletes those records in an order that avoids broken database links. Think of it like taking down a shelf system: first you remove the books and brackets attached to it, then you remove the shelf itself.

The migration also handles databases that may be at slightly different shapes. It checks whether optional tables or columns exist before touching them, so the migration can run safely in more than one historical database state.

After the cleanup, it removes old columns from `sweep_edition`: `attempt` and `conversation_id`. Then it creates a new `sweep_application` table. That table ties together a workspace, conversation, member, and agent, with database rules called foreign keys, which make sure those IDs point to real rows. The downgrade reverses only the schema shape: it drops the new table and adds the removed columns back. It does not restore the deleted Daily Brief data.

#### Function details

##### `upgrade`  (lines 68–208)

```
def upgrade() -> None
```

**Purpose**: Moves the database forward to the new Sweep Daily Brief application design. It deletes old Daily Brief agent-related records, removes obsolete columns from `sweep_edition`, and creates the new `sweep_application` table.

**Data flow**: It reads the database to find agents provisioned by the Sweep extension with the Daily Brief name. From those agents it derives related conversation IDs and turn IDs, then uses those IDs to delete or clear dependent records in many tables. It also checks the live database shape for optional tables or columns before changing them. The end result is that old Daily Brief records are gone, `sweep_edition` no longer has the old columns, and `sweep_application` exists with its required columns and relationship rules.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands SQL statements to Alembic operations such as `op.execute`, `op.batch_alter_table`, and `op.create_table`, while SQLAlchemy builds the table references, filters, columns, and constraints used in those database commands.

*Call graph*: 22 external calls (batch_alter_table, create_table, execute, get_bind, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint (+12 more)).


##### `downgrade`  (lines 211–215)

```
def downgrade() -> None
```

**Purpose**: Moves the database schema back one step if this migration must be undone. It removes the new `sweep_application` table and restores the two old columns on `sweep_edition`.

**Data flow**: It takes the current migrated database as input. It drops `sweep_application`, then edits `sweep_edition` to add back a nullable `conversation_id` column and an `attempt` column with a default value of 1. It changes the table structure only; it does not recreate any data that `upgrade` deleted.

**Call relations**: Alembic calls this function when rolling the migration back. It delegates the actual database work to Alembic operations like `op.drop_table` and `op.batch_alter_table`, with SQLAlchemy used to describe the columns being restored.

*Call graph*: 5 external calls (batch_alter_table, drop_table, Column, Integer, Uuid).


### Branch merge and cleanup
Merges the Sweep migration branch back into the main line and removes obsolete Daily Brief tables.

### `core/src/ufo/schema/migrations/versions/20260823211339_close_the_daily_brief_branch.py`

`config` · `database migration during deployment`

This file exists to fix the shape of the database migration history, not the shape of the database itself. Alembic, the tool used here to move databases from one version to another, keeps a graph of revisions. If a deployed database says it is on a revision whose file is missing or still separate as another “head,” Alembic can stop before it even tries to change the database. That would block the migration job that deployments wait for.

This revision joins two previous lines of history: the normal core revision `20260823021954` and the older Sweep revision `sweep_0002`. Think of it like tying a side road back into the main road so future travelers only see one route forward.

Importantly, this migration deliberately leaves the old Sweep tables in place. The comments explain why: during a rolling deploy, old application pods may still be running for a short time. Those old pods still read the `sweep_application` table before allowing certain scheduled tool actions. If this migration dropped that table too early, those old pods could fail safely by denying work. So this file only retires the extra migration branch and saves actual table cleanup for a later revision.

#### Function details

##### `upgrade`  (lines 21–22)

```
def upgrade() -> None
```

**Purpose**: Marks this revision as applied when moving the database forward. It intentionally performs no table changes because the only needed change is to merge the migration history branch.

**Data flow**: Alembic enters with a database currently stamped at one or both parent revisions. This function makes no database edits; the effect comes from the revision metadata at the top of the file, which tells Alembic that the two parent paths now meet here. The result is a database migration history with one fewer active branch head.

**Call relations**: Alembic calls this during an upgrade run after resolving the parent revisions. There is nothing for it to hand off to, because the branch merge is represented by `down_revision`, not by executable schema commands inside the function.


##### `downgrade`  (lines 25–26)

```
def downgrade() -> None
```

**Purpose**: Defines what would happen when moving backward from this revision. It intentionally does nothing, because undoing this merge would recreate the separate branch head that this file exists to remove.

**Data flow**: Alembic enters while considering a move back from this revision. The function makes no database edits and does not split the migration history in code. The database schema remains unchanged.

**Call relations**: Alembic would call this only during a downgrade. It does not call other helpers or issue database commands, because reversing the branch merge would bring back the deployment problem this migration was created to solve.


### `core/src/ufo/schema/migrations/versions/20260823223019_drop_daily_brief_tables.py`

`data_model` · `database migration during deployment`

This file is an Alembic migration, meaning it is one step in the project’s ordered history of database shape changes. Its job is to clean up the database after an old feature called Sweep, or the daily brief, has fully disappeared from the running application.

The comment at the top explains the timing: an earlier release could not remove these tables yet because an older application image still read from one of them during a safety check. By the time this migration runs, that old image is gone, so the tables no longer serve any live code. Keeping unused tables around is like leaving old filing cabinets in an office after the team has moved away from that paperwork: they take space, confuse readers, and suggest there is still a process that no longer exists.

The `upgrade` function performs the forward change: it drops the `sweep_application` table, removes an index from `sweep_edition`, and then drops `sweep_edition` itself. The `downgrade` function is the reverse recipe. If the migration is undone, it rebuilds both tables, including their columns, primary keys, foreign-key links to other tables, a status rule, an index, and a uniqueness rule.

#### Function details

##### `upgrade`  (lines 18–21)

```
def upgrade() -> None
```

**Purpose**: This applies the cleanup by deleting the obsolete daily brief database tables. Someone would use it when moving the database forward to the version where the Sweep feature is fully removed.

**Data flow**: It takes no direct input from application code, but it runs against the connected database through Alembic’s migration tool. It tells the database to remove `sweep_application`, remove the `sweep_edition_pending` index, and then remove `sweep_edition`. After it finishes, those tables and that index no longer exist.

**Call relations**: Alembic calls this when applying this migration in the normal forward direction. Inside it, the function hands the actual database work to Alembic operations for dropping tables and an index, so the migration tool can issue the right database commands.

*Call graph*: 2 external calls (drop_index, drop_table).


##### `downgrade`  (lines 24–74)

```
def downgrade() -> None
```

**Purpose**: This is the undo path for the migration. It recreates the deleted daily brief tables so the database can be moved back to the previous schema if needed.

**Data flow**: It takes no direct input, but it writes a full table blueprint to the connected database through Alembic and SQLAlchemy. It defines each recreated column, the allowed status values, links to related tables such as workspace and member, primary keys, an index for pending editions, and a uniqueness rule for applications. After it finishes, the two Sweep tables exist again in their old shape, though the old row data would not be restored by this schema-only step.

**Call relations**: Alembic calls this only when rolling the migration backward. It builds the table definitions using SQLAlchemy objects, then passes them to Alembic create operations so the migration tool can rebuild the database structures in the right order.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, UniqueConstraint (+1 more)).
