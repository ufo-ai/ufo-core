# Monitor, objective, report, and scheduled-pause extension migrations  `stage-1.2.20`

This stage is behind-the-scenes setup for several extensions. It runs during installation or upgrade, when the system prepares its database for features that need to remember work over time. A migration is a small change to the database structure, like adding a new shelf before storing a new kind of record.

The monitor migration creates the table for scheduled checks, tied to a workspace, conversation, agent, timing, status, and owner. The objectives migrations build storage for goals, their ordered steps, proof that steps were completed or blocked, and later verification checks. A second objectives migration adds a stored flag showing whether a step can run on its own, so the runner does not need to recalculate that each time.

The report digest migrations add storage for summaries of published reports after they are read, plus records of report-read turns where nothing changed. Finally, the scheduled-pause migration creates a durable place for delayed tasks that must resume later. Together, these tables let long-running work survive restarts and upgrades.

## Files in this stage

### Monitor Storage
Creates durable storage for scheduled monitor checks and their workspace, conversation, agent, timing, status, and ownership metadata.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration / setup`

This is a database migration, which means it is a recipe for changing the shape of the database when this extension is installed or updated. Without it, the monitors feature would have nowhere reliable to save its scheduled checks, their current state, or their links to the rest of the system.

The migration adds a new table called `monitor`. Each row represents one monitor. It stores the monitor’s identity, which workspace and conversation it belongs to, which agent runs it, what command it should use, how often it should run, and when it is next due. It also records human-facing information such as the monitor name, audience, reason, next steps, and user description.

The table includes bookkeeping fields too. These track how many probes have run, how many quiet or failed checks happened in a row, whether probes were skipped, and whether a worker has temporarily claimed the monitor for processing. This is like putting both the task card and its progress notes in one filing cabinet drawer.

The migration also protects the data. It links monitors to existing workspaces, conversations, agents, and members using foreign keys, which are database rules that keep references valid. It prevents duplicate monitor names within the same workspace, requires the interval to be at least one minute, and adds an index so the system can quickly find monitors that are due to run.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: Creates the `monitor` table and its lookup index when the monitors extension is installed or migrated forward. This gives the application a structured place to store scheduled monitor definitions and their running state.

**Data flow**: It starts with no `monitor` table in this migration branch. It asks Alembic, the database migration tool, to create columns for IDs, text fields, timing fields, counters, ownership claims, and timestamps. It also adds database rules for valid links, unique names, and minimum intervals, then creates an index for finding due monitors efficiently. After it runs, the database can store and query monitor records.

**Call relations**: This function is called by Alembic during a forward database migration. It hands the actual database work to Alembic operations such as table and index creation, using SQLAlchemy objects to describe columns and constraints in Python before Alembic turns them into database changes.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: Removes the monitor database objects if this migration is rolled back. This is the undo step for the schema created by `upgrade`.

**Data flow**: It starts with the `monitor` table and its due-monitor index present in the database. It tells Alembic to drop the index first, then remove the table. After it runs, the database no longer has the storage structure for monitors from this migration.

**Call relations**: This function is called by Alembic during a rollback. It reverses the work done by `upgrade`, handing the deletion steps to Alembic so the database schema can move back to its earlier state.

*Call graph*: 2 external calls (drop_index, drop_table).


### Objective Storage
Builds the objectives schema, then extends objective steps with a stored fanout decision for independent execution.

### `extensions/objectives/migrations/objectives_0001_objective.py`

`config` · `database migration`

This is a database migration, which is a script that changes the shape of the database in a controlled way. Without this file, the objectives feature would have no permanent storage: the application could not remember what goals belong to a conversation, what steps make up each goal, or what happened while trying to complete them.

The migration builds four linked tables. The main table, objective, stores one named objective inside a workspace and conversation, along with its directive and timestamps. The objective_step table stores the ordered checklist-like steps for that objective. The objective_event table records important happenings for a step, such as whether someone did it or was blocked, plus evidence text. The objective_check table stores later verdicts about a step, as structured JSON data.

The tables are tied together with foreign keys, which are database rules saying, for example, that a step must belong to a real objective. Many of these links use cascade delete, meaning if a workspace, conversation, objective, or step is removed, the dependent records are automatically cleaned up too. The file also adds indexes, which are like book indexes: they make common lookups faster, such as finding objectives for a conversation or events for a step in time order.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating all database storage needed for objectives. It is used when the system is moving forward to a version that includes the objectives feature.

**Data flow**: It takes no ordinary application input. When run by Alembic, the database migration tool, it sends instructions to the database: create the objective, objective_step, objective_event, and objective_check tables; add their columns; enforce their relationships and uniqueness rules; and create indexes for faster lookup. The result is a database that can store objective data safely and consistently.

**Call relations**: Alembic calls this function during an upgrade. Inside it, the function hands table, column, constraint, and index definitions to Alembic and SQLAlchemy, which turn those Python descriptions into real database changes.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by removing the objectives tables and their indexes. It is used if the database must be rolled back to a version before the objectives feature existed.

**Data flow**: It takes no ordinary application input. When run, it tells the database to drop the indexes first where needed, then remove the objective_check, objective_event, objective_step, and objective tables. Afterward, the database no longer has storage for objectives or their related records.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic's drop-index and drop-table operations in the reverse order of creation so dependent tables are removed before the tables they rely on.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`config` · `database migration`

This file changes the shape of the objectives database. An objective is made of steps, and a plan can list those steps in order. But order alone does not always tell the full story: two steps might truly need to happen one after the other, or they might only appear next to each other in the list while still being safe to run at the same time. This migration adds an `independent` column to the `objective_step` table so each step can record that answer directly.

The important idea is “fan-out”: when work splits into multiple pieces that can run in parallel, like several people taking separate checkout lines at a store. By storing whether a step is independent, the engine can decide the shape of that split from saved data. Without this column, the system would have less direct information about which steps can run at once, and might need to recompute or infer that choice each time it wakes up.

For existing rows, the new column is filled with `false`. That is the safe default: old steps are treated as not independent unless someone explicitly says otherwise. The file also includes the reverse operation, so the migration can be undone by removing the column.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: This applies the migration by adding the new `independent` field to the `objective_step` database table. The field is required, and old rows automatically get `false` so existing data remains valid.

**Data flow**: It reads no application data directly. When run by Alembic, the database migration tool, it creates a new Boolean true-or-false column named `independent` on `objective_step`, with a database-side default of `false`. Afterward, every objective step row has a stored answer for whether it is independent.

**Call relations**: Alembic calls this function when moving the database forward to revision `objectives_0002`. Inside, it hands the table and column definition to `alembic.op.add_column`, using SQLAlchemy to describe the column type and default value.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by removing the `independent` field from the `objective_step` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It takes the current database schema, looks for the `independent` column on `objective_step`, and removes it. Afterward, objective step rows no longer store whether they are independent.

**Call relations**: Alembic calls this function when rolling the database back from revision `objectives_0002` to `objectives_0001`. It delegates the actual schema change to `alembic.op.drop_column`.

*Call graph*: 1 external calls (drop_column).


### Report Digest Storage
Adds durable tables for read report digests and later unchanged report-reading turns.

### `extensions/report_digest/ufo_ext_report_digest/migrations/0001_report_digest.py`

`data_model` · `database migration`

This is a database migration file. A migration is a small, ordered change to the database shape, like adding a new shelf to a filing cabinet so the application has somewhere to put a new kind of record.

Here, the new shelf is a table called `report_digest_entry`. Each row records a digest for one report-reading event, tied to both a workspace and a turn. The row stores the report title, a summary, structured points as JSON, the reader name or identifier, the model used to write the digest, and the time it was written.

The table uses `workspace_id` and `turn_id` together as its primary key, meaning there can be only one digest entry for a given workspace-and-turn pair. It also links those IDs back to the existing `workspace` and `turn` tables. If a workspace or turn is deleted, the related digest entry is deleted too. That prevents orphaned digest records from being left behind with no parent data.

Without this migration, the report digest extension would not have a database place to save its digest entries, so any feature expecting to store or retrieve those entries would fail once it touched the database.

#### Function details

##### `upgrade`  (lines 12–26)

```
def upgrade() -> None
```

**Purpose**: Creates the `report_digest_entry` table when this migration is applied. This gives the report digest feature a permanent database home for saved digest entries.

**Data flow**: It starts with no application input; the migration runner simply calls it. It defines the table name, its columns, its two links to existing tables, and its combined primary key. The result is a new database table ready to store report digest records.

**Call relations**: During an upgrade, Alembic, the database migration tool, calls this function. The function hands the full table definition to `alembic.op.create_table`, using SQLAlchemy building blocks such as columns, text fields, UUID fields, JSON fields, date-time fields, foreign keys, and a primary key.

*Call graph*: 8 external calls (create_table, Column, DateTime, ForeignKeyConstraint, JSON, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 29–30)

```
def downgrade() -> None
```

**Purpose**: Removes the `report_digest_entry` table when this migration is rolled back. This is the undo step for the schema change made by `upgrade`.

**Data flow**: It receives no direct input from the application. When called, it tells the database migration tool to drop the `report_digest_entry` table. After it runs, the table and the digest data stored in it are gone.

**Call relations**: During a rollback, Alembic calls this function instead of `upgrade`. It passes the table name to `alembic.op.drop_table`, which performs the database change needed to reverse the migration.

*Call graph*: 1 external calls (drop_table).


### `extensions/report_digest/ufo_ext_report_digest/migrations/0002_report_digest_unchanged.py`

`data_model` · `database migration`

This file is part of the project’s database change history. A database migration is like a dated instruction card for reshaping the database safely as the software evolves. Here, the new shape is a table called `report_digest_unchanged`, used to remember that a specific writing or processing turn looked at reports in a workspace but found no changes to digest.

The table is deliberately small. It stores two identifiers: the workspace involved and the turn involved. Together, those two values form the table’s primary key, meaning the same workspace-and-turn pair can only be recorded once. Each identifier also points back to an existing table: `workspace` and `turn`. Those links are foreign keys, which means the database checks that the referenced workspace and turn really exist. If either the workspace or turn is deleted, the matching row in this table is automatically deleted too, because of `ondelete="CASCADE"`. In everyday terms, it is like removing a folder and having its index cards thrown away with it.

Without this migration, the report digest feature would have no dedicated place to record “I checked, and there was no change” events. That distinction matters because “nothing changed” is still useful information: it can prevent repeated unnecessary work and make later reporting more accurate.

#### Function details

##### `upgrade`  (lines 12–20)

```
def upgrade() -> None
```

**Purpose**: This function applies the migration by creating the `report_digest_unchanged` table. It is used when the database is being moved forward to the newer version of the report digest schema.

**Data flow**: It starts with an existing database that does not yet have this table. It tells Alembic, the database migration tool, to create a table with `workspace_id` and `turn_id` UUID columns, links those columns to the existing `workspace` and `turn` tables, and makes the pair of IDs unique as the table’s primary key. After it runs, the database can store records saying that a particular workspace and turn had no report changes.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function hands the table definition to `alembic.op.create_table`, using SQLAlchemy building blocks to describe the columns, foreign key rules, and primary key rule.

*Call graph*: 5 external calls (create_table, Column, ForeignKeyConstraint, PrimaryKeyConstraint, Uuid).


##### `downgrade`  (lines 23–24)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration by deleting the `report_digest_unchanged` table. It is used if the database needs to be rolled back to the previous schema version.

**Data flow**: It starts with a database that includes the `report_digest_unchanged` table. It tells Alembic to drop that table entirely. After it runs, the database no longer has a place to store these “unchanged report digest” records, and any data in that table is gone.

**Call relations**: Alembic calls this function when rolling back this migration. The function delegates the actual database change to `alembic.op.drop_table`, which removes the table created by `upgrade`.

*Call graph*: 1 external calls (drop_table).


### Scheduled Pause Storage
Creates durable storage for scheduled pauses so delayed work can resume later.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration / install or upgrade`

