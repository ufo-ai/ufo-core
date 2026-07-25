# Core runtime fleet, scheduling, and job schema  `stage-1.6`

This stage is behind-the-scenes groundwork for running the system at scale. It is made of database migrations, which are ordered changes to the database layout. Together they give workers a reliable “control panel” for knowing what is running, what should run later, and how to find work quickly.

The runtime-instance migrations add and refine records for live runtime processes. First, each runtime can be tied to a workspace and report when it started and last checked in. Later, shared fleet runtimes are allowed to exist without a workspace, and old dedicated-runtime columns are removed to match the newer shared-fleet model.

The scheduled-task migrations add a place to store jobs that should run in the future or repeat, plus safe claiming rules so two workers do not grab the same task. They also record the last turn that triggered a scheduled task. The scheduled-pause and scheduled-admission migrations let the system mark pauses and admit turns that came from the scheduler. Finally, job-candidate indexes act like a book index, helping background sweeps find eligible rows without reading everything.

## Files in this stage

### Runtime and task foundations
Foundational migrations add tables for tracking runtime instances and storing scheduled tasks.

### `core/src/ufo/schema/migrations/versions/0015_runtime_instance.py`

`data_model` · `database migration during upgrade or rollback`

This migration changes the database structure. Its job is to create a new place to store information about live runtime instances, which are running copies of some runtime process connected to a workspace. Think of it like adding a sign-in sheet for active workers: each worker has an ID, says which workspace it belongs to, records when it started, and keeps updating a “last seen” time so the system can tell whether it is still alive.

The new table is called `runtime_instance`. Each row stores a unique ID, a workspace ID, start time, latest heartbeat time, a fingerprint string that can identify the instance, and normal created/updated timestamps. The workspace ID is linked back to the existing `workspace` table, so the database can reject runtime records for workspaces that do not exist.

The migration also adds an index named `runtime_instance_live` on workspace ID and heartbeat time. An index is like a sorted lookup card in a library: it helps the database quickly find recent runtime instances for a workspace, which is likely important when checking what is currently alive.

If this migration is rolled back, it removes the index first and then removes the table, undoing the database change cleanly.

#### Function details

##### `upgrade`  (lines 12–25)

```
def upgrade() -> None
```

**Purpose**: Adds the `runtime_instance` table and a lookup index to the database. This is used when moving the database forward to a version that can track active runtime processes.

**Data flow**: It takes no direct input from application code. When the migration tool runs it, it tells the database to create columns for IDs, workspace links, timestamps, and a fingerprint, then adds a foreign-key link to `workspace.id` and a primary key on `id`. After the table exists, it creates an index so searches by workspace and heartbeat time are faster.

**Call relations**: The migration runner calls this function when applying revision `0015`. Inside, it hands the actual database changes to Alembic operations such as creating the table and index, while SQLAlchemy column and constraint objects describe what the new table should look like.

*Call graph*: 8 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, Uuid).


##### `downgrade`  (lines 28–30)

```
def downgrade() -> None
```

**Purpose**: Removes the database objects created by `upgrade`. This is used if the database must be rolled back to the previous schema version.

**Data flow**: It takes no direct input from application code. When run, it first removes the `runtime_instance_live` index, then deletes the `runtime_instance` table. Afterward, the database no longer has storage for runtime instance records.

**Call relations**: The migration runner calls this function when rolling back from revision `0015` to `0014`. It uses Alembic’s drop operations in the reverse order of creation so the database can remove the schema safely.

*Call graph*: 2 external calls (drop_index, drop_table).


### `core/src/ufo/schema/migrations/versions/0017_scheduled_task.py`

`data_model` · `database migration during setup or upgrade`

This migration creates a new database table called `scheduled_task`. A database migration is a step-by-step change to the database structure, like adding a new shelf and labels to a filing cabinet. Without this file, the application would have no standard place to store scheduled work, such as what should run, when it should run next, and which agent or conversation it belongs to.

The table stores the task’s identity, the workspace it belongs to, the conversation and agent it is tied to, its name, schedule, prompt, and description. It also records timing information: when the task should next run, when it last ran, when it was created, and when it was updated. The `claimed_by` and `claim_expires_at` fields let a worker temporarily reserve a task, which helps prevent two workers from running the same scheduled task at the same time.

