# Automation, objectives, monitors, and brief migrations  `stage-2.2.5`

This stage is behind-the-scenes groundwork for features that need to remember work over time. It is mostly made of database migrations, which are small setup scripts that create or change storage tables so the running system has reliable places to save state.

The monitor migration builds the table for scheduled checks, including which workspace, conversation, and agent own a monitor, plus its status, counters, schedule, and history. The objectives migrations create storage for goals, ordered steps, proof that steps were done or blocked, and later reviews of those steps. A later objectives migration adds a simple “can run independently” flag, so the system can decide which steps may happen in parallel without rechecking that every time. The scheduled tasks migration creates a pause table, letting a conversation go quiet and resume at a planned time. The Sweep migration stores each person’s daily brief and whether it is pending, failed, or complete. Together these tables act like labeled shelves for long-running automation.

## Files in this stage

### Monitor storage
Defines durable state for scheduled monitor checks, ownership, counters, and history.

### `extensions/monitors/ufo_ext_monitors/migrations/0001_monitor.py`

`data_model` · `database migration during setup or upgrade`

This is a database migration, which is a small script that changes the shape of the database in a controlled way. It belongs to Alembic, a tool that applies database changes step by step so every installation can reach the same structure.

The main job here is to add a new table called `monitor`. Each row represents one monitor. The table stores who and what the monitor belongs to, such as its workspace, conversation, agent, and optional creating member. It also stores human-facing details like the monitor name, audience, command, reason, next steps, and user description. Alongside that, it keeps scheduling and runtime state: how often the monitor should run, when it is due next, when its deadline is, how many probes have run, and whether recent runs were quiet, failed, or skipped.

The migration also adds safety rules. Foreign keys connect monitor rows to existing workspace, conversation, agent, and member rows, so the database can keep relationships valid. A unique rule prevents two monitors in the same workspace from having the same name. A check rule requires the interval to be at least one minute. An index on next probe time and deadline acts like a calendar shortcut, helping the system quickly find monitors that are due to run.

The matching downgrade removes the index and table, undoing this migration if the system is rolled back.

#### Function details

##### `upgrade`  (lines 12–48)

```
def upgrade() -> None
```

**Purpose**: Creates the `monitor` table and the index needed to quickly find monitors that are due to run. This is used when the monitors extension is installed or upgraded to this schema version.

**Data flow**: It takes no application input. When Alembic runs it, it sends table-building instructions to the database: column names and types, required fields, relationship rules, default counter values, uniqueness rules, and a minimum interval rule. After it finishes, the database has a new `monitor` table plus a `monitor_due` index for efficient scheduling lookups.

**Call relations**: Alembic calls this function while moving the database forward to revision `monitors_0001`. Inside, it hands the work to Alembic operations like `create_table` and `create_index`, and uses SQLAlchemy objects to describe the columns and constraints in a database-independent way.

*Call graph*: 13 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+3 more)).


##### `downgrade`  (lines 51–53)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used if the migration must be rolled back.

**Data flow**: It takes no application input. When Alembic runs it, it first removes the `monitor_due` index and then removes the whole `monitor` table. After it finishes, the database no longer has the schema storage for monitors from this migration.

**Call relations**: Alembic calls this function when moving the database backward from revision `monitors_0001`. It delegates the actual database changes to Alembic operations `drop_index` and `drop_table`, reversing the setup done by `upgrade`.

*Call graph*: 2 external calls (drop_index, drop_table).


### Objective storage
Creates the objectives schema and then extends objective steps with fanout metadata for parallel execution decisions.

### `extensions/objectives/migrations/objectives_0001_objective.py`

`data_model` · `database migration during install or upgrade`

This is a database migration: a small script that changes the shape of the database when the application is installed or upgraded. Without it, the objectives feature would have no permanent storage, so objectives and their progress could not be saved or queried later.

The migration builds four related tables. The main table, `objective`, stores a named goal inside a workspace and conversation. Each objective can have many `objective_step` rows, which are the ordered pieces of work needed to complete that goal. Each step can then collect two kinds of history: `objective_event`, which records that a step was done or blocked, and `objective_check`, which stores check results about that step.