This is a database migration, which means it is a small recipe for changing the database shape when the application is installed or upgraded. Its job is to add a new table named `pause`. Think of this table like a waiting room for conversations or agents that should be picked up again at a specific time.

Each pause record stores who and what it belongs to: the workspace, conversation, agent, and possibly the member who created it. It also stores when the pause should resume, the prompt and user-facing description to use later, and ordering numbers that help resume at the right point in the conversation. The `claimed_by` and `claim_expires_at` fields support safe background processing: a worker can temporarily “claim” a pause so two workers do not resume the same thing at once.

The migration also adds database rules that keep links valid. For example, if a workspace, conversation, or agent is deleted, its pauses are deleted too. If the creating member is deleted, the pause remains but that member reference is cleared. An index on `resume_at` helps the system quickly find pauses that are due to run.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Creates the `pause` table and the lookup index needed by the scheduled tasks extension. This is used when moving the database forward to a version that supports stored pauses.

**Data flow**: It starts with an existing database that does not yet have this table. It asks Alembic, the database migration tool, to create columns for pause identity, ownership, resume timing, text content, worker claiming, and timestamps. It also adds relationship rules to other tables and creates an index so due pauses can be found quickly. After it runs, the database can permanently store and query scheduled pause records.

**Call relations**: This function is called by the migration system during an upgrade. It hands the actual database work to Alembic operations such as creating the table and index, while SQLAlchemy objects describe the column types and constraints in a database-neutral way.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used if the migration is rolled back to a version before scheduled pauses existed.

**Data flow**: It starts with a database that contains the `pause` table and its `pause_due` index. It first removes the index, then removes the table. After it runs, stored pause records and the schema for them are gone.

**Call relations**: This function is called by the migration system during a rollback. It uses Alembic’s drop operations to undo the earlier upgrade in the safe order: remove the index first, then the table it belongs to.

*Call graph*: 2 external calls (drop_index, drop_table).