The migration also links each scheduled task to existing `workspace`, `conversation`, and `agent` records using foreign keys, which are database rules that keep references valid. It enforces that task names are unique within a workspace, and it adds an index on `next_run_at` so the system can quickly find tasks that are due to run.

#### Function details

##### `upgrade`  (lines 12–35)

```
def upgrade() -> None
```

**Purpose**: Adds the new `scheduled_task` table and an index for finding tasks by their next run time. This is used when moving the database forward to support scheduled tasks.

**Data flow**: Before this runs, the database has no `scheduled_task` table. The function declares the table’s columns, links to existing workspace, conversation, and agent tables, a primary key, a uniqueness rule for task names inside a workspace, and an index on `next_run_at`. After it runs, the database can store scheduled tasks and search efficiently for tasks that are ready to run.

**Call relations**: Alembic, the database migration tool, calls this function when applying revision `0017`. Inside it, the function hands the table and index definitions to Alembic operations, which translate them into database changes.

*Call graph*: 9 external calls (create_index, create_table, Column, DateTime, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid).


##### `downgrade`  (lines 38–40)

```
def downgrade() -> None
```

**Purpose**: Removes the scheduled task database changes. This is used if the migration must be rolled back.

**Data flow**: Before this runs, the database contains the `scheduled_task` table and its due-time index. The function first removes the index, then removes the table itself. After it runs, the database no longer has the scheduled task storage added by this migration.

**Call relations**: Alembic calls this function when rolling revision `0017` backward. It uses Alembic’s drop operations to undo the work done by `upgrade` in the safe order: remove the index first, then the table.

*Call graph*: 2 external calls (drop_index, drop_table).


### Job sweep indexes
Indexes are added to keep background job-candidate searches efficient as tables grow.

### `core/src/ufo/schema/migrations/versions/0027_job_candidate_indexes.py`

`data_model` · `database migration during upgrade or rollback`

This migration is like adding labels to filing cabinets. The data is already there, but the database needs shortcuts so it can jump directly to the records the system often asks for. The file adds several indexes, which are database lookup aids, to support recurring searches around turns, conversations, sandboxes, and extension storage. Two of the indexes are partial indexes: they only cover rows matching a condition, such as turns whose status is parked or conversations that have a sandbox handle. That keeps the shortcut smaller and more focused than indexing every row. The migration also includes a matching rollback path. If the system needs to undo this database version, it removes the same indexes in reverse order. This file does not change application behavior directly. Its job is to make existing queries stay fast and predictable, especially during sweep-style work where the system repeatedly looks for candidate jobs or active conversations.

#### Function details

##### `upgrade`  (lines 12–29)

```
def upgrade() -> None
```

**Purpose**: Adds database indexes that make common lookups faster. Someone would use this when moving the database schema forward to version 0027.

**Data flow**: It takes no direct input from the application. It reads the migration instructions written in the function, asks Alembic, the database-migration tool, to create indexes on selected table columns, and uses SQL text conditions for the partial indexes. The result is a database with new lookup shortcuts on the turn, conversation, and ext_store tables.

**Call relations**: The migration runner calls this function when applying revision 0027. Inside, it hands each index request to Alembic's create_index operation, and for conditional indexes it also passes SQLAlchemy text expressions so the database knows which rows belong in the index.

*Call graph*: 2 external calls (create_index, text).


##### `downgrade`  (lines 32–37)

```
def downgrade() -> None
```

**Purpose**: Removes the indexes added by this migration. Someone would use this when rolling the database schema back from version 0027 to the previous version.

**Data flow**: It takes no direct input from the application. It tells Alembic to drop each named index from the relevant table. The result is that the database no longer has these added lookup shortcuts, returning it to the earlier schema shape.

**Call relations**: The migration runner calls this function during rollback. It hands each removal to Alembic's drop_index operation, undoing the work of upgrade in reverse order.

*Call graph*: 1 external calls (drop_index).


### Fleet runtime support
Runtime-instance schema is loosened so shared fleet processes can be represented without workspace ownership.

### `core/src/ufo/schema/migrations/versions/0028_runtime_instance_fleet.py`

`data_model` · `database migration during deploy or upgrade`