The tables are linked with foreign keys, which are database rules saying “this row must point to a real row over there.” For example, a step must belong to an existing objective. The `ondelete="CASCADE"` rules mean that if a workspace, conversation, objective, or step is deleted, its dependent records are automatically removed too, like clearing all notes attached to a deleted folder. Indexes are added for common lookups, such as finding objectives in a conversation or events for a step in time order.

#### Function details

##### `upgrade`  (lines 12–69)

```
def upgrade() -> None
```

**Purpose**: Creates the database structure needed by the objectives feature. It adds tables for objectives, steps, step events, and step checks, plus rules and indexes that keep the data connected and quick to look up.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it sends table, column, constraint, and index definitions to the database. After it finishes, the database has four new tables with relationships, uniqueness rules, allowed event kinds, and lookup indexes.

**Call relations**: This is called by Alembic, the database migration tool, when moving the database forward to this revision. Inside the function, it hands each table and index definition to Alembic operations, which then translate those definitions into database changes.

*Call graph*: 12 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+2 more)).


##### `downgrade`  (lines 72–79)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by removing the objectives tables and their indexes. It is used when rolling the database back to a state before the objectives feature schema existed.

**Data flow**: It takes no direct input from application code. When run, it tells the database to drop the indexes first where needed, then remove the tables in an order that respects their dependencies. After it finishes, the objective-related storage created by `upgrade` is gone.

**Call relations**: This is called by Alembic when moving the database backward from this revision. It uses Alembic drop operations to undo the schema changes made by `upgrade`, starting with dependent tables before removing the main objective table.

*Call graph*: 2 external calls (drop_index, drop_table).


### `extensions/objectives/migrations/objectives_0002_step_fanout.py`

`data_model` · `database migration`

This file changes the shape of the database table that stores steps in an objective plan. A plan may list steps in an order, but a list alone does not always tell us whether one step truly depends on the previous one. For example, buying paint and buying brushes may appear one after the other, but they can happen independently. This migration adds an `independent` column to the `objective_step` table to record that distinction directly.

The file is an Alembic migration. Alembic is a tool that applies database changes in a controlled order, like a set of numbered renovation instructions for a building. The `revision` and `down_revision` values tell Alembic where this change fits in the migration history.

When moving forward, the migration adds the `independent` column as a required Boolean value, meaning it must always be either true or false. Existing rows are given a default value of false, because old data did not explicitly say any step was independent. When rolling back, the migration removes the column. Without this migration, later objective logic could not store the precomputed fan-out shape of a plan in the database.

#### Function details

##### `upgrade`  (lines 20–24)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change by adding the `independent` field to objective steps. This is used when upgrading the application schema to support marking steps that can run independently.

**Data flow**: It reads no application data directly. It tells Alembic to alter the `objective_step` table by adding a non-null Boolean column named `independent`, with a database default of false so existing rows remain valid. After it runs, every objective step row has this new field available.

**Call relations**: Alembic calls this function when applying this migration. Inside it, the function builds the new column definition with SQLAlchemy helpers and hands that definition to Alembic's `add_column` operation, which performs the actual database change.

*Call graph*: 4 external calls (add_column, Boolean, Column, false).


##### `downgrade`  (lines 27–28)

```
def downgrade() -> None
```

**Purpose**: Reverses the migration by removing the `independent` field from objective steps. This is used if the database schema must be rolled back to the previous version.

**Data flow**: It takes no input from application code. It tells Alembic to drop the `independent` column from the `objective_step` table. After it runs, the database no longer stores whether a step is independent.

**Call relations**: Alembic calls this function when rolling this migration backward. It hands off directly to Alembic's `drop_column` operation, which removes the column created by `upgrade`.

*Call graph*: 1 external calls (drop_column).