This file is one step in the project’s database history. It changes the `runtime_instance` table so that the `workspace_id` field is allowed to be empty, also called `NULL` in database terms. In plain language, a runtime instance used to always need a workspace label. This migration makes room for a different kind of runtime instance: a shared fleet process that serves more broadly and is not owned by one workspace.

The comment at the top explains why this matters. The executor-recovery sweep, which is a cleanup or health-check pass that looks for live runtime seats, needs to see all runtime instances, including shared fleet ones. If the database forced every row to have a workspace ID, these fleet rows could not be recorded correctly.

The file also includes the reverse change. If the system is rolled back to the previous database version, `workspace_id` becomes required again. Alembic, the migration tool, runs `upgrade` when moving forward and `downgrade` when moving backward. The table alteration is done through Alembic’s batch mode, which is a safer way to change table structure across different database engines.

#### Function details

##### `upgrade`  (lines 13–15)

```
def upgrade() -> None
```

**Purpose**: This moves the database schema forward by allowing `runtime_instance.workspace_id` to be empty. It is used when installing or upgrading to this version so shared fleet runtime instances can be stored without pretending they belong to a workspace.

**Data flow**: It reads no application data. It opens a controlled table-change operation for the `runtime_instance` table, identifies `workspace_id` as a UUID column, and changes that column from required to optional. The result is a database table that accepts rows where `workspace_id` is missing.

**Call relations**: Alembic calls this function when applying revision `0028`. Inside it, the function asks Alembic to alter the `runtime_instance` table and uses SQLAlchemy’s UUID type description so the migration tool knows what kind of column it is changing.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


##### `downgrade`  (lines 18–20)

```
def downgrade() -> None
```

**Purpose**: This reverses the migration by making `runtime_instance.workspace_id` required again. It is used only if the database is rolled back to the previous revision.

**Data flow**: It reads no application data. It opens a controlled table-change operation for the `runtime_instance` table, identifies `workspace_id` as a UUID column, and changes that column from optional back to required. After this, the database will reject runtime instance rows that do not have a workspace ID.

**Call relations**: Alembic calls this function when rolling back revision `0028`. Like the forward migration, it hands the table change to Alembic’s batch alteration helper and uses SQLAlchemy’s UUID type description to describe the column being restored.

*Call graph*: 2 external calls (batch_alter_table, Uuid).


### Scheduled turn metadata
Scheduling migrations add pause tracking, admit turns from scheduled sources, and remember the last turn fired by a scheduled task.

### `core/src/ufo/schema/migrations/versions/0029_scheduled_pause.py`

`config` · `schema migration during deploy or rollback`

This migration changes two database tables, `turn` and `scheduled_task`, so the application can better represent pause-and-resume behavior. A database migration is like a renovation plan for a house: it says exactly what walls, doors, or labels must change so the software and the stored data still match.

On upgrade, it renames `turn.resume_enqueued_at` to `dispatch_enqueued_at`, which makes the column name more general. It also adds `admission_source` to each turn, with a default value of `internal`, and adds a rule that the value must be either `member` or `internal`. That rule protects the database from unclear or misspelled sources.

It then adds two optional fields to `scheduled_task`: `origin_seq`, which can remember the sequence position that caused the task, and `resume_turn_id`, which can point back to the turn being resumed. Finally, it creates a unique index for one-time scheduled tasks using `workspace_id` and `conversation_id`, but only where the schedule is `@once`. In plain terms, that prevents duplicate one-time pause tasks for the same conversation in the same workspace.

The downgrade reverses these changes, allowing the database to be rolled back to the previous version if needed.

#### Function details

##### `upgrade`  (lines 12–30)

```
def upgrade() -> None
```

**Purpose**: Applies the forward database change for revision 0029. It prepares the database to store scheduled pause information and to distinguish whether a turn was admitted by a member or internally by the system.

**Data flow**: It starts with the existing `turn` and `scheduled_task` tables. It renames one `turn` column, adds a required `admission_source` column with a safe default, adds a check rule limiting that value to known choices, adds two optional columns to `scheduled_task`, and creates a filtered unique index for one-time scheduled tasks. After it runs, the database has the new structure expected by newer application code.

**Call relations**: Alembic, the database migration tool, calls this function when moving the database from revision 0028 to 0029. Inside it, the function hands each table change to Alembic operations such as altering a table, adding columns, and creating an index, while SQLAlchemy supplies the column types and SQL text used in those operations.

*Call graph*: 8 external calls (add_column, batch_alter_table, create_index, Column, Integer, Text, Uuid, text).


##### `downgrade`  (lines 33–40)

```
def downgrade() -> None
```

**Purpose**: Reverses the database changes made by `upgrade`. It is used when the system needs to roll the database back from revision 0029 to revision 0028.

**Data flow**: It starts with the newer database shape. It removes the unique index for scheduled pauses, deletes the two added `scheduled_task` columns, removes the `admission_source` rule and column from `turn`, and renames `dispatch_enqueued_at` back to `resume_enqueued_at`. After it runs, the database matches the older schema version again.

**Call relations**: Alembic calls this function during a rollback. It uses Alembic operations to undo the same kinds of changes that `upgrade` applied, in reverse order, so dependent objects like indexes and constraints are removed before the columns they refer to disappear.

*Call graph*: 3 external calls (batch_alter_table, drop_column, drop_index).


### `core/src/ufo/schema/migrations/versions/0031_scheduled_admission.py`

`config` · `database schema migration`

This file is a small database change script, used when the application schema moves from version 0030 to 0031 or back again. The database has a table named turn, and one of its columns, admission_source, is protected by a check constraint. A check constraint is a database rule that rejects values outside an allowed list, like a form field that only accepts certain choices.

Before this migration, admission_source could only be “member” or “internal”. This file expands that rule so the database also accepts “scheduled”. Without this migration, application code that tries to store a scheduled turn would fail at the database level, even if the rest of the program understood the new idea.

The upgrade path removes the old rule and creates a new one with the extra allowed value. The downgrade path does the reverse, but first it rewrites any existing “scheduled” values to “internal”. That step matters because the old rule cannot be restored while rows still contain a value it does not allow. In everyday terms, it updates the guest list before putting the old doorman back on duty.

#### Function details

##### `upgrade`  (lines 11–16)

```
def upgrade() -> None
```

**Purpose**: This function applies the forward database change for version 0031. It lets the turn table store “scheduled” as a valid admission_source value.

**Data flow**: It reads no application data directly. It opens a safe table-alteration context for the turn table, removes the old database rule named turn_admission_source, then creates a replacement rule that accepts “member”, “internal”, or “scheduled”. The result is a changed database schema that allows the new value.

**Call relations**: The migration runner calls this when moving the database up from revision 0030 to 0031. Inside it, Alembic’s table-alteration helper is used to make the constraint change in a database-friendly way.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 19–25)

```
def downgrade() -> None
```

**Purpose**: This function reverses the migration so the database again only allows “member” and “internal” admission sources. It also cleans up existing “scheduled” rows first so the older rule can be restored safely.

**Data flow**: It first sends a SQL update to the database that changes every turn with admission_source set to “scheduled” into “internal”. Then it opens a table-alteration context for the turn table, removes the newer rule, and creates the older rule that only allows “member” and “internal”. The output is a database schema and data state compatible with revision 0030.

**Call relations**: The migration runner calls this when rolling the database back from revision 0031 to 0030. It uses Alembic’s direct SQL execution for the data cleanup, then Alembic’s table-alteration helper to replace the check constraint.

*Call graph*: 2 external calls (batch_alter_table, execute).


### `core/src/ufo/schema/migrations/versions/0037_scheduled_last_turn.py`

`data_model` · `schema migration`

This migration changes the shape of the database table that stores scheduled tasks. A scheduled task is something the system plans to run later or repeatedly. The new column, `last_turn_id`, gives each task a place to remember which “turn” it last ran on. In plain terms, it is like adding a “last done on step X” note to a recurring reminder.

The file uses Alembic, a tool for applying database changes in order. The `revision` value says this is migration 0037, and `down_revision` says it follows migration 0036. That lets the system apply schema changes in the right sequence.

When upgrading, the migration adds `last_turn_id` to the `scheduled_task` table. The column is a UUID, which is a long unique identifier often used to point to another record. It is nullable, meaning old or not-yet-run tasks do not need to have a value immediately. When downgrading, the migration removes the same column. Without this file, newer code that expects scheduled tasks to record their last firing turn would not have a database field to store that information.