### Scheduled pause storage
Adds persistence for conversations that should pause and resume later.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/migrations/0001_pause.py`

`data_model` · `database migration / setup`

This migration teaches the database how to store a paused scheduled task. Without it, the scheduled-tasks extension would have nowhere durable to record that an agent should stop now and continue at a specific future time.

The table it creates is called `pause`. Each row is one pause request. It records which workspace, conversation, and agent the pause belongs to, when it should resume, what prompt should be used, and a user-facing description of why the pause exists. It also stores sequence numbers so the system can reconnect the pause to the exact point in the conversation stream. Think of this like putting a bookmark in a book, with a note saying when to come back and what to do next.

The table links back to existing tables such as workspace, conversation, agent, and member. These links are enforced by the database, so a pause cannot point at a missing workspace or conversation. If a workspace, conversation, or agent is deleted, its pauses are deleted too. If the member who created the pause is deleted, the pause remains but the creator field is cleared.

The migration also adds an index on `resume_at`, which helps the system quickly find pauses that are due to resume, instead of scanning every saved pause.

#### Function details

##### `upgrade`  (lines 12–36)

```
def upgrade() -> None
```

**Purpose**: Creates the new `pause` database table and adds a lookup shortcut for finding pauses by their resume time. This is used when the application is being moved forward to a version that supports scheduled pauses.

**Data flow**: The function takes no application data as input. It sends table and column definitions to Alembic, the database migration tool, which changes the database schema. After it runs, the database has a `pause` table with its required fields, relationships to other tables, uniqueness rule, and an index for due pauses.

**Call relations**: During an upgrade, Alembic calls this function as part of applying the scheduled-tasks migration branch. The function hands the actual database changes to Alembic and SQLAlchemy helpers, which translate the Python definitions into database operations.

*Call graph*: 10 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 39–41)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by this migration. This is used if the database must be rolled back to a version before scheduled pauses existed.

**Data flow**: The function takes no application data as input. It asks Alembic to remove the `pause_due` index first, then remove the `pause` table. After it runs, the database no longer has storage for pause records from this migration.

**Call relations**: During a rollback, Alembic calls this function to undo the migration. It reverses the work of `upgrade` in a safe order: remove the index that depends on the table, then remove the table itself.

*Call graph*: 2 external calls (drop_index, drop_table).


### Sweep brief storage
Creates durable tracking for each user's daily Sweep brief and its completion status.

### `extensions/sweep/ufo_ext_sweep/migrations/0001_sweep.py`

`data_model` · `database migration / setup`

This is a database migration: a small, versioned script that changes the shape of the database. The Sweep feature appears to produce daily brief editions for workspace members. To do that safely, it needs a durable place to record one edition per person per local day, including its status, retry attempt count, related conversation or turn, saved candidate data, and timestamps.

The main table created here is `sweep_edition`. Its primary key is the combination of workspace, member, and local date, which means the database itself prevents duplicate daily briefs for the same member on the same day. The table also links back to existing workspace, member, conversation, and turn records. Those links are foreign keys, which are database rules that keep references pointing at real rows. Some links are deleted automatically if their parent is deleted, while a deleted turn simply clears the turn reference.

The migration also limits `status` to three allowed words: `pending`, `failed`, or `completed`. This matters because later code can trust that it will not see random status values. Finally, it adds an index for looking up pending work by workspace, like adding a shortcut in a filing cabinet for the queue Sweep checks most often.

#### Function details

##### `upgrade`  (lines 12–38)

```
def upgrade() -> None
```

**Purpose**: Creates the `sweep_edition` table and its lookup index when this migration is applied. This gives the Sweep extension a permanent database record for each daily brief edition and its progress.

**Data flow**: Before this runs, the database has no `sweep_edition` table. The function asks Alembic, the database migration tool, to create a table with columns for ownership, date, status, related conversation data, saved candidate data, and timestamps. It also adds database rules for allowed statuses, links to other tables, uniqueness, and faster pending-edition lookup. After it runs, later application code can store and find Sweep daily brief records.

**Call relations**: This is called by Alembic during a forward migration. Inside it, the function hands the table definition to Alembic and SQLAlchemy, which translate these Python declarations into actual database changes.

*Call graph*: 11 external calls (create_index, create_table, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, JSON, PrimaryKeyConstraint, Text (+1 more)).


##### `downgrade`  (lines 41–42)

```
def downgrade() -> None
```

**Purpose**: Removes the `sweep_edition` table when this migration is rolled back. This is the undo path for the database change.

**Data flow**: Before this runs, the database may contain the Sweep edition table and its stored daily brief state. The function tells Alembic to drop that table. After it runs, the table and the data inside it are gone, returning the database to the shape it had before this migration.

**Call relations**: This is called by Alembic during a rollback. It delegates the actual removal to Alembic’s table-dropping operation.

*Call graph*: 1 external calls (drop_table).