#### Function details

##### `upgrade`  (lines 12–13)

```
def upgrade() -> None
```

**Purpose**: Adds the `last_turn_id` column to the `scheduled_task` database table. This prepares the database so scheduled tasks can remember the most recent turn in which they fired.

**Data flow**: The function takes no direct input. It tells Alembic to alter the `scheduled_task` table by adding a nullable UUID column named `last_turn_id`. After it runs, the database table has one extra field available for future reads and writes.

**Call relations**: Alembic calls this function when applying migration 0037 during a database upgrade. Inside it, the function creates the column definition with SQLAlchemy and hands it to Alembic’s `add_column` operation, which performs the actual database change.

*Call graph*: 3 external calls (add_column, Column, Uuid).


##### `downgrade`  (lines 16–17)

```
def downgrade() -> None
```

**Purpose**: Removes the `last_turn_id` column from the `scheduled_task` table. This is used when rolling the database schema back to the previous version.

**Data flow**: The function takes no direct input. It tells Alembic to drop the `last_turn_id` column from `scheduled_task`. After it runs, the table returns to the shape it had before this migration, and any stored values in that column are lost.

**Call relations**: Alembic calls this function when reversing migration 0037. It hands the table and column name to Alembic’s `drop_column` operation, which carries out the rollback change in the database.

*Call graph*: 1 external calls (drop_column).


### Shared fleet cleanup
Obsolete dedicated-runtime columns are removed to align the schema with the shared-fleet runtime model.

### `core/src/ufo/schema/migrations/versions/0046_shared_fleet_columns.py`

`data_model` · `schema migration`

This file is a database migration, which is a small, ordered recipe for changing the shape of the database over time. Its job is to remove fields that the current system no longer reads or needs. The project now runs only in a shared-fleet mode, so older dedicated-mode details are dead weight.

The migration removes `approved_by` from the `proposal` table. In the old design, this stored which member approved a proposal. Now proposal promotion is represented by `status`, so keeping a separate approver column would add confusion without being used. It also removes `fingerprint` and `started_at` from the `runtime_instance` table. These were tied to older runtime boot and liveness checks, but the remaining live system does not read them.

The file also includes a reverse recipe, called a downgrade. That matters because migrations are meant to be reversible when possible. If someone rolls the database back to the previous schema version, the removed columns are added back, including sensible defaults and the foreign-key link from `proposal.approved_by` to the `member` table. Think of this file like a renovation note: upgrade says which unused cupboards to tear out, downgrade says how to rebuild them if you undo the renovation.

#### Function details

##### `upgrade`  (lines 18–23)

```
def upgrade() -> None
```

**Purpose**: Applies this migration by removing database columns that are no longer used by the shared-fleet runtime. Someone runs this when moving the database forward from revision `0045` to `0046`.

**Data flow**: It takes no direct input from the caller, but it operates on the current database through Alembic, the migration tool. It opens safe table-alteration blocks for `proposal` and `runtime_instance`, drops `approved_by` from `proposal`, and drops `fingerprint` and `started_at` from `runtime_instance`. The result is an updated database schema with those old fields gone.

**Call relations**: Alembic calls this function when applying revision `0046`. Inside, it uses `alembic.op.batch_alter_table` so the table changes are grouped in a way Alembic can run safely across supported databases.

*Call graph*: 1 external calls (batch_alter_table).


##### `downgrade`  (lines 26–39)

```
def downgrade() -> None
```

**Purpose**: Reverses this migration by adding the removed columns back. Someone would use it only when rolling the database schema back to the previous revision.

**Data flow**: It takes no direct input, but reads and changes the database schema through Alembic. It adds `started_at` back to `runtime_instance` as a timezone-aware date and time with a default of the current time, adds `fingerprint` back as required text with an empty-string default, then adds nullable `approved_by` back to `proposal` and restores its link to the `member` table. The result is a database shaped like it was before this migration ran.

**Call relations**: Alembic calls this function during a rollback from revision `0046` to `0045`. It uses Alembic table-alteration blocks and SQLAlchemy column types to describe exactly what should be rebuilt, then hands those instructions to the migration framework to execute.

*Call graph*: 5 external calls (batch_alter_table, Column, DateTime, Text, Uuid).
